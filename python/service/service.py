# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import logging
import time
import os
import queue
import threading
import json
import datetime
import app_base
import uuid
import collections
from concurrent.futures import ThreadPoolExecutor
from azure.iot.hub import IoTHubRegistryManager
from azure.iot.hub.protocol.models import Twin, TwinProperties
import azure.iot.hub.constant
from azure.eventhub import EventHubConsumerClient
import azure_monitor
from utilities import get_random_length_string
from thief_constants import PingbackType


logging.basicConfig(format="%(asctime)s %(levelname)s:%(message)s", level=logging.WARNING)
logging.getLogger("thief").setLevel(level=logging.INFO)
logging.getLogger("azure.iot").setLevel(level=logging.INFO)

logger = logging.getLogger("thief.{}".format(__name__))

# use os.environ[] for required environment variables
iothub_connection_string = os.environ["THIEF_SERVICE_CONNECTION_STRING"]
iothub_name = os.environ["THIEF_IOTHUB_NAME"]
eventhub_connection_string = os.environ["THIEF_EVENTHUB_CONNECTION_STRING"]
eventhub_consumer_group = os.environ["THIEF_EVENTHUB_CONSUMER_GROUP"]
service_pool = os.environ["THIEF_SERVICE_POOL"]

run_id = str(uuid.uuid4())

# configure our traces and events to go to Azure Monitor
azure_monitor.add_logging_properties(
    client_type="service",
    run_id=run_id,
    hub=iothub_name,
    sdk_version=azure.iot.hub.constant.VERSION,
    pool_id=service_pool,
)
event_logger = azure_monitor.get_event_logger()
azure_monitor.log_to_azure_monitor("thief")
azure_monitor.log_to_azure_monitor("azure")
azure_monitor.log_to_azure_monitor("uamqp")


Pingback = collections.namedtuple("Pingback", "device_id pingback_id")

# TODO: remove items from pairing list of no traffic for X minutes


def custom_props(device_id, pairing_id=None):
    """
    helper function for adding customDimensions to logger calls at execution time
    """
    props = {"deviceId": device_id}
    if pairing_id:
        props["pairingId"] = pairing_id
    return {"custom_dimensions": props}


class PerDeviceData(object):
    def __init__(self, pairing_id, device_id):
        self.device_id = device_id

        # For Pairing
        self.pairing_id = pairing_id

        # For testing C2D
        self.test_c2d_enabled = False
        self.first_c2d_sent = False
        self.next_c2d_message_index = 0
        self.c2d_interval_in_seconds = 0
        self.c2d_max_filler_size = 0
        self.c2d_next_message_epochtime = 0

        # for verifying reported property changes
        self.reported_property_list_lock = threading.Lock()
        self.reported_property_values = {}


class ServiceRunMetrics(object):
    """
    Object we use internally to keep track of how a the entire test is performing.
    """

    def __init__(self):
        self.run_start_utc = None
        self.run_end_utc = None
        self.run_time = None
        self.run_state = app_base.WAITING
        self.exit_reason = None


class ServiceRunConfig(object):
    """
    Object we use internally to keep track of how the entire test is configured.
    """

    def __init__(self):
        # How long does this app live?  0 == forever
        self.max_run_duration = 0

        # How long do we allow a thread to be unresponsive for.
        self.watchdog_failure_interval_in_seconds = 300

        # How often to refresh the AMQP connection.  Necessary because of a 10 minute hardcoded credential interval
        self.amqp_refresh_interval_in_seconds = 4 * 60


def get_device_id_from_event(event):
    """
    Helper function to get the device_id from an EventHub message
    """
    return event.message.annotations["iothub-connection-device-id".encode()].decode()


def get_message_source_from_event(event):
    """
    Helper function to get the message source from an EventHub message
    """
    return event.message.annotations["iothub-message-source".encode()].decode()


