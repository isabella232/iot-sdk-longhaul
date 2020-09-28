# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import logging
import os
import time
import uuid
import json
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
import dps
import platform
import psutil
import queue
import app_base
from azure.iot.device import Message
import azure.iot.device.constant
from measurement import ThreadSafeCounter
from azure_monitor import enable_tracing, get_event_logger, log_to_azure_monitor, DependencyTracer


logger = logging.getLogger("thief.{}".format(__name__))
event_logger = get_event_logger("device")

logging.basicConfig(level=logging.WARNING)
# logging.getLogger("paho").setLevel(level=logging.DEBUG)
logging.getLogger("thief").setLevel(level=logging.INFO)


provisioning_host = os.environ["THIEF_DEVICE_PROVISIONING_HOST"]
id_scope = os.environ["THIEF_DEVICE_ID_SCOPE"]
group_symmetric_key = os.environ["THIEF_DEVICE_GROUP_SYMMETRIC_KEY"]
registration_id = os.environ["THIEF_DEVICE_ID"]

log_to_azure_monitor("thief", "device")
log_to_azure_monitor("azure")
log_to_azure_monitor("paho")
enable_tracing("device")


class DeviceRunMetrics(object):
    """
    Object we use internally to keep track of how a the entire test is performing.
    """

    def __init__(self):
        self.run_start_utc = None
        self.run_end_utc = None
        self.run_time = None
        self.run_state = app_base.WAITING
        self.exit_reason = None

        self.heartbeats_sent = ThreadSafeCounter()
        self.heartbeats_received = ThreadSafeCounter()

        self.pingback_requests_sent = ThreadSafeCounter()
        self.pingback_responses_received = ThreadSafeCounter()

        self.d2c_in_flight = ThreadSafeCounter()
        self.d2c_sent = ThreadSafeCounter()
        self.d2c_acknowledged = ThreadSafeCounter()
        self.d2c_failed = ThreadSafeCounter()


class DeviceRunConfig(object):
    """
    Object we use internally to keep track of how the entire test is configured.
    Currently hardcoded. Later, this will come from desired properties.
    """

    def __init__(self):
        self.max_run_duration = 0

        self.heartbeat_interval = 10
        self.heartbeat_failure_interval = 30
        self.thief_property_update_interval = 60

        self.d2c_operations_per_second = 5
        self.d2c_timeout_interval = 60
        self.d2c_failures_allowed = 0


