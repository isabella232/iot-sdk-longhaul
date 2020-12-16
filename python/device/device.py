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
import pprint
from concurrent.futures import ThreadPoolExecutor
import dps
import queue
import app_base
from azure.iot.device import Message
import azure.iot.device.constant
from measurement import ThreadSafeCounter
import azure_monitor
from azure_monitor_metrics import MetricsReporter
from out_of_order_message_tracker import OutOfOrderMessageTracker
from thief_constants import PingbackType

logging.basicConfig(level=logging.WARNING)
logging.getLogger("paho").setLevel(level=logging.DEBUG)
logging.getLogger("thief").setLevel(level=logging.INFO)
logging.getLogger("azure.iot").setLevel(level=logging.INFO)

logger = logging.getLogger("thief.{}".format(__name__))

# TODO: exit service when device stops responding
# TODO: add code to receive rest of pingacks at end.  wait for delta since last to be > 20 seconds.
# TODO: add mid to debug logs as custom property, maybe pingback id

# use os.environ[] for required environment variables
provisioning_host = os.environ["THIEF_DEVICE_PROVISIONING_HOST"]
id_scope = os.environ["THIEF_DEVICE_ID_SCOPE"]
group_symmetric_key = os.environ["THIEF_DEVICE_GROUP_SYMMETRIC_KEY"]
registration_id = os.environ["THIEF_DEVICE_ID"]
requested_service_pool = os.environ["THIEF_REQUESTED_SERVICE_POOL"]

run_id = str(uuid.uuid4())

# configure our traces and events to go to Azure Monitor
azure_monitor.add_logging_properties(
    client_type="device",
    run_id=run_id,
    sdk_version=azure.iot.device.constant.VERSION,
    transport="mqtt",
    pool_id=requested_service_pool,
)
event_logger = azure_monitor.get_event_logger()
azure_monitor.log_to_azure_monitor("thief")
azure_monitor.log_to_azure_monitor("azure")
azure_monitor.log_to_azure_monitor("paho")

