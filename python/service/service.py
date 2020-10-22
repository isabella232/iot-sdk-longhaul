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
from azure.iot.hub.models import Twin, TwinProperties
import azure.iot.hub.constant
from azure.eventhub import EventHubConsumerClient
import azure_monitor
from measurement import ThreadSafeCounter
from azure.iot.device.common.auth import connection_string


logging.basicConfig(level=logging.WARNING)
logging.getLogger("thief").setLevel(level=logging.DEBUG)

logger = logging.getLogger("thief.{}".format(__name__))

# use os.environ[] for required environment variables
iothub_connection_string = os.environ["THIEF_SERVICE_CONNECTION_STRING"]
eventhub_connection_string = os.environ["THIEF_EVENTHUB_CONNECTION_STRING"]
eventhub_consumer_group = os.environ["THIEF_EVENTHUB_CONSUMER_GROUP"]

# use os.getenv() for optional environment variables
run_id = os.getenv("THIEF_SERVICE_APP_RUN_ID")

if not run_id:
    run_id = str(uuid.uuid4())

cs = connection_string.ConnectionString(iothub_connection_string)

# configure our traces and events to go to Azure Monitor
azure_monitor.configure_logging(
    client_type="service",
    run_id=run_id,
    hub=cs["HostName"],
    sdk_version=azure.iot.hub.constant.VERSION,
)
event_logger = azure_monitor.get_event_logger()
azure_monitor.log_to_azure_monitor("thief")
azure_monitor.log_to_azure_monitor("azure")
azure_monitor.log_to_azure_monitor("uamqp")


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

        self.pingback_requests_received = ThreadSafeCounter()
        self.pingback_responses_sent = ThreadSafeCounter()