class ServiceApp(app_base.AppBase):
    """
    Main application object
    """

    def __init__(self):
        super(ServiceApp, self).__init__()

        self.executor = ThreadPoolExecutor(max_workers=128)
        self.done = threading.Event()
        self.registry_manager_lock = threading.Lock()
        self.registry_manager = None
        self.eventhub_consumer_client = None
        self.metrics = ServiceRunMetrics()
        self.config = ServiceRunConfig()

        # for any kind of c2d
        self.outgoing_c2d_queue = queue.Queue()

        # for pingbacks
        self.outgoing_pingback_response_queue = queue.Queue()

        # for pairing
        self.incoming_pairing_request_queue = queue.Queue()
        self.pairing_list_lock = threading.Lock()
        self.paired_devices = {}

        # for reported property tracking
        self.incoming_twin_changes = queue.Queue()

        # for incoming eventHub events
        self.incoming_eventhub_event_queue = queue.Queue()

    def remove_device_from_pairing_list(self, device_id):
        """
        Function to unpair a device by removing it from the list of paired devices.

        Note: this doesn't do anything to inform the device that the pairing has occured,
        such as change the state of the device twin.  This is intentional since
        `remove_device_from_pairing_list` is a reactive function (called after the unpair has
        happened), and not a proactive function (called to initiate the unpairing).
        """
        with self.pairing_list_lock:
            if device_id in self.paired_devices:
                pairing_id = self.paired_devices[device_id].pairing_id
                logger.info(
                    "Unpairing {}. Removing it from paired device list".format(device_id),
                    extra=custom_props(device_id, pairing_id),
                )
                del self.paired_devices[device_id]

    def dispatch_incoming_messages_thread(self, worker_thread_info):
        """
        Function to dispatch incoming EventHub messages.  A different thread receives the messages
        and puts them into `incoming_eventhub_event_queue`.  This thread removes events from
        that queue and decides what to do with them, either by acting immediately or by putting
        the events into a different thread
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            try:
                event, partition_id = self.incoming_eventhub_event_queue.get(timeout=1)
            except queue.Empty:
                continue

            device_id = get_device_id_from_event(event)

            body = event.body_as_json()
            thief = body.get("thief", {})
            passed_run_id = thief.get("serviceRunId", None)
            pairing_id = thief.get("pairingId", None)
            cmd = thief.get("cmd", None)

            with self.pairing_list_lock:
                device_data = self.paired_devices.get(device_id, None)

            if get_message_source_from_event(event) == "twinChangeEvents":
                if (
                    body.get("properties", {})
                    .get("reported", {})
                    .get("thief", {})
                    .get("pairing", {})
                ):
                    self.incoming_pairing_request_queue.put(event)
                if device_data:
                    self.incoming_twin_changes.put(event)

            elif pairing_id and passed_run_id:

                with self.pairing_list_lock:
                    device_data = self.paired_devices.get(device_id, None)

                if device_data:
                    if cmd == "pingbackRequest":
                        pingback_type = thief.get("pingbackType", PingbackType.TELEMETRY_PINGBACK)

                        if pingback_type == PingbackType.TELEMETRY_PINGBACK:
                            logger.info(
                                "received telemetry pingback request from {} with pingbackId {}".format(
                                    device_id, thief["pingbackId"]
                                ),
                                extra=custom_props(device_id, pairing_id),
                            )
                            self.outgoing_pingback_response_queue.put(
                                Pingback(device_id=device_id, pingback_id=thief.get("pingbackId"))
                            )
                        else:
                            logger.warning(
                                "unknown pingback type: {}. Ignoring.".format(pingback_type),
                                extra=custom_props(device_id, pairing_id),
                            )
                    else:
                        logger.info(
                            "Unknown command received from {}: {}".format(device_id, body),
                            extra=custom_props(device_id, pairing_id),
                        )

    def receive_incoming_messages_thread(self, worker_thread_info):
        """
        Thread to listen on eventhub for events that we can handle.  This thread does minimal
        checking to see if an event is "interesting" before placing it into `incoming_eventhub_event_queue`.
        Any additional processing necessary to handle the events is done in different threads which
        process events from that queue.

        Right now, we service events on all partitions, but we could restrict this and have one
        (or more) service app(s) per partition.
        """

        def on_error(partition_context, error):
            logger.error("EventHub on_error: {}".format(error))

        def on_partition_initialize(partition_context):
            logger.warning("EventHub on_partition_initialize")

        def on_partition_close(partition_context, reason):
            logger.warning("EventHub on_partition_close: {}".format(reason))

        def on_event(partition_context, event):
            # TODO: find a better place to update the watchdog.  This will cause the service
            # app to fail if no messages are received for a long time.
            worker_thread_info.watchdog_epochtime = time.time()
            self.incoming_eventhub_event_queue.put((event, partition_context.partition_id))

        logger.info("starting EventHub receive")
        with self.eventhub_consumer_client:
            self.eventhub_consumer_client.receive(
                on_event,
                on_error=on_error,
                on_partition_initialize=on_partition_initialize,
                on_partition_close=on_partition_close,
            )

    def send_outgoing_c2d_messages_thread(self, worker_thread_info):
        """
        Thread which is responsible for sending C2D messages.  This is separated into it's own
        thead in order to centralize error handling and also because sending is a synchronous
        function and we don't want to block other threads while we're sending.
        """
        logger.info("starting thread")
        last_amqp_refresh_epochtime = time.time()

        while not (self.done.isSet() and self.outgoing_c2d_queue.empty()):
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            if (
                time.time() - last_amqp_refresh_epochtime
                > self.config.amqp_refresh_interval_in_seconds
            ):
                logger.warning(
                    "AMPQ credential approaching expiration.  Recreating registry manager"
                )
                with self.registry_manager_lock:
                    self.registry_manager.amqp_svc_client.disconnect_sync()
                    self.registry_manager = None
                    time.sleep(1)
                    self.registry_manager = IoTHubRegistryManager(iothub_connection_string)
                logger.info("New registry_manager object created")
                last_amqp_refresh_epochtime = time.time()

            try:
                (device_id, message, props) = self.outgoing_c2d_queue.get(timeout=1)
            except queue.Empty:
                pass
            else:
                with self.pairing_list_lock:
                    do_send = device_id in self.paired_devices

                if not do_send:
                    logger.warning(
                        "c2d found in outgoing queue for device {} which is not paired".format(
                            device_id
                        )
                    )
                else:
                    start = time.time()
                    try:
                        with self.registry_manager_lock:
                            self.registry_manager.send_c2d_message(device_id, message, props)
                    except Exception as e:
                        logger.error(
                            "send_c2d_messge to {} raised {}.  Forcing un-pair with device".format(
                                device_id, str(e)
                            ),
                            exc_info=e,
                        )
                        self.remove_device_from_pairing_list(device_id)
                    else:
                        end = time.time()
                        if end - start > 2:
                            logger.warning(
                                "Send throtttled.  Time delta={} seconds".format(end - start)
                            )

    def handle_pingback_request_thread(self, worker_thread_info):
        """
        Thread which is responsible for returning pingback response message to the
        device clients.  The various pingbacks are collected in `outgoing_pingback_response_queue`
        and this thread collects the pingbacks into batches to send at a regular interval.  This
        batching is required because we send many pingbacks per second and IoTHub will throttle
        C2d events if we send too many.  Sending fewer big messages is better than sending fewer
        small messages.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            pingbacks = {}
            while True:
                try:
                    pingback = self.outgoing_pingback_response_queue.get_nowait()
                except queue.Empty:
                    break
                if pingback.device_id not in pingbacks:
                    pingbacks[pingback.device_id] = []
                pingbacks[pingback.device_id].append({"pingbackId": pingback.pingback_id})

            if len(pingbacks):
                for device_id in pingbacks:

                    with self.pairing_list_lock:
                        if device_id in self.paired_devices:
                            device_data = self.paired_devices[device_id]
                        else:
                            device_data = None

                    if device_data:
                        pairing_id = device_data.pairing_id

                        logger.info(
                            "send pingback for device_id = {}: {}".format(
                                device_id, pingbacks[device_id]
                            ),
                            extra=custom_props(device_id, pairing_id),
                        )

                        message = json.dumps(
                            {
                                "thief": {
                                    "cmd": "pingbackResponse",
                                    "serviceRunId": run_id,
                                    "pairingId": pairing_id,
                                    "pingbacks": pingbacks[device_id],
                                }
                            }
                        )

                        self.outgoing_c2d_queue.put(
                            (
                                device_id,
                                message,
                                {"contentType": "application/json", "contentEncoding": "utf-8"},
                            )
                        )

            # TODO: this should be configurable
            # Too small and this causes C2D throttling
            time.sleep(15)

    def stop_c2d_message_sending(self, device_id):
        """
        Stop sending c2d messages for a specific device.
        """
        with self.pairing_list_lock:
            if device_id in self.paired_devices:
                device_data = self.paired_devices[device_id]
                device_data.test_c2d_enabled = False

    def start_c2d_message_sending(self, device_id, interval, max_filler_size):
        """
        Start sending c2d messages for a specific device.
        """

        with self.pairing_list_lock:
            if device_id in self.paired_devices:
                device_data = self.paired_devices[device_id]
                device_data.test_c2d_enabled = True
                device_data.c2d_interval_in_seconds = interval
                device_data.c2d_max_filler_size = max_filler_size
                device_data.c2d_next_message_epochtime = 0

    def test_c2d_thread(self, worker_thread_info):
        """
        Thread to send test C2D messages to devices which have enabled C2D testing
        """

        while not self.done.isSet():
            now = time.time()
            worker_thread_info.watchdog_epochtime = now
            if self.is_paused():
                time.sleep(1)
                continue

            with self.pairing_list_lock:
                devices = list(self.paired_devices.keys())

            for device_id in devices:
                with self.pairing_list_lock:
                    if device_id in self.paired_devices:
                        device_data = self.paired_devices[device_id]
                        # make sure c2d is enabled and make sure it's time to send the next c2d
                        if (
                            not device_data.test_c2d_enabled
                            or device_data.c2d_next_message_epochtime > time.time()
                        ):
                            device_data = None
                    else:
                        device_data = None

                if device_data:
                    # we can access device_data without holding pairing_list_lock because that lock protects the list
                    # but not the structures inside the list.
                    message = json.dumps(
                        {
                            "thief": {
                                "cmd": "testC2d",
                                "serviceRunId": run_id,
                                "pairingId": device_data.pairing_id,
                                "firstMessage": not device_data.first_c2d_sent,
                                "testC2dMessageIndex": device_data.next_c2d_message_index,
                                "filler": get_random_length_string(device_data.c2d_max_filler_size),
                            }
                        }
                    )

                    logger.info(
                        "Sending test c2d to {} with index {}".format(
                            device_id, device_data.next_c2d_message_index
                        ),
                        extra=custom_props(device_id, device_data.pairing_id),
                    )

                    device_data.next_c2d_message_index += 1
                    device_data.first_c2d_sent = True
                    device_data.c2d_next_message_epochtime = (
                        now + device_data.c2d_interval_in_seconds
                    )

                    self.outgoing_c2d_queue.put(
                        (
                            device_id,
                            message,
                            {"contentType": "application/json", "contentEncoding": "utf-8"},
                        )
                    )

            # loop through devices and see when our next outgoing c2d message is due to be sent.
            next_iteration_epochtime = now + 10
            with self.pairing_list_lock:
                for device_data in self.paired_devices.values():
                    if (
                        device_data.test_c2d_enabled
                        and device_data.c2d_next_message_epochtime
                        and device_data.c2d_next_message_epochtime < next_iteration_epochtime
                    ):
                        next_iteration_epochtime = device_data.c2d_next_message_epochtime

            if next_iteration_epochtime > now:
                time.sleep(next_iteration_epochtime - now)

    def pairing_thread(self, worker_thread_info):
        """
        Thread which responds to pairing events on the hub.  It does this by watching for changes
        to all device twin reported propreties under /thief/pairing.  If the change is
        interesting, the thread acts on it.  If not, it ignores it.

        A different thread is responsible for putting /thief/pairing changes into
        `incoming_pairing_requesat_queue`.  This thread just removes events from that queue
        and acts on them.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            try:
                event = self.incoming_pairing_request_queue.get(timeout=1)
            except queue.Empty:
                continue

            device_id = get_device_id_from_event(event)
            body = event.body_as_json()
            pairing = (
                body.get("properties", {}).get("reported", {}).get("thief", {}).get("pairing", {})
            )

            pairing_id = pairing.get("pairingId", None)
            requested_service_pool = pairing.get("requestedServicePool", None)
            selected_run_id = pairing.get("serviceRunId", None)

            logger.info(
                "Received pairing request for device {}: {}".format(device_id, pairing),
                extra=custom_props(device_id, pairing_id),
            )

            with self.pairing_list_lock:
                device_data = self.paired_devices.get(device_id, {})

            if requested_service_pool and requested_service_pool != service_pool:
                # Ignore events if the device is looking for a service pool which isn't us.
                logger.info(
                    "device {} requesting an app in a diffeent pool: {}".format(
                        device_id, requested_service_pool
                    ),
                    extra=custom_props(device_id, pairing_id),
                )
                continue

            # Ignore events if they don't even have a pairingId property.
            elif pairing_id:

                if device_data and selected_run_id != run_id:
                    # if device_data, that means we think we're paired.  If the properties
                    # tell us otherwise, we assume the pairing is no longer valid.
                    logger.info(
                        "Device {} deviced to pair with {}.  Unpairing".format(
                            device_id, selected_run_id
                        ),
                        extra=custom_props(device_id, pairing_id),
                    )
                    self.remove_device_from_pairing_list(device_id)

                elif selected_run_id == run_id:
                    # If the device has selected us, the pairing is complete.
                    logger.info(
                        "Device {} pairing complete".format(device_id),
                        extra=custom_props(device_id, pairing_id),
                    )

                    if not device_data:
                        # only create a new device_data structure if we don't already
                        # have one.
                        device_data = PerDeviceData(pairing_id, device_id)

                        with self.pairing_list_lock:
                            self.paired_devices[device_id] = device_data

                    # Tell the device that we've accepted the pairing.
                    desired = {
                        "thief": {
                            "pairing": {"acceptedPairing": "{},{}".format(pairing_id, run_id)}
                        }
                    }

                    logger.info(
                        "Marking acceptedPairing for device {}: {}".format(device_id, desired),
                        extra=custom_props(device_id, pairing_id),
                    )

                    with self.registry_manager_lock:
                        self.registry_manager.update_twin(
                            device_id, Twin(properties=TwinProperties(desired=desired)), "*"
                        )

                elif selected_run_id is None:
                    # If the device hasn't selected a serviceRunId value yet, we will try to
                    # pair with it.
                    logger.info(
                        "Device {} attempting to pair".format(device_id),
                        extra=custom_props(device_id, pairing_id),
                    )

                    desired = {
                        "thief": {
                            "pairing": {
                                "serviceRunId": run_id,
                                "pairingId": pairing_id,
                                "acceptedPairing": None,
                            }
                        }
                    }

                    with self.registry_manager_lock:
                        self.registry_manager.update_twin(
                            device_id, Twin(properties=TwinProperties(desired=desired)), "*"
                        )

                else:
                    # The device chose someone else since selected_run_id != run_id.
                    # Ignore this change.
                    pass

    def respond_to_test_content_properties(self, event):
        """
        Function to respond to changes to `testContent` reported properties.  `testContent`
        properties contain content, such as properties that contain random strings which are
        used to test various features.

        for `reportedPropertyTest` properties, this function will send pingback messages to the
        device when the property is added, and then again when the property is removed
        """
        device_id = get_device_id_from_event(event)
        with self.pairing_list_lock:
            device_data = self.paired_devices[device_id]

        test_content = (
            event.body_as_json()
            .get("properties", {})
            .get("reported", {})
            .get("thief", {})
            .get("testContent", {})
        )
        reported_property_test = test_content.get("reportedPropertyTest")

        for property_name in reported_property_test:
            if property_name.startswith("prop_"):
                property_value = reported_property_test[property_name]

                with device_data.reported_property_list_lock:
                    if property_value:
                        pingback_id = property_value["addPingbackId"]
                        device_data.reported_property_values[property_name] = property_value
                    else:
                        pingback_id = device_data.reported_property_values[property_name][
                            "removePingbackId"
                        ]
                        del device_data.reported_property_values[property_name]

                self.outgoing_pingback_response_queue.put(
                    Pingback(device_id=device_id, pingback_id=pingback_id)
                )

    def respond_to_test_control_properties(self, event):
        """
        Function to respond to changes to `testControl` reported properties.  `testControl`
        properties are used to control the operation of the test, such as enabling c2d testing.

        For `c2d` properties, this function can enable and disable c2d testing, and it can set
        various properties of the c2d messages that the device expects to receive.
        """
        device_id = get_device_id_from_event(event)

        test_control = (
            event.body_as_json()
            .get("properties", {})
            .get("reported", {})
            .get("thief", {})
            .get("testControl", {})
        )

        c2d = test_control.get("c2d")
        if c2d:
            send = c2d["send"]
            if send is False:
                self.stop_c2d_message_sending(device_id)
            elif send is True:
                interval = c2d["messageIntervalInSeconds"]
                max_filler_size = c2d["maxFillerSize"]
                self.start_c2d_message_sending(device_id, interval, max_filler_size)

    def dispatch_twin_change_thread(self, worker_thread_info):
        """
        Thread which goes through the queue of twin changes messages which have arrived and
        acts on the content.
        """
        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            try:
                event = self.incoming_twin_changes.get(timeout=1)
            except queue.Empty:
                continue

            device_id = get_device_id_from_event(event)
            with self.pairing_list_lock:
                device_data = self.paired_devices[device_id]

            logger.info(
                "Twin change for {}: {}".format(device_id, event.body_as_json()),
                extra=custom_props(device_id, device_data.pairing_id),
            )
            thief = event.body_as_json().get("properties", {}).get("reported", {}).get("thief", {})

            if thief.get("testContent"):
                self.respond_to_test_content_properties(event)
            if thief.get("testControl"):
                self.respond_to_test_control_properties(event)

            run_state = thief.get("sessionMetrics", {}).get("runState")
            if run_state and run_state != app_base.RUNNING:
                logger.info("Device {} no longer running.".format(device_id))
                self.remove_device_from_pairing_list(device_id)

    def main(self):

        self.metrics.run_start_utc = datetime.datetime.now(datetime.timezone.utc)
        self.metrics.run_state = app_base.RUNNING

        with self.registry_manager_lock:
            logger.info("creating registry manager")
            self.registry_manager = IoTHubRegistryManager(iothub_connection_string)

        self.eventhub_consumer_client = EventHubConsumerClient.from_connection_string(
            eventhub_connection_string, consumer_group=eventhub_consumer_group
        )

        threads_to_launch = [
            app_base.WorkerThreadInfo(
                self.receive_incoming_messages_thread, "receive_incoming_messages_thread"
            ),
            app_base.WorkerThreadInfo(
                self.dispatch_incoming_messages_thread, "dispatch_incoming_messages_thread"
            ),
            app_base.WorkerThreadInfo(self.pairing_thread, "pairing_thread"),
            app_base.WorkerThreadInfo(
                self.dispatch_twin_change_thread, "dispatch_twin_change_thread"
            ),
            app_base.WorkerThreadInfo(
                self.send_outgoing_c2d_messages_thread, "send_outgoing_c2d_messages_thread"
            ),
            app_base.WorkerThreadInfo(
                self.handle_pingback_request_thread, "handle_pingback_request_thread"
            ),
            app_base.WorkerThreadInfo(self.test_c2d_thread, "test_c2d_thread"),
        ]

        self.run_threads(threads_to_launch)

    def pre_shutdown(self):
        # close the eventhub consumer before shutting down threads.  This is necessary because
        # the "receive" function that we use to receive EventHub events is blocking and doesn't
        # have a timeout.
        logger.info("closing eventhub listener")
        self.eventhub_consumer_client.close()

    def disconnect(self):
        # nothing to do
        pass


if __name__ == "__main__":
    try:
        ServiceApp().main()
    except Exception as e:
        logger.error("App shutdown exception: {}".format(str(e)), exc_info=True)
        raise
    finally:
        # Flush azure monitor telemetry
        logging.shutdown()
