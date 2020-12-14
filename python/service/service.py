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
from concurrent.futures import ThreadPoolExecutor
from azure.iot.hub import IoTHubRegistryManager
import azure.iot.hub.constant
from azure.eventhub import EventHubConsumerClient
import azure_monitor
from utilities import get_random_string


logging.basicConfig(format="%(asctime)s %(levelname)s:%(message)s", level=logging.WARNING)
logging.getLogger("thief").setLevel(level=logging.INFO)
logging.getLogger("azure.iot").setLevel(level=logging.INFO)

logger = logging.getLogger("thief.{}".format(__name__))

# use os.environ[] for required environment variables
iothub_connection_string = os.environ["THIEF_SERVICE_CONNECTION_STRING"]
eventhub_connection_string = os.environ["THIEF_EVENTHUB_CONNECTION_STRING"]
eventhub_consumer_group = os.environ["THIEF_EVENTHUB_CONSUMER_GROUP"]
service_pool = os.environ["THIEF_SERVICE_POOL"]

# use os.getenv() for optional environment variables
run_id = os.getenv("THIEF_SERVICE_APP_RUN_ID")

if not run_id:
    run_id = str(uuid.uuid4())

cs_info = dict(map(str.strip, sub.split("=", 1)) for sub in iothub_connection_string.split(";"))

# configure our traces and events to go to Azure Monitor
azure_monitor.add_logging_properties(
    client_type="service",
    run_id=run_id,
    hub=cs_info["HostName"],
    sdk_version=azure.iot.hub.constant.VERSION,
    pool_id=service_pool,
)
event_logger = azure_monitor.get_event_logger()
azure_monitor.log_to_azure_monitor("thief")
azure_monitor.log_to_azure_monitor("azure")
azure_monitor.log_to_azure_monitor("uamqp")


# TODO: move this to thief_constants.py along with RUNNING, etc.
class PingbackType(object):
    TELEMETRY_PINGBACK = "telemetry"
    ADD_REPORTED_PROPERTY_PINGBACK = "add_reported"
    REMOVE_REPORTED_PROPERTY_PINGBACK = "remove_reported"


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
    def __init__(self, pairing_id):
        # For Pairing
        self.pairing_id = pairing_id
        self.pairing_complete = False

        # For testing C2D
        self.test_c2d_enabled = False
        self.first_c2d_sent = False
        self.next_c2d_message_index = 0
        self.c2d_interval_in_seconds = 0
        self.c2d_filler_size = 0
        self.c2d_next_message_epochtime = 0

        # for verifying reported property changes
        self.reported_property_list_lock = threading.Lock()
        self.reported_property_add_list = {}
        self.reported_property_remove_list = {}


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

        # How often to check device twins to make sure we're still paired and to look for reported property changes
        self.check_device_twin_interval_in_seconds = 30

        # How often to refresh the AMQP connection.  Necessary because of a 10 minute hardcoded credential interval
        self.amqp_refresh_interval_in_seconds = 9 * 60