PingbackWaitInfo = collections.namedtuple(
    "PingbackWaitInfo", "on_pingback_received pingback_id send_epochtime pingback_type user_data"
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
        self.last_io_epochtime = time.time()

        self.send_message_count_unacked = ThreadSafeCounter()
        self.send_message_count_sent = ThreadSafeCounter()
        self.send_message_count_received_by_service_app = ThreadSafeCounter()
        self.send_message_count_failures = ThreadSafeCounter()

        self.receive_message_count_received = ThreadSafeCounter()

        self.reported_properties_count_added = ThreadSafeCounter()
        self.reported_properties_count_added_and_verified_by_service_app = ThreadSafeCounter()
        self.reported_properties_count_removed = ThreadSafeCounter()
        self.reported_properties_count_removed_and_verified_by_service_app = ThreadSafeCounter()


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
        self.thief_property_update_interval_in_seconds = 60

        # How long can a thread go without updating its watchdog before failing.
        self.watchdog_failure_interval_in_seconds = 300

        # How long to keep trying to pair with a service instance before giving up.
        self.pairing_request_timeout_interval_in_seconds = 900

        # How many seconds to wait before sending a new pairingRequest message
        self.pairing_request_send_interval_in_seconds = 30

        # How many times to call send_message per second
        self.send_message_operations_per_second = 1

        # How many threads do we spin up for overlapped send_message calls.  These threads
        # pull messages off of a single outgoing queue.  If all of the send_message threads
        # are busy, outgoing messages will just pile up in the queue.
        self.send_message_thread_count = 10

        # How long do we wait for notification that the service app received a
        # message before we consider it a failure?
        self.send_message_arrival_failure_interval_in_seconds = 3600

        # How many messages fail to arrive at the service before we fail the test
        # This counts messages that have been sent and acked, but the service app hasn't reported receipt.
        self.send_message_arrival_allowed_failure_count = 10

        # How many messages to we allow in the send_message backlog before we fail the test.
        # This counts messages that gets backed up because send_message hasn't even been called yet.
        self.send_message_backlog_allowed_failure_count = 200

        # How many unack'ed messages do we allow before we fail the test?
        # This counts messages that either haven't been sent, or they've been sent but not ack'ed by the receiver
        self.send_message_unacked_allowed_failure_count = 200

        # How many send_message exceptions do we allow before we fail the test?
        self.send_message_exception_allowed_failure_count = 10

        # How often do we want the service to send test C2D messages?
        # Be careful with this.  Too often will result in throttling on the service, which has 1.83  messages/sec/unit as a shared limit for all devices
        self.receive_message_interval_in_seconds = 20

        # How big, in bytes, is the maximum random text size in test C2D messages to be
        self.receive_message_max_filler_size = 16 * 1024

        # How many missing C2D messages will cause the test to fail?
        self.receive_message_missing_message_allowed_failure_count = 100

        # How many seconds between reported property patches
        self.reported_properties_update_interval_in_seconds = 10

        # How many reported property patches are allowed to fail before we fail the test
        self.reported_properties_update_allowed_failure_count = 100

        # How many seconds do we wait for the service to acknowledge a reported property update
        # before we consider it failed
        self.reported_properties_verify_failure_interval_in_seconds = 3600


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
        self.service_run_id = None
        # for pingbacks
        self.pingback_list_lock = threading.Lock()
        self.pingback_wait_list = {}
        self.incoming_pingback_response_queue = queue.Queue()
        # for telemetry
        self.outgoing_test_message_queue = queue.Queue()
        # for metrics
        self.reporter = MetricsReporter()
        self._configure_azure_monitor_metrics()
        # for pairing
        self.pairing_complete = False
        self.incoming_pairing_message_queue = queue.Queue()
        self.pairing_id = None
        # for c2d
        self.out_of_order_message_tracker = OutOfOrderMessageTracker()
        self.incoming_test_c2d_message_queue = queue.Queue()

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
            "send_message_count_received_by_service_app",
            "sendMessageCountReceivedByServiceApp",
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
            "send_message_count_not_received_by_service_app",
            "sendMessageCountNotReceivedByServiceApp",
            "Count of messages sent to iothub and acked by the transport, but receipt not (yet) verified via service sdk",
            "message(s)",
        )
        self.reporter.add_integer_measurement(
            "send_message_count_failures",
            "sendMessageCountFailures",
            "Count of messages that failed to send",
            "message(s)",
        )
        self.reporter.add_integer_measurement(
            "receive_message_count_received",
            "receiveMessageCountReceived",
            "Count of messages received from the service",
            "message(s)",
        )
        self.reporter.add_integer_measurement(
            "receive_message_count_missing",
            "receiveMessageCountMissing",
            "Count of messages sent my the service but not received",
            "message(s)",
        )
        self.reporter.add_integer_measurement(
            "reportedPropertiesCountAdded",
            "reported_properties_count_added",
            "Count of reported properties added",
            "patches with add operation(s)",
        )
        self.reporter.add_integer_measurement(
            "reportedPropertiesCountAddedAndVerifiedByServiceApp",
            "reported_properties_count_added_and_verified_by_service_app",
            "Count of reported properties added and verified by the service app",
            "patches with add operation(s)",
        )
        self.reporter.add_integer_measurement(
            "reportedPropertiesCountRemoved",
            "reported_properties_count_removed",
            "Count of reported properties removed",
            "patches with remove operation(s)",
        )
        self.reporter.add_integer_measurement(
            "reportedPropertiesCountRemovedAndVerifiedByServiceApp",
            "reported_properties_count_removed_and_verified_by_service_app",
            "Count of reported properties removed and verified by the service app",
            "patches with remove operations(s)",
        )

    def get_session_metrics(self):
        """
        Return metrics which describe the session the tests are running in
        """
        self.metrics.run_time = (
            datetime.datetime.now(datetime.timezone.utc) - self.metrics.run_start_utc
        )

        props = {
            "runStartUtc": self.metrics.run_start_utc.isoformat(),
            "latestUpdateTimeUtc": datetime.datetime.utcnow().isoformat(),
            "runEndUtc": self.metrics.run_end_utc.isoformat() if self.metrics.run_end_utc else None,
            "runTime": str(self.metrics.run_time),
            "runState": str(self.metrics.run_state),
            "exitReason": self.metrics.exit_reason,
            "pairingId": self.pairing_id,
        }
        return props

    def get_test_metrics(self):
        """
        Return metrics which describe the progress of the  different features being tested
        """
        sent = self.metrics.send_message_count_sent.get_count()
        received_by_service_app = (
            self.metrics.send_message_count_received_by_service_app.get_count()
        )

        props = {
            "sendMessageCountSent": sent,
            "sendMessageCountReceivedByServiceApp": received_by_service_app,
            "sendMessageCountFailures": self.metrics.send_message_count_failures.get_count(),
            "sendMessageCountInBacklog": self.outgoing_test_message_queue.qsize(),
            "sendMessageCountUnacked": self.metrics.send_message_count_unacked.get_count(),
            "sendMessageCountNotReceivedByServiceApp": sent - received_by_service_app,
            "receiveMessageCountReceived": self.metrics.receive_message_count_received.get_count(),
            "receiveMessageCountMissing": self.out_of_order_message_tracker.get_missing_count(),
            "reportedPropertiesCountAdded": self.metrics.reported_properties_count_added.get_count(),
            "reportedPropertiesCountAddedAndVerifiedByServiceApp": self.metrics.reported_properties_count_added_and_verified_by_service_app.get_count(),
            "reportedPropertiesCountRemoved": self.metrics.reported_properties_count_removed.get_count(),
            "reportedPropertiesCountRemovedAndVerifiedByServiceApp": self.metrics.reported_properties_count_removed_and_verified_by_service_app.get_count(),
        }
        return props

    def get_longhaul_config_properties(self):
        """
        return test configuration values as dictionary entries that can be put into reported
        properties
        """
        return {
            "thiefPropertyUpdateIntervalInSeconds": self.config.thief_property_update_interval_in_seconds,
            "watchdogFailureIntervalInSeconds": self.config.watchdog_failure_interval_in_seconds,
            "pairingRequestTimeoutIntervalInSeconds": self.config.pairing_request_timeout_interval_in_seconds,
            "pairingRequestSendIntervalInSeconds": self.config.pairing_request_send_interval_in_seconds,
            "sendMessageOperationsPerSecond": self.config.send_message_operations_per_second,
            "sendMessageThreadCount": self.config.send_message_thread_count,
            "sendMessageArrivalFailureIntervalInSeconds": self.config.send_message_arrival_failure_interval_in_seconds,
            "sendMessageArrivalAllowedFailureCount": self.config.send_message_arrival_allowed_failure_count,
            "sendMessageBacklogAllowedFailureCount": self.config.send_message_backlog_allowed_failure_count,
            "sendMessageUnackedAllowedFailureCount": self.config.send_message_unacked_allowed_failure_count,
            "sendMessageExceptionAllowedFailureCount": self.config.send_message_exception_allowed_failure_count,
            "receiveMessageIntervalInSeconds": self.config.receive_message_interval_in_seconds,
            "receiveMessageMaxFillerSize": self.config.receive_message_max_filler_size,
            "receiveMessageMissingMessageAllowedFailureCount": self.config.receive_message_missing_message_allowed_failure_count,
            "reportedPropertiesUpdateIntervalInSeconds": self.config.reported_properties_update_interval_in_seconds,
            "reportedPropertiesVerifyFailureIntervalInSeconds": self.config.reported_properties_verify_failure_interval_in_seconds,
            "reportedPropertiesUpdateAllowedFailureCount": self.config.reported_properties_update_allowed_failure_count,
        }

    def update_initial_reported_properties(self):
        """
        Update reported properties at the start of a run
        """
        props = {
            "thief": {
                "systemProperties": self.get_system_properties(azure.iot.device.constant.VERSION),
                "sessionMetrics": self.get_session_metrics(),
                "testMetrics": self.get_test_metrics(),
                "config": self.get_longhaul_config_properties(),
                "testContent": None,
                "testControl": None,
            }
        }
        self.client.patch_twin_reported_properties(props)

    def create_message_from_dict(self, props):
        """
        helper function to create a message from a dict object
        """

        # Note: we're changing the dictionary that the user passed in.
        # This isn't the best idea, but it works and it saves us from deep copies
        if self.service_run_id:
            props["thief"]["serviceRunId"] = self.service_run_id
        props["thief"]["pairingId"] = self.pairing_id

        # This function only creates the message.  The caller needs to queue it up for sending.
        msg = Message(json.dumps(props))
        msg.content_type = "application/json"
        msg.content_encoding = "utf-8"

        msg.custom_properties["eventDateTimeUtc"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()

        return msg

    def create_message_from_dict_with_pingback(
        self, props, on_pingback_received, pingback_type, user_data=None
    ):
        """
        helper function to create a message from a dict and add pingback
        properties.
        """
        pingback_id = str(uuid.uuid4())

        # Note: we're changing the dictionary that the user passed in.
        # This isn't the best idea, but it works and it saves us from deep copies
        assert props["thief"].get("cmd", None) is None
        props["thief"]["cmd"] = "pingbackRequest"
        props["thief"]["pingbackId"] = pingback_id
        props["thief"]["pingbackType"] = pingback_type

        with self.pingback_list_lock:
            self.pingback_wait_list[pingback_id] = PingbackWaitInfo(
                on_pingback_received=on_pingback_received,
                pingback_id=pingback_id,
                send_epochtime=time.time(),
                pingback_type=pingback_type,
                user_data=user_data,
            )

        logger.info("Requesting {} pingback for pingbackId = {}".format(pingback_type, pingback_id))
        return self.create_message_from_dict(props)

    def pairing_thread(self, worker_thread_info):
        """
        "pair" with a service app. This is necessary because we can have a single
        service app responsible for multiple device apps.  The pairing process works
        like this:

        1. Device sets reported properties in `properties/reported/thief/pairing` which indicates
            that it doesn't have a service app (by settign `serviceRunId` = None).
        2. An available service sets `properties/desired/thief/pairing/serviceRunId` to the service
            app's `runId` value
        3. The device sets `properties/reported/thief/pairing/serviceRunId` to the serivce app's
            `runId` value.
        4. When the service sees that the device accepted it, it sets
            `properties/desired/thief/pairing/acceptedPairing`.  At this point, the pairing is
            considered to be "complete" and testing can begin.

        Right now, there is no provision for a failing service app.  In the future, the device
        code _could_ see that the service app isn't responding and start the pairing process
        over again.
        """

        currently_pairing = False
        pairing_start_epochtime = 0
        pairing_last_request_epochtime = 0

        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            try:
                msg = self.incoming_pairing_message_queue.get(timeout=1)
            except queue.Empty:
                msg = None

            repeat_pairing_request = False
            if not msg and currently_pairing:
                # if we're trying to pair and we haven't seen a response yet, we may need to
                # re-send our request (by setting the desired property again), or it may be
                # time to fail the pairing operation
                if (
                    time.time() - pairing_start_epochtime
                ) > self.config.pairing_request_timeout_interval_in_seconds:
                    raise Exception(
                        "No resopnse to pairing requests after trying for {} seconds".format(
                            self.config.pairing_request_timeout_interval_in_seconds
                        )
                    )

                elif (
                    time.time() - pairing_last_request_epochtime
                ) > self.config.pairing_request_send_interval_in_seconds:
                    logger.info("pairing response timeout.  Requesting again")
                    repeat_pairing_request = True

            # We trigger a pairing operation by pushing this string into our incoming queue.
            # Not a great design, but it lets us group this functionality into a single thread
            # without adding another signalling mechanism.
            if msg == "START_PAIRING":
                pairing_start_epochtime = time.time()
            if msg == "START_PAIRING" or repeat_pairing_request:
                # Set our reported properties to start a pairing operation or to try again if no
                # service has responded yet.
                logger.info("Starting pairing operation")
                currently_pairing = True
                self.pairing_complete = False
                pairing_last_request_epochtime = time.time()
                self.pairing_id = str(uuid.uuid4())
                self.service_run_id = None
                props = {
                    "thief": {
                        "pairing": {
                            "requestedServicePool": requested_service_pool,
                            "serviceRunId": None,
                            "pairingId": self.pairing_id,
                            "deviceRunId": run_id,
                        }
                    }
                }
                logger.info("updating pairing reported props: {}".format(pprint.pformat(props)))
                self.client.patch_twin_reported_properties(props)
                azure_monitor.add_logging_properties(pairing_id=self.pairing_id)

            elif msg and isinstance(msg, dict):
                # is msg is a dict, that means we have a desired property change.  A service
                # might be trying to pair with us.
                logger.info("received pairing desired props: {}".format(pprint.pformat(msg)))

                pairing = msg.get("thief", {}).get("pairing", {})
                pairing_id = pairing.get("pairingId", None)
                service_run_id = pairing.get("serviceRunId", None)
                accepted_pairing = pairing.get("acceptedPairing", None)

                if accepted_pairing == "{},{}".format(self.pairing_id, self.service_run_id):
                    # This is the final part of the pairing.  Once `acceptedPairing` is set, we're
                    # done
                    logger.info("Pairing accepted by service")
                    self.on_pairing_complete()

                elif self.service_run_id:
                    # It's possible that a second service app tried to pair with us after we
                    # already chose someone else.  Ignore this
                    logger.info("Already paired.  Ignoring.")

                elif not pairing_id or not service_run_id:
                    # Or maybe something is wrong with the desired properties.  Probably a
                    # service app that goes by different rules. Ignoring it is better than
                    # crashing.
                    logger.info("pairingId and/or serviceRunId missing.  Ignoring.")

                elif pairing_id != self.pairing_id:
                    # Another strange case.  A service app is trying to pair with us, but it's
                    # using a different `pairingId` value.  It's possible that we had to
                    # repeat the pairing request and a service is responding to the older
                    # request.  Ignore this.
                    logger.info(
                        "pairingId mismatch.  Ignoring. (received {}, expected {})".format(
                            pairing_id, self.pairing_id
                        )
                    )

                else:
                    # It looks like a service app has decided to pair with us.  Set reported
                    # properties to "select" this service instance as our partner.
                    logger.info(
                        "Service app {} claimed this device instance".format(service_run_id)
                    )
                    currently_pairing = False
                    self.service_run_id = service_run_id
                    props = {
                        "thief": {
                            "pairing": {
                                "serviceRunId": self.service_run_id,
                                "pairingId": self.pairing_id,
                            }
                        }
                    }
                    currently_pairing = False
                    logger.info("updating pairing reported props: {}".format(pprint.pformat(props)))
                    self.client.patch_twin_reported_properties(props)

    def start_pairing(self):
        """
        trigger the pairing process
        """
        self.incoming_pairing_message_queue.put("START_PAIRING")

    def is_pairing_complete(self):
        """
        return True if the pairing process is complete
        """
        return self.pairing_id and self.service_run_id and self.pairing_complete

    def on_pairing_complete(self):
        """
        Called when pairing is complete
        """
        logger.info("Pairing is complete.  Starting c2d")
        self.pairing_complete = True
        self.start_c2d_message_sending()

    def send_message_thread(self, worker_thread_info):
        """
        Thread which reads the telemetry queue and sends the telemetry.  Since send_message is
        blocking, and we want to overlap send_messsage calls, we create multiple
        send_message_thread instances so we can send multiple messages at the same time.

        The number of send_message_thread instances is the number of overlapped sent operations
        we can have
        """
        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            try:
                msg = self.outgoing_test_message_queue.get(timeout=1)
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
        """
        Send metrics to azure monitor, based on the reported properties that we probably just
        sent to the hub
        """
        # we don't record session_metrics to azure monitor

        system_health_metrics = props["systemHealthMetrics"]
        self.reporter.set_process_cpu_percent(system_health_metrics["processCpuPercent"])
        self.reporter.set_process_working_set(system_health_metrics["processWorkingSet"])
        self.reporter.set_process_bytes_in_all_heaps(
            system_health_metrics["processBytesInAllHeaps"]
        )
        self.reporter.set_process_private_bytes(system_health_metrics["processPrivateBytes"])
        self.reporter.set_process_working_set_private(system_health_metrics["processCpuPercent"])

        test_metrics = props["testMetrics"]
        self.reporter.set_send_message_count_sent(test_metrics["sendMessageCountSent"])
        self.reporter.set_send_message_count_received_by_service_app(
            test_metrics["sendMessageCountReceivedByServiceApp"]
        )
        self.reporter.set_send_message_count_in_backlog(test_metrics["sendMessageCountInBacklog"])
        self.reporter.set_send_message_count_unacked(test_metrics["sendMessageCountUnacked"])
        self.reporter.set_send_message_count_not_received_by_service_app(
            test_metrics["sendMessageCountNotReceivedByServiceApp"]
        )
        self.reporter.set_receive_message_count_received(
            test_metrics["receiveMessageCountReceived"]
        )
        self.reporter.set_receive_message_count_missing(test_metrics["receiveMessageCountMissing"])
        self.reporter.record()

    def test_send_message_thread(self, worker_thread_info):
        """
        Thread to continuously send d2c messages throughout the longhaul run.  This thread doesn't
        actually send messages becauase send_message is blocking and we want to overlap our send
        operations.  Instead, this thread adds the messsage to a queue, and relies on a
        send_message_thread instance to actually send the message.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            if self.is_pairing_complete():
                props = {
                    "thief": {
                        "sessionMetrics": self.get_session_metrics(),
                        "testMetrics": self.get_test_metrics(),
                        "systemHealthMetrics": self.get_system_health_telemetry(),
                    }
                }

                # push these same metrics to Azure Monitor
                self.send_metrics_to_azure_monitor(props["thief"])

                def on_pingback_received(pingback_id, user_data):
                    logger.info("received pingback with pingbackId = {}".format(pingback_id))
                    self.metrics.send_message_count_received_by_service_app.increment()

                # This function only queues the message.  A send_message_thread instance will pick
                # it up and send it.
                msg = self.create_message_from_dict_with_pingback(
                    props=props,
                    on_pingback_received=on_pingback_received,
                    pingback_type=PingbackType.TELEMETRY_PINGBACK,
                )
                self.outgoing_test_message_queue.put(msg)

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
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            # setting this at the begining and checking at the end guarantees one last update
            # before the thread dies
            if self.done.isSet():
                done = True

            props = {
                "thief": {
                    "sessionMetrics": self.get_session_metrics(),
                    "testMetrics": self.get_test_metrics(),
                    # system_health_metrics don't go into reported properties
                }
            }

            logger.info("updating thief props: {}".format(pprint.pformat(props)))
            self.client.patch_twin_reported_properties(props)

            self.done.wait(self.config.thief_property_update_interval_in_seconds)

    def wait_for_desired_properties_thread(self, worker_thread_info):
        """
        Thread which waits for desired property patches and puts them into
        queues for other threads to handle
        """
        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            props = self.client.receive_twin_desired_properties_patch(timeout=30)
            if props:
                # props that have the pairing structure go to `incoming_pairing_message_queue`
                if props.get("thief", {}).get("pairing", {}):
                    self.incoming_pairing_message_queue.put(props)

                # Other props get dropped.  Eventually we'll use this to test desired properties.
                # self.incoming_desired_property_patch_queue.put(props)

    def dispatch_incoming_message_thread(self, worker_thread_info):
        """
        Thread which continuously receives c2d messages throughout the test run.  This
        thread does minimal processing for each c2d.  If anything complex needs to happen as a
        result of a c2d message, this thread puts the message into a queue for some other thread
        to pick up.
        """
        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            msg = self.client.receive_message(timeout=30)

            if msg:
                obj = json.loads(msg.data.decode())
                thief = obj.get("thief")

                if thief and thief["pairingId"] == self.pairing_id:
                    # We only inspect messages that have `thief/pairingId` set to our `pairingId` value.
                    cmd = thief["cmd"]
                    if cmd == "pingbackResponse":
                        # If this is a pingback response, we put it into `incoming_pingback_response_queue`
                        # for another thread to handle.
                        logger.info("received {} message with {}".format(cmd, thief["pingbacks"]))
                        self.incoming_pingback_response_queue.put(msg)

                    elif cmd == "testC2d":
                        # If thie is a test C2D messages, we put it into `incoming_test_c2d_message_queue`
                        # for another thread to handle.
                        logger.info(
                            "received {} message with index {}".format(
                                cmd, thief["testC2dMessageIndex"]
                            )
                        )
                        self.incoming_test_c2d_message_queue.put(msg)

                    else:
                        logger.warning("Unknown command received: {}".format(obj))

                else:
                    logger.warning("C2d received, but it's not for us: {}".format(obj))

    def handle_pingback_response_thread(self, worker_thread_info):
        """
        Thread which handles incoming pingback responses.  This is where we go through the list
        of `pingbackId` values that we received and call the appropriate callbacks to indicate that
        the service has responded.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

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
                    arrival.on_pingback_received(arrival.pingback_id, arrival.user_data)

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
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            arrival_failure_count = 0
            reported_properties_add_failure_count = 0
            reported_properties_remove_failure_count = 0
            now = time.time()

            with self.pingback_list_lock:
                for pingback_wait_info in self.pingback_wait_list.values():
                    if (pingback_wait_info.pingback_type == PingbackType.TELEMETRY_PINGBACK) and (
                        now - pingback_wait_info.send_epochtime
                    ) > self.config.send_message_arrival_failure_interval_in_seconds:
                        logger.warning(
                            "arrival time for {} of {} seconds is longer than failure interval of {}".format(
                                pingback_wait_info.pingback_id,
                                (now - pingback_wait_info.send_epochtime),
                                self.config.send_message_arrival_failure_interval_in_seconds,
                            )
                        )
                        arrival_failure_count += 1

                    elif (
                        (
                            pingback_wait_info.pingback_type
                            == PingbackType.ADD_REPORTED_PROPERTY_PINGBACK
                        )
                        and (now - pingback_wait_info.send_epochtime)
                        > self.config.reported_properties_update_interval_in_seconds
                    ):
                        logger.warning(
                            "reported property set time for {} of {} seconds is longer than failure interval of {}".format(
                                pingback_wait_info.pingback_id,
                                (now - pingback_wait_info.send_epochtime),
                                self.config.reported_properties_update_interval_in_seconds,
                            )
                        )
                        reported_properties_add_failure_count += 1

                    elif (
                        (
                            pingback_wait_info.pingback_type
                            == PingbackType.REMOVE_REPORTED_PROPERTY_PINGBACK
                        )
                        and (now - pingback_wait_info.send_epochtime)
                        > self.config.reported_properties_update_interval_in_seconds
                    ):
                        logger.warning(
                            "reported property clear time for {} of {} seconds is longer than failure interval of {}".format(
                                pingback_wait_info.pingback_id,
                                (now - pingback_wait_info.send_epochtime),
                                self.config.reported_properties_update_interval_in_seconds,
                            )
                        )
                        reported_properties_remove_failure_count += 1

            if arrival_failure_count > self.config.send_message_arrival_allowed_failure_count:
                raise Exception(
                    "count of failed arrivals of {} is greater than maximum count of {}".format(
                        arrival_failure_count,
                        self.config.send_message_arrival_allowed_failure_count,
                    )
                )

            if (
                reported_properties_add_failure_count
                > self.config.reported_properties_update_allowed_failure_count
            ):
                raise Exception(
                    "count of failed reported property sets of {} is greater than maximum count of {}".format(
                        reported_properties_add_failure_count,
                        self.config.reported_properties_update_allowed_failure_count,
                    )
                )

            if (
                reported_properties_remove_failure_count
                > self.config.reported_properties_update_allowed_failure_count
            ):
                raise Exception(
                    "count of failed reported property clears of {} is greater than maximum count of {}".format(
                        reported_properties_remove_failure_count,
                        self.config.reported_properties_update_allowed_failure_count,
                    )
                )

            if (
                self.outgoing_test_message_queue.qsize()
                > self.config.send_message_backlog_allowed_failure_count
            ):
                raise Exception(
                    "send_message backlog with {} items exceeded maxiumum count of {} items".format(
                        self.outgoing_test_message_queue.qsize(),
                        self.config.send_message_backlog_allowed_failure_count,
                    )
                )

            if (
                self.metrics.send_message_count_unacked.get_count()
                > self.config.send_message_unacked_allowed_failure_count
            ):
                raise Exception(
                    "unacked message count  of with {} items exceeded maxiumum count of {} items".format(
                        self.metrics.send_message_count_unacked.get_count,
                        self.config.send_message_unacked_allowed_failure_count,
                    )
                )

            if (
                self.metrics.send_message_count_failures.get_count()
                > self.config.send_message_exception_allowed_failure_count
            ):
                raise Exception(
                    "send_message failure count of {} exceeds maximum count of {} failures".format(
                        self.metrics.send_message_count_failures.get_count(),
                        self.config.send_message_exception_allowed_failure_count,
                    )
                )

            if (
                self.out_of_order_message_tracker.get_missing_count()
                > self.config.receive_message_missing_message_allowed_failure_count
            ):
                raise Exception(
                    "missing received message count of {} exceeds maximum count of {} missing".format(
                        self.out_of_order_message_tracker.get_missing_count(),
                        self.config.receive_message_missing_message_allowed_failure_count,
                    )
                )

            time.sleep(10)

    def start_c2d_message_sending(self):
        """
        set a reported property to start c2d messages flowing
        """
        # TODO: add a timeout here, make sure the messages are correct, and make sure messages actually flow

        props = {
            "thief": {
                "testControl": {
                    "c2d": {
                        "send": True,
                        "messageIntervalInSeconds": self.config.receive_message_interval_in_seconds,
                        "maxFillerSize": self.config.receive_message_max_filler_size,
                    }
                }
            }
        }

        logger.info("Enabling C2d message testing: {}".format(props))
        self.client.patch_twin_reported_properties(props)

    def handle_incoming_test_c2d_messages_thread(self, worker_thread_info):
        """
        Thread which handles c2d messages that were sent by the service app for the purpose of
        testing c2d
        """
        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            try:
                msg = self.incoming_test_c2d_message_queue.get(timeout=1)
            except queue.Empty:
                msg = None

            if msg:
                thief = json.loads(msg.data.decode())["thief"]
                self.metrics.receive_message_count_received.increment()
                self.out_of_order_message_tracker.add_message(thief.get("testC2dMessageIndex"))

    def test_reported_properties_threads(self, worker_thread_info):
        """
        Thread to test reported properties.  It does this by setting properties inside
        `properties/reported/thief/testContent/reportedPropertyTest`.  Each property has
        a `addPingbackId` value and a `removePingbackId` value.  When  the service sees the
        property added, it sends the `addPingbackId` to the device.  When the service sees the
        property removed, it sends the `removePingbackid` to the device. This way the device
        can add a property, verify that it was added, then remove it and verify that it was removed.
        """

        property_index = 1

        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            if self.is_pairing_complete():
                add_pingback_id = str(uuid.uuid4())
                remove_pingback_id = str(uuid.uuid4())

                property_name = "prop_{}".format(property_index)
                property_value = {
                    "addPingbackId": add_pingback_id,
                    "removePingbackId": remove_pingback_id,
                }

                def on_property_added(pingback_id, user_data):
                    self.metrics.reported_properties_count_added_and_verified_by_service_app.increment()
                    added_property_name = user_data
                    logger.info(
                        "Add of reported property {} verified by service".format(
                            added_property_name
                        )
                    )

                    reported_properties = {
                        "thief": {
                            "testContent": {"reportedPropertyTest": {added_property_name: None}}
                        }
                    }
                    logger.info("Removing test property {}".format(added_property_name))
                    self.client.patch_twin_reported_properties(reported_properties)
                    self.metrics.reported_properties_count_removed.increment()

                def on_property_removed(pingback_id, user_data):
                    self.metrics.reported_properties_count_removed_and_verified_by_service_app.increment()
                    removed_property_name = user_data
                    logger.info(
                        "Remove of reported property {} verified by service".format(
                            removed_property_name
                        )
                    )

                with self.pingback_list_lock:
                    self.pingback_wait_list[add_pingback_id] = PingbackWaitInfo(
                        on_pingback_received=on_property_added,
                        pingback_id=add_pingback_id,
                        send_epochtime=time.time(),
                        pingback_type=PingbackType.ADD_REPORTED_PROPERTY_PINGBACK,
                        user_data=property_name,
                    )
                    self.pingback_wait_list[remove_pingback_id] = PingbackWaitInfo(
                        on_pingback_received=on_property_removed,
                        pingback_id=remove_pingback_id,
                        send_epochtime=time.time(),
                        pingback_type=PingbackType.REMOVE_REPORTED_PROPERTY_PINGBACK,
                        user_data=property_name,
                    )

                reported_properties = {
                    "thief": {
                        "testContent": {"reportedPropertyTest": {property_name: property_value}}
                    }
                }
                logger.info("Adding test property {}".format(property_name))
                self.client.patch_twin_reported_properties(reported_properties)
                self.metrics.reported_properties_count_added.increment()

                property_index += 1

            time.sleep(self.config.reported_properties_update_interval_in_seconds)

    def main(self):

        self.metrics.run_start_utc = datetime.datetime.now(datetime.timezone.utc)
        self.metrics.run_state = app_base.RUNNING
        logger.info("Starting at {}".format(self.metrics.run_start_utc))

        # Create our client and push initial properties
        self.client, self.hub, self.device_id = dps.create_device_client_using_dps_group_key(
            provisioning_host=provisioning_host,
            registration_id=registration_id,
            id_scope=id_scope,
            group_symmetric_key=group_symmetric_key,
        )
        azure_monitor.add_logging_properties(hub=self.hub, device_id=self.device_id)
        self.update_initial_reported_properties()

        # pair with a service app instance
        self.start_pairing()

        # Make a list of threads to launch
        worker_thread_infos = [
            app_base.WorkerThreadInfo(
                self.dispatch_incoming_message_thread, "dispatch_incoming_message_thread"
            ),
            app_base.WorkerThreadInfo(
                self.update_thief_properties_thread, "update_thief_properties_thread"
            ),
            app_base.WorkerThreadInfo(
                self.wait_for_desired_properties_thread, "wait_for_desired_properties_thread"
            ),
            app_base.WorkerThreadInfo(
                self.handle_pingback_response_thread, "handle_pingback_response_thread"
            ),
            app_base.WorkerThreadInfo(self.test_send_message_thread, "test_send_message_thread"),
            app_base.WorkerThreadInfo(self.check_for_failure_thread, "check_for_failure_thread"),
            app_base.WorkerThreadInfo(
                self.handle_incoming_test_c2d_messages_thread,
                "handle_incoming_test_c2d_messages_thread",
            ),
            app_base.WorkerThreadInfo(self.pairing_thread, "pairing_thread"),
            app_base.WorkerThreadInfo(
                self.test_reported_properties_threads, "test_reported_properties_threads"
            ),
        ]
        for i in range(0, self.config.send_message_thread_count):
            worker_thread_infos.append(
                app_base.WorkerThreadInfo(
                    self.send_message_thread, "send_message_thread #{}".format(i),
                )
            )

        # TODO: add virtual function that can be used to wait for all messages to arrive after test is done

        self.run_threads(worker_thread_infos)

        logger.info("Exiting main at {}".format(datetime.datetime.utcnow()))

    def disconnect(self):
        self.client.disconnect()


if __name__ == "__main__":
    try:
        DeviceApp().main()
    except Exception as e:
        logger.error("App shutdown exception: {}".format(str(e)), exc_info=True)
        raise
    finally:
        # Flush azure monitor telemetry
        logging.shutdown()
