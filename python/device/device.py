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
import collections
from concurrent.futures import ThreadPoolExecutor
import dps
import queue
import app_base
from azure.iot.device import Message
import azure.iot.device.constant
from measurement import ThreadSafeCounter
import azure_monitor
from azure_monitor_metrics import MetricsReporter

logging.basicConfig(level=logging.WARNING)
logging.getLogger("paho").setLevel(level=logging.INFO)
logging.getLogger("thief").setLevel(level=logging.INFO)
logging.getLogger("aure.iot").setLevel(level=logging.WARNING)

logger = logging.getLogger("thief.{}".format(__name__))

# use os.environ[] for required environment variables
provisioning_host = os.environ["THIEF_DEVICE_PROVISIONING_HOST"]
id_scope = os.environ["THIEF_DEVICE_ID_SCOPE"]
group_symmetric_key = os.environ["THIEF_DEVICE_GROUP_SYMMETRIC_KEY"]
registration_id = os.environ["THIEF_DEVICE_ID"]

# use os.getenv() for optional environment variables
run_id = os.getenv("THIEF_DEVICE_APP_RUN_ID")
requested_service_pool = os.getenv("THIEF_REQUESTED_SERVICE_POOL")

if not run_id:
    run_id = str(uuid.uuid4())

# configure our traces and events to go to Azure Monitor
azure_monitor.configure_logging(
    client_type="device", run_id=run_id, sdk_version=azure.iot.device.constant.VERSION,
)
event_logger = azure_monitor.get_event_logger()
azure_monitor.log_to_azure_monitor("thief")
azure_monitor.log_to_azure_monitor("azure")
azure_monitor.log_to_azure_monitor("paho")

MessageWaitingToArrive = collections.namedtuple(
    "MessageWaitingToArrive", "callback pingback_id message send_time"
)


class DeviceRunMetrics(object):
    """
    Object we use internally to keep track of how a the entire test is performing.
    """

    def __init__(self):
        self.run_start_utc = None
        self.run_end_utc = None
        self.run_time = 0
        self.run_state = app_base.WAITING
        self.exit_reason = None

        self.send_message_count_unacked = ThreadSafeCounter()
        self.send_message_count_sent = ThreadSafeCounter()
        self.send_message_count_received_by_service_app = ThreadSafeCounter()
        self.send_message_count_failures = ThreadSafeCounter()


class DeviceRunConfig(object):
    """
    Object we use internally to keep track of how the entire test is configured.
    Currently hardcoded. Later, this will come from desired properties.
    """

    # All durations are in seconds
    def __init__(self):
        # how long should the test run before finishing.  0 = forever
        self.max_run_duration = 0

        # How often do we update reported properties
        self.thief_property_update_interval = 60

        # How long can a thread go without updating its watchdog before failing.
        self.watchdog_failure_interval = 300

        # How long to keep trying to pair with a service instance before giving up.
        self.pairing_request_timeout_interval = 900

        # How many seconds to wait before sending a new pairingRequest message
        self.pairing_request_send_interval = 30

        # How many times to call send_message per second
        self.send_message_operations_per_second = 1

        # How many threads do we spin up for overlapped send_message calls.  These threads
        # pull messages off of a single outgoing queue.  If all of the send_message threads
        # are busy, outgoing messages will just pile up in the queue.
        self.send_message_thread_count = 10

        # How long do we wait for notification that the service app received a
        # message before we consider it a failure?
        self.send_message_arrival_failure_interval = 3600

        # How many messages fail to arrive at the service before we fail the test
        # This counts messages that have been sent and acked, but the service app hasn't reported receipt.
        self.send_message_arrival_failure_count = 10

        # How many messages to we allow in the send_message backlog before we fail the test.
        # This counts messages that gets backed up because send_message hasn't even been called yet.
        self.send_message_backlog_failure_count = 200

        # How many unack'ed messages do we allow before we fail the test?
        # This counts messages that either haven't been sent, or they've been sent but not ack'ed by the receiver
        self.send_message_unacked_failure_count = 200

        # How many send_message exceptions do we allow before we fail the test?
        self.send_message_exception_failure_count = 5