def get_device_id_from_event(event):
    return event.message.annotations["iothub-connection-device-id".encode()].decode()


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
        self.incoming_pingback_request_queue = queue.Queue()

        # for pairing
        self.incoming_pairing_request_queue = queue.Queue()
        self.pairing_list_lock = threading.Lock()
        self.paired_devices = {}

    def add_device_to_pairing_list(self, event):
        device_id = get_device_id_from_event(event)
        logger.info(
            "pairing request received for device {}. Queueing.".format(device_id),
            extra=custom_props(device_id),
        )
        self.incoming_pairing_request_queue.put(event)

    def handle_pairing_request_thread(self, worker_thread_info):
        """
        Thread responsible for responding to pairing requests from devices.
        The pairing handshake is documented in device.py inside the
        `pair_with_service_instance` function.

        This functionality lives in it's own thread because it involves some
        back-and-forth communication with the device, and this can take a while.
        Since this can happen any time, we don't want to block any other functionality
        while we do this.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()

            # See if we have any pairingRequest events waiting in the queue
            try:
                event = self.incoming_pairing_request_queue.get(timeout=5)
            except queue.Empty:
                event = None

            if event:
                device_id = get_device_id_from_event(event)

                body = event.body_as_json()
                thief = body.get("thief", None)
                if not thief:
                    continue

                pairing_id = thief.get("pairingId", None)
                if not pairing_id:
                    continue

                logger.info(
                    "attempting to pair with device {}".format(device_id),
                    extra=custom_props(device_id, pairing_id),
                )

                # Maybe the device app wants an instance from a specific pool.  If we're not
                # in that pool. we can stop
                requested_service_pool = thief.get("requestedServicePool", None)
                if requested_service_pool and requested_service_pool != service_pool:
                    logger.info(
                        "device {} requesting an app in a diffeent pool: {}".format(
                            device_id, thief["requestedServicePool"]
                        ),
                        extra=custom_props(device_id, pairing_id),
                    )
                    continue

                # Send a pairingResponse to indicate that we're available.  If we see the device
                # using our run_id, then we know that it chose us.

                # add to paired_devices to the outgoing thread will send it.
                device_data = PerDeviceData(pairing_id)
                with self.pairing_list_lock:
                    self.paired_devices[device_id] = device_data

                message = json.dumps(
                    {
                        "thief": {
                            "cmd": "pairingResponse",
                            "serviceAppRunId": run_id,
                            "pairingId": pairing_id,
                        }
                    }
                )

                logger.info(
                    "Putting pairing request into outgoing c2d queue",
                    extra=custom_props(device_id, pairing_id),
                )
                self.outgoing_c2d_queue.put(
                    (
                        device_id,
                        message,
                        {"contentType": "application/json", "contentEncoding": "utf-8"},
                    )
                )

                # at this point, the device might choose us or it might not, but we've
                # tried.

    def check_device_twin_thread(self, worker_thread_info):
        """
        Thread which queries the device at a regular interval
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            with self.pairing_list_lock:
                list_copy = self.paired_devices.copy()

            for device_id in list_copy:
                device_data = list_copy[device_id]
                pairing_id = device_data.pairing_id

                with self.registry_manager_lock:
                    twin = self.registry_manager.get_twin(device_id)

                thief = twin.properties.reported.get("thief", None)
                session_metrics = None
                if thief:
                    session_metrics = thief.get("sessionMetrics", None)
                if not session_metrics:
                    logger.info(
                        "session metrics for {} is empty.  ignoring twin.".format(device_id),
                        extra=custom_props(device_id, pairing_id),
                    )
                elif session_metrics["runState"] != app_base.RUNNING:
                    if session_metrics["pairingId"] != pairing_id:
                        logger.warning(
                            "device {} has sessionMetrics tagged an old session id {}:  Ignoring twin.".format(
                                device_id, session_metrics["pairingId"]
                            ),
                            extra=custom_props(device_id, pairing_id),
                        )
                    else:
                        logger.warning(
                            "device {} is not running.  Removing.".format(device_id),
                            extra=custom_props(device_id, pairing_id),
                        )
                        self.unpair_device(device_id)
                else:
                    logger.info(
                        "device {} is still paired and alive.  Continuing to monitor".format(
                            device_id
                        ),
                        extra=custom_props(device_id, pairing_id),
                    )

                    # check for added and removed properties
                    property_pingbacks = []

                    with device_data.reported_property_list_lock:
                        new_add_list = {}
                        new_remove_list = {}
                        property_test_list = twin.properties.reported["thief"].get(
                            "propertyTest", {}
                        )

                        for key in device_data.reported_property_add_list:
                            if key in property_test_list:
                                property_pingbacks.append(
                                    {"pingbackId": device_data.reported_property_add_list[key]}
                                )
                            else:
                                new_add_list[key] = device_data.reported_property_add_list[key]

                        for key in device_data.reported_property_remove_list:
                            if key not in property_test_list:
                                property_pingbacks.append(
                                    {"pingbackId": device_data.reported_property_remove_list[key]}
                                )
                            else:
                                new_remove_list[key] = device_data.reported_property_remove_list[
                                    key
                                ]

                        device_data.reported_property_add_list = new_add_list
                        device_data.reported_property_remove_list = new_remove_list

                    if property_pingbacks:
                        message = json.dumps(
                            {
                                "thief": {
                                    "cmd": "pingbackResponse",
                                    "serviceRunAppId": run_id,
                                    "pairingId": pairing_id,
                                    "pingbacks": property_pingbacks,
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

            time.sleep(self.config.check_device_twin_interval_in_seconds)

    def unpair_device(self, device_id):
        # TODO: this isn't very robust.  There should be some way to tell the device that we're done.
        with self.pairing_list_lock:
            if device_id in self.paired_devices:
                pairing_id = self.paired_devices[device_id].pairing_id
                logger.info(
                    "Unpairing {}. Removing it from paired device list".format(device_id),
                    extra=custom_props(device_id, pairing_id),
                )
                del self.paired_devices[device_id]

    def implicitely_update_paired_device_list(self, device_id, pairing_id, service_app_run_id):
        """
        update the pairing status of a device based on pairing_id and service_app_run_id,
        probably received via telemetry message
        """

        with self.pairing_list_lock:
            # See if this is coming from a device_id that we're interested in.  Do this
            # in the lock because we might be updating the paired device list.
            if device_id in self.paired_devices:
                if (
                    pairing_id == self.paired_devices[device_id].pairing_id
                    and service_app_run_id == run_id
                ):
                    if not self.paired_devices[device_id].pairing_complete:
                        logger.info(
                            "Device {} has decided to pair with us".format(device_id),
                            extra=custom_props(device_id, pairing_id),
                        )
                        self.paired_devices[device_id].pairing_complete = True
                else:
                    logger.info(
                        "Device {} has decided to pair with a different service instance:  {}".format(
                            device_id, service_app_run_id
                        ),
                        extra=custom_props(device_id, pairing_id),
                    )
                    del self.paired_devices[device_id]

    def dispatch_incoming_messages_thread(self, worker_thread_info):
        """
        Thread to listen on eventhub for events that we can handle.  Right now, we service
        events on all partitions, but we could restrict this and have one (or more) service app(s)
        per partition.
        """

        def on_error(partition_context, error):
            logger.warning("on_error: {}".format(error))

        def on_partition_initialize(partition_context):
            logger.warning("on_partition_initialize")

        def on_partition_close(partition_context, reason):
            logger.warning("on_partition_close: {}".format(reason))

        def on_event(partition_context, event):
            # TODO: find a better place to update the watchdog.  This will cause the service
            # app to fail if no messages are received for a long time.
            worker_thread_info.watchdog_epochtime = time.time()

            body = event.body_as_json()
            thief = body.get("thief")
            cmd = thief.get("cmd") if thief else None

            service_app_run_id = thief.get("serviceAppRunId") if thief else None
            pairing_id = thief.get("pairingId") if thief else None

            device_id = get_device_id_from_event(event)

            if cmd == "pairingRequest":
                logger.info(
                    "Got pairingRequest command for device {}".format(device_id),
                    extra=custom_props(device_id, pairing_id),
                )
                # If we're re-connecting a device, wipe out data from the previous relationship
                self.unpair_device(device_id)
                self.add_device_to_pairing_list(event)

            elif pairing_id and service_app_run_id:
                self.implicitely_update_paired_device_list(
                    device_id=device_id,
                    pairing_id=pairing_id,
                    service_app_run_id=service_app_run_id,
                )

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
                            self.incoming_pingback_request_queue.put(
                                (event, partition_context.partition_id)
                            )
                        elif pingback_type == PingbackType.ADD_REPORTED_PROPERTY_PINGBACK:
                            logger.info(
                                "Adding add property pingback check for {} from {} with pingbackId {}".format(
                                    thief["propertyName"], device_id, thief["pingbackId"]
                                ),
                                extra=custom_props(device_id, pairing_id),
                            )
                            with device_data.reported_property_list_lock:
                                device_data.reported_property_add_list[
                                    thief["propertyName"]
                                ] = thief["pingbackId"]
                        elif pingback_type == PingbackType.REMOVE_REPORTED_PROPERTY_PINGBACK:
                            logger.info(
                                "Adding remove property pingback check for {} from {} with pingbackId {}".format(
                                    thief["propertyName"], device_id, thief["pingbackId"]
                                ),
                                extra=custom_props(device_id, pairing_id),
                            )
                            with device_data.reported_property_list_lock:
                                device_data.reported_property_remove_list[
                                    thief["propertyName"]
                                ] = thief["pingbackId"]
                        else:
                            logger.warning(
                                "unknown pingback type: {}. Ignoring.".format(pingback_type),
                                extra=custom_props(device_id, pairing_id),
                            )
                    elif cmd == "startC2dMessageSending":
                        self.start_c2d_message_sending(device_id, thief)
                    else:
                        logger.info(
                            "Unknown command received from {}: {}".format(device_id, body),
                            extra=custom_props(device_id, pairing_id),
                        )

        logger.info("starting receive")
        with self.eventhub_consumer_client:
            self.eventhub_consumer_client.receive(
                on_event,
                on_error=on_error,
                on_partition_initialize=on_partition_initialize,
                on_partition_close=on_partition_close,
            )

    def send_outgoing_c2d_messages_thread(self, worker_thread_info):
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
                    del self.registry_manager
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
                            "send_c2d_messge to {} raised {}.  Final error. Forcing un-pair with device".format(
                                device_id, str(e)
                            ),
                            exc_info=e,
                        )
                        self.unpair_device(device_id)
                    else:
                        end = time.time()
                        if end - start > 2:
                            logger.warning(
                                "Send throtttled.  Time delta={} seconds".format(end - start)
                            )

    def handle_pingback_request_thread(self, worker_thread_info):
        """
        Thread which is responsible for returning pingback response message to the
        device client on the other side of the wall.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_epochtime = time.time()
            if self.is_paused():
                time.sleep(1)
                continue

            pingbacks = {}
            while True:
                try:
                    (event, partition_id) = self.incoming_pingback_request_queue.get_nowait()
                except queue.Empty:
                    break
                device_id = get_device_id_from_event(event)
                thief = event.body_as_json()["thief"]
                pingback_id = thief["pingbackId"]
                if device_id not in pingbacks:
                    pingbacks[device_id] = []
                pingbacks[device_id].append(
                    {"pingbackId": pingback_id, "partitionId": partition_id, "offset": event.offset}
                )

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
                                    "serviceRunAppId": run_id,
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

    def start_c2d_message_sending(self, device_id, thief):
        """
        Start sending c2d messages for a specific device.

        NOTE: the caller is holding a lock when this is called.  Do not call any external
        functions from here.
        """

        with self.pairing_list_lock:
            if device_id in self.paired_devices:
                device_data = self.paired_devices[device_id]
                device_data.test_c2d_enabled = True
                device_data.c2d_interval_in_seconds = thief["messageIntervalInSeconds"]
                device_data.c2d_filler_size = thief["fillerSize"]
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
                                "serviceAppRunId": run_id,
                                "pairingId": device_data.pairing_id,
                                "firstMessage": not device_data.first_c2d_sent,
                                "testC2dMessageIndex": device_data.next_c2d_message_index,
                                "filler": get_random_string(device_data.c2d_filler_size),
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
            next_iteration_epochtime = now + 1
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

    def main(self):

        self.metrics.run_start_utc = datetime.datetime.now(datetime.timezone.utc)
        self.metrics.run_state = app_base.RUNNING

        with self.registry_manager_lock:
            self.registry_manager = IoTHubRegistryManager(iothub_connection_string)

        self.eventhub_consumer_client = EventHubConsumerClient.from_connection_string(
            eventhub_connection_string, consumer_group=eventhub_consumer_group
        )

        threads_to_launch = [
            app_base.WorkerThreadInfo(
                self.dispatch_incoming_messages_thread, "dispatch_incoming_messages_thread"
            ),
            app_base.WorkerThreadInfo(
                self.handle_pairing_request_thread, "handle_pairing_request_thread"
            ),
            app_base.WorkerThreadInfo(self.check_device_twin_thread, " check_device_twin_thread"),
            app_base.WorkerThreadInfo(
                self.send_outgoing_c2d_messages_thread, "send_outgoing_c2d_messages_thread"
            ),
            app_base.WorkerThreadInfo(
                self.handle_pingback_request_thread, "handle_pingback_request_thread"
            ),
            app_base.WorkerThreadInfo(self.test_c2d_thread, "test_c2d_thread"),
        ]

        self.run_threads(threads_to_launch)

        # 60 seconds to let app insights data flush
        time.sleep(60)

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

    ServiceApp().main()