class DeviceApp(app_base.AppBase):
    """
    Main application object
    """

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=128)
        self.done = threading.Event()
        self.shutdown_event = threading.Event()
        self.client = None
        self.metrics = DeviceRunMetrics()
        self.config = DeviceRunConfig()
        # for pingbacks
        self.pingback_list_lock = threading.Lock()
        self.pingback_wait_list = {}
        self.received_pingback_list = []
        self.new_pingback_event = threading.Event()
        # for heartbeats
        self.next_heartbeat_id = 0
        self.last_heartbeat = time.time()
        self.first_heartbeat_received = threading.Event()
        # for telemetry
        self.telemetry_queue = queue.Queue()

    def get_fixed_system_metrics(self):
        return {
            "language": "python",
            "languageVersion": platform.python_version(),
            "sdkVersion": str(azure.iot.device.constant.VERSION),
            "sdkGithubRepo": os.getenv("THIEF_SDK_GIT_REPO"),
            "sdkGithubBranch": os.getenv("THIEF_SDK_GIT_BRANCH"),
            "sdkGithubCommit": os.getenv("THIEF_SDK_GIT_COMMIT"),
            "osType": platform.system(),
            "osRelease": platform.version(),
        }

    def get_variable_system_metrics(self):
        process = psutil.Process(os.getpid())
        return {
            "processResidentMemory": process.memory_info().rss / 1024,
            "processCpuUtilization": process.cpu_percent(),
        }

    def get_longhaul_metrics(self):
        self.metrics.run_time = (
            datetime.datetime.now(datetime.timezone.utc) - self.metrics.run_start_utc
        )

        props = {
            "runStartUtc": self.metrics.run_start_utc.isoformat(),
            "runEndUtc": self.metrics.run_end_utc.isoformat() if self.metrics.run_end_utc else None,
            "runTime": str(self.metrics.run_time),
            "runState": str(self.metrics.run_state),
            "exitReason": self.metrics.exit_reason,
            "heartbeats": {
                "sent": self.metrics.heartbeats_sent.get_count(),
                "received": self.metrics.heartbeats_received.get_count(),
            },
            "pingbacks": {
                "requestsSent": self.metrics.pingback_requests_sent.get_count(),
                "responsesReceived": self.metrics.pingback_responses_received.get_count(),
            },
            "d2c": {
                "queued": self.telemetry_queue.qsize(),
                "inFlight": self.metrics.d2c_in_flight.get_count(),
                "sent": self.metrics.d2c_sent.get_count(),
                "acknowledged": self.metrics.d2c_acknowledged.get_count(),
            },
        }
        return props

    def update_initial_reported_properties(self):
        """
        Update reported properties at the start of a run
        """
        props = {"thief": {"device": self.get_longhaul_metrics()}}
        props["thief"]["device"].update(self.get_fixed_system_metrics())
        props["thief"]["device"].update(self.get_variable_system_metrics())

        print("patching {}".format(props))
        self.client.patch_twin_reported_properties(props)

    def send_telemetry_thread(self):
        """
        Thread which reads the telemetry queue and sends the telemetry.  Since send_message is
        blocking, and we want to overlap send_messsage calls, we create multiple
        send_telemetry_therad instances so we can send multiple messages at the same time.

        The number of send_telemetry_thread instances is the number of overlapped sent operations
        we can have
        """
        while not self.done.isSet():
            try:
                msg, tracer = self.telemetry_queue.get(timeout=1)
            except queue.Empty:
                msg = None
            if msg:
                try:
                    self.metrics.d2c_in_flight.increment()
                    with tracer.span("thiefSendTelemetry"):
                        self.client.send_message(msg)
                except Exception as e:
                    # TODO: check failure count
                    # TODO: look at duration and flag
                    # TODO: add enqueued time somewhere
                    self.metrics.d2c_failed.increment()
                    logger.error(
                        "send_message raised {}".format(e),
                        exc_info=True,
                        extra=tracer.get_logging_tags(),
                    )
                    tracer.end_operation(e)
                else:
                    self.metrics.pingback_requests_sent.increment()
                    self.metrics.d2c_sent.increment()
                    tracer.end_operation()
                finally:
                    self.metrics.d2c_in_flight.decrement()

    def test_d2c_thread(self):
        """
        Thread to continuously send d2c messages throughout the longhaul run.  This thread doesn't
        actually send messages becauase send_message is blocking and we want to overlap our send
        operations.  Instead, this thread adds the messsage to a queue, and relies on a
        send_telemetry_thread instance to actually send the message.
        """

        # Don't start sending until we know that there's a service app on the other side.
        while not (self.first_heartbeat_received.isSet() or self.done.isSet()):
            self.first_heartbeat_received.wait(1)

        while not self.done.isSet():

            tracer = DependencyTracer("thiefSendTelemetryWithPingback")
            pingback_id = str(uuid.uuid4())

            props = {
                "thief": {
                    "cmd": "pingback",
                    "pingbackId": pingback_id,
                    "tracingCarrier": tracer.span_to_dict(),
                    "device": self.get_longhaul_metrics(),
                }
            }
            props["thief"]["device"].update(self.get_variable_system_metrics())

            msg = Message(json.dumps(props))
            msg.content_type = "application/json"
            msg.content_encoding = "utf-8"
            msg.custom_properties["eventDateTimeUtc"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()

            def on_pingback_received():
                # TODO: if the pingback is never received, this span is never persisted.  We should record failures.
                tracer.end_operation()
                self.metrics.d2c_acknowledged.increment()

            with self.pingback_list_lock:
                self.pingback_wait_list[pingback_id] = on_pingback_received

            # This function only queues the message.  A send_telemetry_thread instance will pick
            # it up and send it.
            self.telemetry_queue.put(msg, tracer)

            # sleep until we need to send again
            self.done.wait(1 / self.config.d2c.operations_per_second)

    def update_thief_properties_thread(self):
        """
        Thread which occasionally sends reported properties with information about how the
        test is progressing
        """
        done = False

        while not done:
            # setting this at the begining and checking at the end guarantees one last update
            # before the thread dies
            if self.done.isSet():
                done = True

            props = {"thief": {"device": self.get_longhaul_metrics()}}
            props["thief"]["device"].update(self.get_variable_system_metrics())

            logger.info("updating thief props: {}".format(props))
            self.client.patch_twin_reported_properties(props)

            self.done.wait(self.config.thief_property_update_interval)

    def dispatch_c2d_thread(self):
        """
        Thread which continuously receives c2d messages throughout the test run.  This
        thread does minimal processing for each c2d.  If anything complex needs to happen as a
        result of a c2d message, this thread puts the message into a queue for some other thread
        to pick up.
        """
        while not self.done.isSet():
            msg = self.client.receive_message(timeout=1)

            if msg:
                obj = json.loads(msg.data.decode())
                thief = obj.get("thief")
                cmd = thief.get("cmd") if thief else None
                if cmd == "heartbeat":
                    logger.info(
                        "heartbeat received.  heartbeatId={}".format(thief.get("heartbeatId"))
                    )
                    self.last_heartbeat = time.time()
                    self.metrics.heartbeats_received.increment()
                    if not self.first_heartbeat_received.isSet():
                        self.first_heartbeat_received.set()

                elif cmd == "pingbackResponse":
                    self.metrics.pingback_responses_received.increment()
                    list = thief.get("pingbackIds")
                    if list:
                        with self.pingback_list_lock:
                            for pingback_id in list:
                                self.received_pingback_list.append(pingback_id)
                        self.new_pingback_event.set()

    def handle_pingback_thread(self):
        """
        Thread which resolves the pingback_wait_list (messages waiting for pingback) with the
        received_pingback_list (pingbacks that we've received) so we can do pingback callbacks.
        We do this in our own thread because we don't wat to make c2d_dispatcher_thread wait
        while we do callbacks.
        """

        while not self.done.isSet():
            self.new_pingback_event.clear()
            callbacks = []

            with self.pingback_list_lock:
                new_list = []
                for pingback_id in self.received_pingback_list:
                    if pingback_id in self.pingback_wait_list:
                        # we've received a pingback.  Don't call back here because we're holding
                        # pingback_lock_list.  Instead, add to a list and call back when we're
                        # not holding the lock.
                        callbacks.append(self.pingback_wait_list[pingback_id])
                        del self.pingback_wait_list[pingback_id]
                    else:
                        # Not waiting for this pingback (yet?).  Keep it in our list.
                        new_list.append(pingback_id)
                self.received_pingback_list = new_list

            for callback in callbacks:
                callback()

            self.new_pingback_event.wait(timeout=1)

    def heartbeat_thread(self):
        """
        Thread which is responsible for sending heartbeat messages to the other side and
        also for making sure that heartbeat messages are received often enough
        """
        while not self.done.isSet():
            logger.info("sending heartbeat with id {}".format(self.next_heartbeat_id))
            msg = Message(
                json.dumps({"thief": {"cmd": "heartbeat", "heartbeatId": self.next_heartbeat_id}})
            )
            self.next_heartbeat_id += 1
            msg.content_type = "application/json"
            msg.content_encoding = "utf-8"
            print("sending {}".format(msg))
            self.client.send_message(msg)
            self.metrics.heartbeats_sent.increment()

            seconds_since_last_heartbeat = time.time() - self.last_heartbeat
            if seconds_since_last_heartbeat > self.config.heartbeat_failure_interval:
                raise Exception(
                    "No heartbeat received for {} seconds".format(seconds_since_last_heartbeat)
                )

            time.sleep(self.config.heartbeat_interval)

    def shutdown(self, error):
        """
        Shutdown the test.  This function can be called from any tread in order to trigger
        the shutdown of the test
        """
        if self.shutdown_event.isSet():
            logger.info("shutdown: event is already set.  ignorning")
        else:
            if error:
                self.metrics.run_state = app_base.FAILED
                logger.error("shutdown: triggering error shutdown", exc_info=error)
            else:
                self.metrics.run_state = app_base.COMPLETE
                logger.info("shutdown: trigering clean shutdown")
            self.metrics.exit_reason = str(error or "Clean shutdown")
            self.shutdown_event.set()

    def main(self):

        self.metrics.run_start_utc = datetime.datetime.now(datetime.timezone.utc)
        self.metrics.run_state = app_base.RUNNING

        # Create our client and push initial properties
        self.client = dps.create_device_client_using_dps_group_key(
            provisioning_host=provisioning_host,
            registration_id=registration_id,
            id_scope=id_scope,
            group_symmetric_key=group_symmetric_key,
        )
        self.update_initial_reported_properties()

        # Make a list of threads to launch
        threads_to_launch = [
            (self.heartbeat_thread, "heartbeat_thread"),
            (self.dispatch_c2d_thread, "c2d_dispatch_thread"),
            (self.update_thief_properties_thread, "update_thief_properties_thread"),
            (self.handle_pingback_thread, "handle_pingback_thread"),
            (self.test_d2c_thread, "test_d2c_thead"),
        ]
        for i in range(0, self.config.d2c_operations_per_second * 2):
            threads_to_launch.append(
                (self.send_telemetry_thread, "send_telemetry_thread #{}".format(i))
            )

        self.run_threads(threads_to_launch)

    def disconnect(self):
        self.client.disconnect()


if __name__ == "__main__":
    DeviceApp().main()