class DeviceApp(app_base.AppBase):
    """
    Main application object
    """

    def __init__(self):
        super(DeviceApp, self).__init__()

        self.executor = ThreadPoolExecutor(max_workers=128)
        self.done = threading.Event()
        self.client = None
        self.hub = None
        self.device_id = None
        self.metrics = DeviceRunMetrics()
        self.config = DeviceRunConfig()
        self.service_app_run_id = None
        # for pingbacks
        self.pingback_list_lock = threading.Lock()
        self.pingback_wait_list = {}
        self.incoming_pingback_response_queue = queue.Queue()
        # for telemetry
        self.outgoing_send_message_queue = queue.Queue()
        # for metrics
        self.reporter = MetricsReporter()
        self._configure_azure_monitor_metrics()
        # for pairing
        self.pair_request_trigger = threading.Event()

    def _configure_azure_monitor_metrics(self):
        self.reporter.add_float_measurement(
            "process_cpu_percent",
            "processCpuPercent",
            "Amount of CPU usage by the process",
            "percentage",
        )
        self.reporter.add_integer_measurement(
            "process_working_set",
            "processWorkingSet",
            "All physical memory used by the process",
            "bytes",
        )
        self.reporter.add_integer_measurement(
            "process_bytes_in_all_heaps",
            "processBytesInAllHeaps",
            "All virtual memory used by the process",
            "bytes",
        )
        self.reporter.add_integer_measurement(
            "process_private_bytes",
            "processPrivateBytes",
            "Amount of non-shared physical memory used by the process",
            "bytes",
        )
        self.reporter.add_integer_measurement(
            "process_working_set_private",
            "processWorkingSetPrivate",
            "Amount of non-shared physical memory used by the process",
            "bytes",
        )
        self.reporter.add_integer_measurement(
            "send_message_count_sent",
            "sendMessageCountSent",
            "Count of messages sent and ack'd by the transport",
            "message(s)",
        )
        self.reporter.add_integer_measurement(
            "send_message_count_received_by_service",
            "sendMessageCountReceivedByService",
            "Count of messages sent to iothub with receipt verified via service sdk",
            "message(s)",
        )
        self.reporter.add_integer_measurement(
            "send_message_count_in_backlog",
            "sendMessageCountInBacklog",
            "Count of messages waiting to be sent",
            "message(s)",
        )
        self.reporter.add_integer_measurement(
            "send_message_count_unacked",
            "sendMessageCountUnacked",
            "Count of messages sent to iothub but not ack'd by the transport",
            "message(s)",
        )
        self.reporter.add_integer_measurement(
            "send_message_count_not_received_by_service",
            "sendMessageCountNotReceivedByService",
            "Count of messages sent to iothub and acked by the transport, but receipt not (yet) verified via service sdk",
            "message(s)",
        )

    def get_longhaul_metrics(self):
        self.metrics.run_time = (
            datetime.datetime.now(datetime.timezone.utc) - self.metrics.run_start_utc
        )

        sent = self.metrics.send_message_count_sent.get_count()
        received_by_service = self.metrics.send_message_count_received_by_service_app.get_count()

        props = {
            "runStartUtc": self.metrics.run_start_utc.isoformat(),
            "runEndUtc": self.metrics.run_end_utc.isoformat() if self.metrics.run_end_utc else None,
            "runTime": str(self.metrics.run_time),
            "runState": str(self.metrics.run_state),
            "exitReason": self.metrics.exit_reason,
            "sendMessageCountSent": sent,
            "sendMessageCountReceivedByService": received_by_service,
            "sendMessageCountFailures": self.metrics.send_message_count_failures.get_count(),
            "sendMessageCountInBacklog": self.outgoing_send_message_queue.qsize(),
            "sendMessageCountUnacked": self.metrics.send_message_count_unacked.get_count(),
            "sendMessageCountNotReceivedByService": sent - received_by_service,
        }
        return props

    def get_longhaul_config_properties(self):
        """
        return test configuration values as dictionary entries that can be put into reported
        properties
        """
        return {
            "configPropertyUpdateInterval": self.config.thief_property_update_interval,
            "configWatchdogFailureInterval": self.config.watchdog_failure_interval,
            "configPairingRequestTimeoutInterval": self.config.pairing_request_timeout_interval,
            "configPairingRequestSendInterval": self.config.pairing_request_send_interval,
            "configSendMessageOperationsPerSecond": self.config.send_message_operations_per_second,
            "configSendMessageThreadCount": self.config.send_message_thread_count,
            "configSendMessageArrivalFailureInterval": self.config.send_message_arrival_failure_interval,
            "configSendMessageArrivalFailureCount": self.config.send_message_arrival_failure_count,
            "configSendMessageBacklogFailureCount": self.config.send_message_backlog_failure_count,
            "configSendMessageUnackedFailureCount": self.config.send_message_unacked_failure_count,
            "configSendMessageExceptionFailureCount": self.config.send_message_exception_failure_count,
        }

    def update_initial_reported_properties(self):
        """
        Update reported properties at the start of a run
        """
        props = {"thief": self.get_longhaul_metrics()}
        props["thief"].update(self.get_fixed_system_metrics(azure.iot.device.constant.VERSION))
        props["thief"].update(self.get_longhaul_config_properties())
        self.client.patch_twin_reported_properties(props)

    def pair_with_service_instance(self):
        """
        "pair" with a service app. This is necessary because we can have a single
        service app responsible for multiple device apps.  The pairing process works
        like this:

        1. The device sets reported properties saying that it isn't paired and also any
            perferences it has about it's prospective partner.
        2. The device app sends a "pairingRequest" d2c message.
        3. All service apps watch for all pairingRequest messages.  If some service app instance
            wants to get paired with a device, it sets a desired property to indicate that it's
            available.  If multiple service apps set the property, the winner is chosen by the
            device app, probably based on whatever value happens to be in the desired property
            when the device app checks.
        4. Once the device decides on a service app, it sets it's serviceAppRunId reported
            property. It also sets the serviceAppRunId property in any telemetry messages it
            sends.

        Right now, there is no provision for a failing service app.  In the future, the device
        code _could_ see that the service app isn't responding and start the pairing process
        over again.
        """

        # pairing ID is used to distinguish this pairingRequest from any previous pairingRequests
        # which may have been handled in the past.
        pairing_id = str(uuid.uuid4())

        reported_properties = {
            "thief": {
                "serviceAppRunId": None,
                "requestedServicePool": requested_service_pool,
                "pairingId": pairing_id,
            }
        }
        logger.info(
            "Attempting to pair with service instance.  Setting reported properties: {}".format(
                reported_properties
            )
        )
        self.client.patch_twin_reported_properties(reported_properties)

        logger.info("sending pairingRequest message")
        pairing_request_message = Message(json.dumps({"thief": {"cmd": "pairingRequest"}}))
        pairing_request_message.content_type = "application/json"
        pairing_request_message.content_encoding = "utf-8"
        self.client.send_message(pairing_request_message)

        start_time = time.time()
        while time.time() - start_time < self.config.pairing_request_timeout_interval:
            logger.info("Waiting for service app to claim this device")
            patch = self.client.receive_twin_desired_properties_patch(
                timeout=self.config.pairing_request_send_interval
            )

            if patch and "thief" in patch:
                logger.info("Got patch")
                thief = patch["thief"]
                service_app_run_id = thief.get("serviceAppRunId", None)
                if (thief.get("pairingId", None) == pairing_id) and service_app_run_id:
                    logger.info(
                        "Service app {} claimed this device instance".format(service_app_run_id)
                    )
                    self.service_app_run_id = service_app_run_id
                    reported_properties["thief"]["serviceAppRunId"] = service_app_run_id
                    self.client.patch_twin_reported_properties(reported_properties)
                    return
                else:
                    logger.info("patch received, but serviceAppRunId not set")

            logger.info("Still no assigned service instance. Sending new pairingRequest message")
            self.client.send_message(pairing_request_message)

        raise Exception("timeout waitig for service to set serviceAppRunId")

    def pairing_thread(self, worker_thread_info):

        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()

            self.pair_request_trigger.wait(timeout=10)

            if self.pair_request_trigger.isSet():
                logger.info(
                    "pairing_thread: pair_request_trigger is set.  Starting pairing process"
                )
                self.pair_with_service_instance()
                self.pair_request_trigger.clear()

    def is_pairing_complete(self):
        """
        return True if the pairing process is complete
        """
        return True if self.service_app_run_id else False

    def send_message_thread(self, worker_thread_info):
        """
        Thread which reads the telemetry queue and sends the telemetry.  Since send_message is
        blocking, and we want to overlap send_messsage calls, we create multiple
        send_message_thread instances so we can send multiple messages at the same time.

        The number of send_message_thread instances is the number of overlapped sent operations
        we can have
        """
        while not self.done.isSet():
            # We do not check is_pairing_complete here because we use this thread to
            # send pairing requests.
            worker_thread_info.watchdog_time = time.time()
            try:
                msg = self.outgoing_send_message_queue.get(timeout=1)
            except queue.Empty:
                msg = None
            if msg:
                try:
                    self.metrics.send_message_count_unacked.increment()
                    self.client.send_message(msg)
                except Exception as e:
                    self.metrics.send_message_count_failures.increment()
                    logger.error("send_message raised {}".format(e), exc_info=True)
                else:
                    self.metrics.send_message_count_sent.increment()
                finally:
                    self.metrics.send_message_count_unacked.decrement()

    def send_metrics_to_azure_monitor(self, props):
        self.reporter.set_process_cpu_percent(props["processCpuPercent"])
        self.reporter.set_process_working_set(props["processWorkingSet"])
        self.reporter.set_process_bytes_in_all_heaps(props["processBytesInAllHeaps"])
        self.reporter.set_process_private_bytes(props["processPrivateBytes"])
        self.reporter.set_process_working_set_private(props["processCpuPercent"])

        self.reporter.set_send_message_count_sent(props["sendMessageCountSent"])
        self.reporter.set_send_message_count_received_by_service(
            props["sendMessageCountReceivedByService"]
        )
        self.reporter.set_send_message_count_in_backlog(props["sendMessageCountInBacklog"])
        self.reporter.set_send_message_count_unacked(props["sendMessageCountUnacked"])
        self.reporter.set_send_message_count_not_received_by_service(
            props["sendMessageCountNotReceivedByService"]
        )
        self.reporter.record()

    def test_send_message_thread(self, worker_thread_info):
        """
        Thread to continuously send d2c messages throughout the longhaul run.  This thread doesn't
        actually send messages becauase send_message is blocking and we want to overlap our send
        operations.  Instead, this thread adds the messsage to a queue, and relies on a
        send_message_thread instance to actually send the message.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()

            if self.is_pairing_complete():
                pingback_id = str(uuid.uuid4())

                system_health = self.get_system_health_telemetry()
                longhaul_metrics = self.get_longhaul_metrics()

                props = {
                    "thief": {
                        "cmd": "pingbackRequest",
                        "pingbackId": pingback_id,
                        "serviceAppRunId": self.service_app_run_id,
                    }
                }
                props["thief"].update(longhaul_metrics)
                props["thief"].update(system_health)

                # push these same metrics to Azure Monitor
                self.send_metrics_to_azure_monitor(props["thief"])

                msg = Message(json.dumps(props))
                msg.content_type = "application/json"
                msg.content_encoding = "utf-8"
                msg.custom_properties["eventDateTimeUtc"] = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()
                msg.custom_properties["serviceAppRunId"] = self.service_app_run_id

                def on_pingback_received(pingback_id):
                    self.metrics.send_message_count_received_by_service_app.increment()

                with self.pingback_list_lock:
                    self.pingback_wait_list[pingback_id] = MessageWaitingToArrive(
                        callback=on_pingback_received,
                        pingback_id=pingback_id,
                        message=msg,
                        send_time=time.time(),
                    )

                # This function only queues the message.  A send_message_thread instance will pick
                # it up and send it.
                self.outgoing_send_message_queue.put(msg)

                # sleep until we need to send again
                self.done.wait(1 / self.config.send_message_operations_per_second)

            else:
                # pairing is not complete
                time.sleep(1)

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

            props = {"thief": self.get_longhaul_metrics()}

            logger.info("updating thief props: {}".format(props))
            self.client.patch_twin_reported_properties(props)

            self.done.wait(self.config.thief_property_update_interval)

    def dispatch_incoming_message_thread(self, worker_thread_info):
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

                if thief:
                    cmd = thief["cmd"]
                    if cmd == "pingbackResponse":
                        self.incoming_pingback_response_queue.put(msg)
                    else:
                        logger.warning("Unknown command received: {}".format(obj))

                else:
                    logger.warning("C2d received, but it's not for us: {}".format(obj))

    def handle_pingback_response_thread(self, worker_thread_info):
        """
        Thread which handles incoming pingback responses.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()

            try:
                msg = self.incoming_pingback_response_queue.get(timeout=1)
            except queue.Empty:
                msg = None

            if msg:
                arrivals = []
                thief = json.loads(msg.data.decode())["thief"]

                with self.pingback_list_lock:
                    for pingback in thief["pingbacks"]:
                        pingback_id = pingback["pingbackId"]

                        if pingback_id in self.pingback_wait_list:
                            # we've received a pingback.  Don't call back here because we're holding
                            # pingback_lock_list.  Instead, add to a list and call back when we're
                            # not holding the lock.
                            arrivals.append(self.pingback_wait_list[pingback_id])
                            del self.pingback_wait_list[pingback_id]
                        else:
                            logger.warning("Received unkonwn pingbackId: {}:".format(pingback_id))

                for arrival in arrivals:
                    arrival.callback(arrival.pingback_id)

    def check_for_failure_thread(self, worker_thread_info):
        """
        Thread which is responsible for watching for test failures based on limits that are
        exceeded.

        These checks were put into their own thread for 2 reasons:

        1. to centralize this code.

        2. because we have multiple threads doing things like calling send_message.  If we check
           these limits inside those threads, we have the chance for multiple overlapping checks.
           This isn't necessarily destructive, but it could be confusing when analyzing logs.

       """

        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()

            arrival_failure_count = 0
            now = time.time()
            with self.pingback_list_lock:
                for message_waiting in self.pingback_wait_list.values():
                    if (
                        now - message_waiting.send_time
                    ) > self.config.send_message_arrival_failure_interval:
                        logger.warning(
                            "arrival time for {} of {} seconds is longer than failure interval of {}".format(
                                message_waiting.pingback_id,
                                (now - message_waiting.send_time),
                                self.config.send_message_arrival_failure_interval,
                            )
                        )
                        arrival_failure_count += 1

            if arrival_failure_count > self.config.send_message_arrival_failure_count:
                raise Exception(
                    "count of failed arrivals of {} is greater than maximum count of {}".format(
                        arrival_failure_count, self.config.send_message_arrival_failure_count
                    )
                )

            if (
                self.outgoing_send_message_queue.qsize()
                > self.config.send_message_backlog_failure_count
            ):
                raise Exception(
                    "send_message backlog with {} items exceeded maxiumum count of {} items".format(
                        self.outgoing_send_message_queue.qsize(),
                        self.config.send_message_backlog_failure_count,
                    )
                )

            if (
                self.metrics.send_message_count_unacked.get_count()
                > self.config.send_message_unacked_failure_count
            ):
                raise Exception(
                    "unacked message count  of with {} items exceeded maxiumum count of {} items".format(
                        self.metrics.send_message_count_unacked.get_count,
                        self.config.send_message_unacked_failure_count,
                    )
                )

            if (
                self.metrics.send_message_count_failures.get_count()
                > self.config.send_message_exception_failure_count
            ):
                raise Exception(
                    "send_message failure count of {} exceeds maximum count of {} failures".format(
                        self.metrics.send_message_count_failures.get_count(),
                        self.config.send_message_exception_failure_count,
                    )
                )

            time.sleep(10)

    def main(self):

        self.metrics.run_start_utc = datetime.datetime.now(datetime.timezone.utc)
        self.metrics.run_state = app_base.RUNNING

        # Create our client and push initial properties
        self.client, self.hub, self.device_id = dps.create_device_client_using_dps_group_key(
            provisioning_host=provisioning_host,
            registration_id=registration_id,
            id_scope=id_scope,
            group_symmetric_key=group_symmetric_key,
        )
        azure_monitor.configure_logging(hub=self.hub, device_id=self.device_id)
        self.update_initial_reported_properties()

        # pair with a service app instance
        self.pair_with_service_instance()

        # Make a list of threads to launch
        worker_thread_infos = [
            app_base.WorkerThreadInfo(
                self.dispatch_incoming_message_thread, "dispatch_incoming_message_thread"
            ),
            app_base.WorkerThreadInfo(
                self.update_thief_properties_thread, "update_thief_properties_thread"
            ),
            app_base.WorkerThreadInfo(
                self.handle_pingback_response_thread, "handle_pingback_response_thread"
            ),
            app_base.WorkerThreadInfo(self.test_send_message_thread, "test_send_message_thread"),
            app_base.WorkerThreadInfo(self.check_for_failure_thread, "check_for_failure_thread"),
        ]
        for i in range(0, self.config.send_message_thread_count):
            worker_thread_infos.append(
                app_base.WorkerThreadInfo(
                    self.send_message_thread, "send_message_thread #{}".format(i),
                )
            )

        # TODO: add virtual function that can be used to wait for all messages to arrive after test is done

        self.run_threads(worker_thread_infos)

    def disconnect(self):
        self.client.disconnect()


if __name__ == "__main__":
    DeviceApp().main()
