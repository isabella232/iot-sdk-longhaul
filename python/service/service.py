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
from concurrent.futures import ThreadPoolExecutor
from azure.iot.hub import IoTHubRegistryManager
from azure.iot.hub.models import Twin, TwinProperties
from azure.eventhub import EventHubConsumerClient
from azure_monitor import get_event_logger, log_to_azure_monitor
from measurement import ThreadSafeCounter
import azure.iot.hub.constant

logger = logging.getLogger("thief.{}".format(__name__))
event_logger = get_event_logger("service")

logging.basicConfig(level=logging.WARNING)
logging.getLogger("thief").setLevel(level=logging.INFO)

iothub_connection_string = os.environ["THIEF_SERVICE_CONNECTION_STRING"]
eventhub_connection_string = os.environ["THIEF_EVENTHUB_CONNECTION_STRING"]
eventhub_consumer_group = os.environ["THIEF_EVENTHUB_CONSUMER_GROUP"]
device_id = os.environ["THIEF_DEVICE_ID"]

log_to_azure_monitor("thief", "service")
log_to_azure_monitor("azure")
log_to_azure_monitor("uamqp")


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

        self.heartbeats_sent = ThreadSafeCounter()
        self.heartbeats_received = ThreadSafeCounter()

        self.pingback_requests_received = ThreadSafeCounter()
        self.pingback_responses_sent = ThreadSafeCounter()


class ServiceRunConfig(object):
    """
    Object we use internally to keep track of how the entire test is configured.
    """

    def __init__(self):
        self.max_run_duration = 0

        self.heartbeat_interval = 10
        self.heartbeat_failure_interval = 300
        self.thief_property_update_interval = 60
        self.watchdog_failure_interval = 300


def get_device_id_from_event(event):
    return event.message.annotations["iothub-connection-device-id".encode()].decode()


