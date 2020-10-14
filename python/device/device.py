# ( Copyright (c) Microsoft. All rights reserved.
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
import queue
import app_base
from azure.iot.device import Message
import azure.iot.device.constant
from measurement import ThreadSafeCounter
from azure_monitor import get_event_logger, log_to_azure_monitor


logger = logging.getLogger("thief.{}".format(__name__))
event_logger = get_event_logger("device")

logging.basicConfig(level=logging.INFO)
logging.getLogger("paho").setLevel(level=logging.DEBUG)
logging.getLogger("thief").setLevel(level=logging.DEBUG)


# use os.environ[] for required environment variables
provisioning_host = os.environ["THIEF_DEVICE_PROVISIONING_HOST"]
id_scope = os.environ["THIEF_DEVICE_ID_SCOPE"]
group_symmetric_key = os.environ["THIEF_DEVICE_GROUP_SYMMETRIC_KEY"]
registration_id = os.environ["THIEF_DEVICE_ID"]

# use os.getenv() for optional environment variables
run_id = os.getenv("THIEF_DEVICE_APP_RUN_ID")
requested_service_app_run_id = os.getenv("THIEF_REQUESTED_SERVICE_APP_RUN_ID")

# Amount of time to wait for a service app to "claim" this device
BOOTSTRAP_TIMEOUT_INTERVAL = 300
# Amount of time before resending bootstrap command
BOOTSTRAP_SEND_INTERVAL = 30

log_to_azure_monitor("thief", "device")
log_to_azure_monitor("azure")
log_to_azure_monitor("paho")

if not run_id:
    run_id = str(uuid.uuid4())


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

        self.thief_property_update_interval = 60
        self.watchdog_failure_interval = 300

        self.d2c_operations_per_second = 5
        self.d2c_slowness_threshold = 60
        self.d2c_failures_allowed = 10
        self.d2c_slow_operations_allowed = 100