class ServiceRunConfig(object):
    """
    Object we use internally to keep track of how the entire test is configured.
    """

    def __init__(self):
        self.max_run_duration = 0

        self.thief_property_update_interval = 60
        self.watchdog_failure_interval = 300


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
        self.shutdown_event = threading.Event()
        self.metrics = ServiceRunMetrics()
        self.config = ServiceRunConfig()

        self.paired_device_ids = []

        # for any kind of c2d
        self.c2d_send_queue = queue.Queue()

        # for pingbacks
        self.pingback_events = queue.Queue()
        self.pingback_events_lock = threading.Lock()

        # for bootstrapping
        self.bootstrap_queue = queue.Queue()

    def do_bootstrap_device(self, event):
        device_id = get_device_id_from_event(event)
        logger.debug("queueing bootstrap for device {}".format(device_id))
        self.bootstrap_queue.put(event)

    def bootstrapper_thread(self, worker_thread_info):
        """
        Thread responsible for responding to bootstrap requests from devices.
        The bootstrap handshake is documented in device.py inside the
        `pair_with_service_instance` function.

        This functionality lives in it's own thread because it involves some
        back-and-forth communication with the device, and this can take a while.
        Since this can happen any time, we don't want to block any other functionality
        while we do this.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()

            try:
                event = self.bootstrap_queue.get_nowait()
            except queue.Empty:
                event = None

            device_id = get_device_id_from_event(event) if event else None
            if device_id:
                logger.info("bootstrapper thread bootstrapping device {}".format(device_id))
                # If we get a bootstrap request from a device, get the twin to see what
                # it wants.
                with self.registry_manager_lock:
                    twin = self.registry_manager.get_twin(device_id)
                if "thief" in twin.properties.reported:
                    thief = twin.properties.reported["thief"]

                    # check to see if the device already selected a difference service app.
                    # if so, we don't need to continue.
                    if thief.get("serviceAppRunId", None):
                        logger.info(
                            "device {} already using serviceAppRunId {}".format(
                                device_id, thief["serviceAppRunId"]
                            )
                        )
                    else:
                        # Maybe the device app wants a specific service app.  If we're not that
                        # instance, we can stop.
                        requested_service_run_app_id = thief.get("requestedServiceAppRunId", None)
                        if requested_service_run_app_id and requested_service_run_app_id != run_id:
                            logger.info(
                                "device {} requesting different service app: {}".format(
                                    device_id, thief["requestedServiceAppRunId"]
                                )
                            )
                        else:
                            # the device is either asking for us specifically, or it doesn't care what service app it uses.
                            # Set the serviceAppRunId desired property to tell the device that we're availble.
                            # Maybe it will choose us, maybe it won't
                            twin = Twin()
                            twin.properties = TwinProperties(
                                desired={"thief": {"serviceAppRunId": run_id}}
                            )
                            logger.info(
                                "Setting twin properties to set serviceAppRunId for {}: {}".format(
                                    device_id, twin.properties
                                )
                            )
                            with self.registry_manager_lock:
                                self.registry_manager.update_twin(device_id, twin, "*")

                            # At this point, maybe the device uses us, or maybe it uses someone else.
                            # Either way, we don't care.  If we start seeing messages from the device
                            # that tag us, we'll respond.

                            # In the future, we could look at the device's reported properties to
                            # see if it chose us, but there's no reason to do this yet.
                else:
                    logger.warning(
                        "trying to bootstrap with invalid device twin {}: {}".format(
                            device_id, twin
                        )
                    )
            else:
                # nothing left in the queue.  wait a second and try again.
                # note: If we just handled a device_id, we don't sleep because there might be
                # something left in the queue.
                time.sleep(1)

    def eventhub_dispatcher_thread(self, worker_thread_info):
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
            worker_thread_info.watchdog_time = time.time()
            body = event.body_as_json()
            thief = body.get("thief")
            cmd = thief.get("cmd") if thief else None

            device_id = get_device_id_from_event(event)
            if cmd == "bootstrap":
                logger.debug("Got bootstrap command for device {}".format(device_id))
                self.do_bootstrap_device(event)

            elif event.properties.get(b"serviceAppRunId", b"").decode("utf-8") == run_id:
                if cmd == "pingback":
                    with self.pingback_events_lock:
                        self.pingback_events.put(event)
                else:
                    logger.info("Unknown command received from {}: {}".format(device_id, body))

        logger.info("starting receive")
        with self.eventhub_consumer_client:
            self.eventhub_consumer_client.receive(
                on_event,
                on_error=on_error,
                on_partition_initialize=on_partition_initialize,
                on_partition_close=on_partition_close,
            )

    def c2d_sender_thread(self, worker_thread_info):
        while not (self.done.isSet() and self.c2d_send_queue.empty()):
            worker_thread_info.watchdog_time = time.time()
            try:
                (device_id, message, props) = self.c2d_send_queue.get(timeout=1)
            except queue.Empty:
                pass
            else:
                tries_left = 3
                success = False
                while tries_left and not success:
                    tries_left -= 1
                    try:
                        with self.registry_manager_lock:
                            self.registry_manager.send_c2d_message(device_id, message, props)
                            success = True
                    except Exception as e:
                        if tries_left:
                            logger.error(
                                "send_c2d_messge raised {}.  Reconnecting and trying again.".format(
                                    e
                                ),
                                exc_info=e,
                            )
                            with self.registry_manager_lock:
                                del self.registry_manager
                                time.sleep(1)
                                self.registry_manager = IoTHubRegistryManager(
                                    iothub_connection_string
                                )
                        else:
                            logger.error(
                                "send_c2d_messge raised {}.  Final error. Raising.".format(e),
                                exc_info=e,
                            )
                            raise

    def pingback_thread(self, worker_thread_info):
        """
        Thread which is responsible for returning pingback response message to the
        device client on the other side of the wall.
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()
            pingback_ids = {}
            while True:
                try:
                    event = self.pingback_events.get_nowait()
                except queue.Empty:
                    break
                device_id = get_device_id_from_event(event)
                thief = event.body_as_json()["thief"]
                pingback_id = thief["pingbackId"]
                if device_id not in pingback_ids:
                    pingback_ids[device_id] = []
                pingback_ids[device_id].append(pingback_id)

                self.metrics.pingback_requests_received.increment()

            if len(pingback_ids):
                for device_id in pingback_ids:
                    logger.debug(
                        "send pingback for device_id = {}: {}".format(
                            device_id, pingback_ids[device_id]
                        )
                    )

                    message = json.dumps(
                        {
                            "thief": {
                                "cmd": "pingbackResponse",
                                "pingbackIds": pingback_ids[device_id],
                            }
                        }
                    )

                    self.c2d_send_queue.put(
                        (
                            device_id,
                            message,
                            {"contentType": "application/json", "contentEncoding": "utf-8"},
                        )
                    )

                    self.metrics.pingback_responses_sent.increment()

            time.sleep(1)

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
                self.eventhub_dispatcher_thread, "eventhub_dispatcher_thread"
            ),
            app_base.WorkerThreadInfo(self.bootstrapper_thread, "bootstrapper_thread"),
            app_base.WorkerThreadInfo(self.c2d_sender_thread, "c2d_sender_thread"),
            app_base.WorkerThreadInfo(self.pingback_thread, "pingback_thread"),
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
    ServiceApp().main()