class ServiceApp(app_base.AppBase):
    """
    Main application object
    """

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=128)
        self.done = threading.Event()
        self.registry_manager_lock = threading.Lock()
        self.registry_manager = None
        self.eventhub_consumer_client = None
        self.shutdown_event = threading.Event()
        self.metrics = ServiceRunMetrics()
        self.config = ServiceRunConfig()

        # for any kind of c2d
        self.c2d_send_queue = queue.Queue()

        # futor pingbacks
        self.pingback_events = queue.Queue()
        self.pingback_events_lock = threading.Lock()

        # for heartbeats
        self.last_heartbeat = time.time()
        self.next_heartbeat_id = 10000
        self.first_heartbeat_received = threading.Event()

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
                "requestsReceived": self.metrics.pingback_requests_received.get_count(),
                "responsesSent": self.metrics.pingback_responses_sent.get_count(),
            },
        }
        return props

    def update_initial_reported_properties(self):
        """
        Update reported properties at the start of a run
        """

        props = {"thief": {"service": self.get_longhaul_metrics()}}
        props["thief"]["service"].update(
            self.get_fixed_system_metrics(azure.iot.hub.constant.VERSION)
        )
        props["thief"]["service"].update(self.get_variable_system_metrics())

        twin = Twin()
        twin.properties = TwinProperties(desired=props)

        logger.info("Setting initial thief properties: {}".format(twin.properties))
        with self.registry_manager_lock:
            self.registry_manager.update_twin(device_id, twin, "*")

    def eventhub_dispatcher_thread(self, worker_thread_info):
        """
        Thread to listen on eventhub for events which are targeted to our device_id
        """

        def on_error(partition_context, error):
            logger.warning("on_error: {}".format(error))

        def on_partition_initialize(partition_context):
            logger.warning("on_partition_initialize")

        def on_partition_close(partition_context, reason):
            logger.warning("on_partition_close: {}".format(reason))

        def on_event(partition_context, event):
            worker_thread_info.watchdog_time = time.time()
            if get_device_id_from_event(event) == device_id:
                body = event.body_as_json()
                thief = body.get("thief")
                cmd = thief.get("cmd") if thief else None

                if cmd == "heartbeat":
                    logger.info(
                        "heartbeat received.  heartbeatId={}".format(thief.get("heartbeatId"))
                    )
                    self.last_heartbeat = time.time()
                    self.metrics.heartbeats_received.increment()
                    if not self.first_heartbeat_received.isSet():
                        self.first_heartbeat_received.set()

                elif cmd == "pingback":
                    with self.pingback_events_lock:
                        self.pingback_events.put(event)

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
            pingback_ids = []
            while True:
                try:
                    event = self.pingback_events.get_nowait()
                except queue.Empty:
                    break
                thief = event.body_as_json()["thief"]
                pingback_ids.append(thief["pingbackId"])
                self.metrics.pingback_requests_received.increment()

            if len(pingback_ids):
                logger.debug("send pingback for {}".format(pingback_ids))

                message = json.dumps(
                    {"thief": {"cmd": "pingbackResponse", "pingbackIds": pingback_ids}}
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

    def heartbeat_thread(self, worker_thread_info):
        """
        Thread which is responsible for sending heartbeat messages to the other side and
        also for making sure that heartbeat messages are received often enough
        """

        while not self.done.isSet():
            worker_thread_info.watchdog_time = time.time()

            # Don't start sending heartbeats until we receive our first heartbeat from
            # the other side
            if self.first_heartbeat_received.is_set():
                logger.info("sending heartbeat with id {}".format(self.next_heartbeat_id))

                message = json.dumps(
                    {"thief": {"cmd": "heartbeat", "heartbeatId": self.next_heartbeat_id}}
                )

                self.c2d_send_queue.put(
                    (
                        device_id,
                        message,
                        {"contentType": "application/json", "contentEncoding": "utf-8"},
                    )
                )
                self.next_heartbeat_id += 1
                self.metrics.heartbeats_sent.increment()

            seconds_since_last_heartbeat = time.time() - self.last_heartbeat
            if seconds_since_last_heartbeat > self.config.heartbeat_failure_interval:
                raise Exception(
                    "No heartbeat received for {} seconds".format(seconds_since_last_heartbeat)
                )

            time.sleep(self.config.heartbeat_interval)

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

            props = {"thief": {"service": self.get_longhaul_metrics()}}
            props["thief"]["service"].update(self.get_variable_system_metrics())

            twin = Twin()
            twin.properties = TwinProperties(desired=props)

            logger.info("Updating thief properties: {}".format(twin.properties))
            with self.registry_manager_lock:
                self.registry_manager.update_twin(device_id, twin, "*")

            self.done.wait(self.config.thief_property_update_interval)

    def wait_for_device_creation(self):
        """
        Make sure our device exists before we continue.  Since the device app and this
        app are both starting up around the same time, it's possible that the device app
        hasn't used DPS to crate the device yet.  Give up after 60 seconds
        """
        start_time = time.time()
        device = None
        while not device and (time.time() - start_time) < 60:
            try:
                with self.registry_manager_lock:
                    device = self.registry_manager.get_device(device_id)
            except Exception as e:
                logger.info("get_device returned: {}".format(e))
                try:
                    if e.response.status_code == 404:
                        # a 404.  sleep and try again
                        logger.info("Sleeping for 10 seconds before trying again")
                        time.sleep(10)
                    else:
                        # not a 404.  Raise it.
                        raise e
                except AttributeError:
                    # an AttributeError means this wasn't an msrest error.  raise it.
                    raise e

        if not device:
            raise Exception("Device does not exist.  Cannot continue")

    def main(self):

        self.metrics.run_start_utc = datetime.datetime.now(datetime.timezone.utc)
        self.metrics.run_state = app_base.RUNNING

        with self.registry_manager_lock:
            self.registry_manager = IoTHubRegistryManager(iothub_connection_string)

        self.wait_for_device_creation()
        self.update_initial_reported_properties()

        self.eventhub_consumer_client = EventHubConsumerClient.from_connection_string(
            eventhub_connection_string, consumer_group=eventhub_consumer_group
        )

        threads_to_launch = [
            app_base.WorkerThreadInfo(
                self.eventhub_dispatcher_thread, "eventhub_dispatcher_thread"
            ),
            app_base.WorkerThreadInfo(self.c2d_sender_thread, "c2d_sender_thread"),
            app_base.WorkerThreadInfo(self.pingback_thread, "pingback_thread"),
            app_base.WorkerThreadInfo(self.heartbeat_thread, "heartbeat_thread"),
            app_base.WorkerThreadInfo(
                self.update_thief_properties_thread, "update_thief_properties_thread"
            ),
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