class DeviceApp(app_base.AppBase):
    """
    Main application object
    """

    def __init__(self):
        super(DeviceApp, self).__init__()

        self.executor = ThreadPoolExecutor(max_workers=128)
        self.done = threading.Event()
        self.client = None
        self.metrics = DeviceRunMetrics()
        self.config = DeviceRunConfig()
        self.service_app_run_id = None
        # for pingbacks
        self.pingback_list_lock = threading.Lock()
        self.pingback_wait_list = {}
        self.received_pingback_list = []
        self.new_pingback_event = threading.Event()
        # for telemetry
        self.telemetry_queue = queue.Queue()

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
        props["thief"]["device"].update(
            self.get_fixed_system_metrics(azure.iot.device.constant.VERSION)
        )
        print("patching {}".format(props))
        self.client.patch_twin_reported_properties(props)

    def pair_with_service_instance(self):
        """
        "pair" with a service app. This is necessary because we can have a single
        service app responsible for multiple device apps.  The pairing process works
        like this:
        1. The device sets reported properties saying it doesn't have a service app
            and any preferernces it has about what service app resopnds.  (currently, it
            can specify the exact service app using the service app's run ID, but we may add
            more preferences later.)
        2. The device app sends a "bootstrap" d2c message.
        3. All service apps watch for all "bootstrap" messages.  If some service app instance
            wants to get paired with a device, it sets a desired property to indicate that it's
            available.  If multiple service apps set the property, the winner is chosen by the
            device app, probably based on whatever value happens to be in the desired property
            when the device app checks.
        4. Once the device decides on a service app, it sets it's serviceAppRunId reported
            property. It also sets the serviceAppRunId property in any telemetry messages it
            sends.

        Right now, there is no provision for a failing service app.  In the future, the device
        code _could_ see that the service app isn't responding and start the bootstrap process
        over again.
        """

        reported_properties = {
            "thief": {
                "device": {
                    "serviceAppRunId": None,
                    "requestedServiceAppRunId": requested_service_app_run_id,
                }
            }
        }
        logger.debug(
            "Attempting to pair with service instance.  Setting reported properties: {}".format(
                reported_properties
            )
        )
        self.client.patch_twin_reported_properties(reported_properties)

        logger.debug("sending bootstrap message")
        bootstrap_message = Message(json.dumps({"thief": {"cmd": "bootstrap"}}))
        bootstrap_message.content_type = "application/json"
        bootstrap_message.content_encoding = "utf-8"
        self.client.send_message(bootstrap_message)

        start_time = time.time()
        while time.time() - start_time < BOOTSTRAP_TIMEOUT_INTERVAL:
            logger.debug("Waiting for service app to claim this device")
            patch = self.client.receive_twin_desired_properties_patch(
                timeout=BOOTSTRAP_SEND_INTERVAL
            )

            if patch and "thief" in patch and "device" in patch["thief"]:
                logger.debug("Got patch")
                service_app_run_id = patch["thief"]["device"].get("serviceAppRunId", None)
                if service_app_run_id:
                    logger.debug(
                        "Service app {} claimed this device instance".format(service_app_run_id)
                    )
                    self.service_app_run_id = service_app_run_id
                    reported_properties["thief"]["device"]["serviceAppRunId"] = service_app_run_id
                    self.client.patch_twin_reported_properties(reported_properties)
                    return
                else:
                    logger.debug("patch received, but serviceAppRunId not set: {}".format(patch))

            logger.debug("Still no assigned service instance. Sending new bootstrap message")
            self.client.send_message(bootstrap_message)

        raise Exception("timeout waitig for service to set serviceAppRunId")

    def send_telemetry_thread(self, worker_thread_info):
        """
        Thread which reads the telemetry queue and sends the telemetry.  Since send_message is
        blocking, and we want to overlap send_messsage calls, we create multiple
        send_telemetry_therad instances so we can send multiple messages at the same time.

        The number of send_telemetry_thread instances is the number of overlapped sent operations
        we can have
        """
        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()
            try:
                msg = self.telemetry_queue.get(timeout=1)
            except queue.Empty:
                msg = None
            if msg:
                try:
                    self.metrics.d2c_in_flight.increment()
                    self.client.send_message(msg)
                except Exception as e:
                    # TODO: check failure count
                    # TODO: look at duration and flag
                    # TODO: add enqueued time somewhere
                    self.metrics.d2c_failed.increment()
                    logger.error("send_message raised {}".format(e), exc_info=True)
                else:
                    self.metrics.pingback_requests_sent.increment()
                    self.metrics.d2c_sent.increment()
                finally:
                    self.metrics.d2c_in_flight.decrement()

    def test_d2c_thread(self, worker_thread_info):
        """
        Thread to continuously send d2c messages throughout the longhaul run.  This thread doesn't
        actually send messages becauase send_message is blocking and we want to overlap our send
        operations.  Instead, this thread adds the messsage to a queue, and relies on a
        send_telemetry_thread instance to actually send the message.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()

            pingback_id = str(uuid.uuid4())

            props = {
                "thief": {
                    "cmd": "pingback",
                    "pingbackId": pingback_id,
                    "device": self.get_longhaul_metrics(),
                }
            }
            props["thief"]["device"].update(self.get_system_health_telemetry())

            # TODO: this is where we push metrics to Azure monitor

            msg = Message(json.dumps(props))
            msg.content_type = "application/json"
            msg.content_encoding = "utf-8"
            msg.custom_properties["eventDateTimeUtc"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            msg.custom_properties["serviceAppRunId"] = self.service_app_run_id

            def on_pingback_received(pingback_id):
                self.metrics.d2c_acknowledged.increment()

            with self.pingback_list_lock:
                self.pingback_wait_list[pingback_id] = (on_pingback_received, (pingback_id,))

            # This function only queues the message.  A send_telemetry_thread instance will pick
            # it up and send it.
            self.telemetry_queue.put(msg)

            # sleep until we need to send again
            self.done.wait(1 / self.config.d2c_operations_per_second)

    def update_thief_properties_thread(self, worker_thread_info):
        """
        Thread which occasionally sends reported properties with information about how the
        test is progressing
        """
        done = False

        while not done:
            worker_thread_info.watchdog_time = time.time()
            # setting this at the begining and checking at the end guarantees one last update
            # before the thread dies
            if self.done.isSet():
                done = True

            props = {"thief": {"device": self.get_longhaul_metrics()}}

            logger.info("updating thief props: {}".format(props))
            self.client.patch_twin_reported_properties(props)

            self.done.wait(self.config.thief_property_update_interval)

    def dispatch_c2d_thread(self, worker_thread_info):
        """
        Thread which continuously receives c2d messages throughout the test run.  This
        thread does minimal processing for each c2d.  If anything complex needs to happen as a
        result of a c2d message, this thread puts the message into a queue for some other thread
        to pick up.
        """
        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()
            msg = self.client.receive_message(timeout=1)

            if msg:
                obj = json.loads(msg.data.decode())
                thief = obj.get("thief")
                cmd = thief.get("cmd") if thief else None
                if cmd == "pingbackResponse":
                    self.metrics.pingback_responses_received.increment()
                    list = thief.get("pingbackIds")
                    if list:
                        with self.pingback_list_lock:
                            for pingback_id in list:
                                self.received_pingback_list.append(pingback_id)
                        self.new_pingback_event.set()
                else:
                    logger.warning("Unknown command received: {}".format(obj))

    def handle_pingback_thread(self, worker_thread_info):
        """
        Thread which resolves the pingback_wait_list (messages waiting for pingback) with the
        received_pingback_list (pingbacks that we've received) so we can do pingback callbacks.
        We do this in our own thread because we don't wat to make c2d_dispatcher_thread wait
        while we do callbacks.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()
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

            for (function, args) in callbacks:
                function(*args)

            self.new_pingback_event.wait(timeout=1)

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

        # pair with a service app instance
        self.pair_with_service_instance()

        # Make a list of threads to launch
        worker_thread_infos = [
            app_base.WorkerThreadInfo(self.dispatch_c2d_thread, "c2d_dispatch_thread"),
            app_base.WorkerThreadInfo(
                self.update_thief_properties_thread, "update_thief_properties_thread"
            ),
            app_base.WorkerThreadInfo(self.handle_pingback_thread, "handle_pingback_thread"),
            app_base.WorkerThreadInfo(self.test_d2c_thread, "test_d2c_thead"),
        ]
        for i in range(0, self.config.d2c_operations_per_second * 2):
            worker_thread_infos.append(
                app_base.WorkerThreadInfo(
                    self.send_telemetry_thread, "send_telemetry_thread #{}".format(i)
                )
            )

        self.run_threads(worker_thread_infos)

    def disconnect(self):
        self.client.disconnect()


if __name__ == "__main__":
    DeviceApp().main()
