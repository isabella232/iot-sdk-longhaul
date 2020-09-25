# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import logging
import longhaul
import reaper
import time
import os
import queue
import threading
import json
import sys
import datetime
from concurrent.futures import ThreadPoolExecutor
from azure.iot.hub import IoTHubRegistryManager
from azure.iot.hub.models import Twin, TwinProperties
from azure.eventhub import EventHubConsumerClient

logger = logging.getLogger("thief.{}".format(__name__))

logging.basicConfig(level=logging.WARNING)
logging.getLogger("thief").setLevel(level=logging.INFO)

iothub_connection_string = os.environ["THIEF_SERVICE_CONNECTION_STRING"]
eventhub_connection_string = os.environ["THIEF_EVENTHUB_CONNECTION_STRING"]
eventhub_consumer_group = os.environ["THIEF_EVENTHUB_CONSUMER_GROUP"]
device_id = os.environ["THIEF_DEVICE_ID"]


class ServiceRunMetrics(longhaul.RunMetrics):
    """
    Object we use internally to keep track of how a the entire test is performing.
    """

    def __init__(self):
        super(ServiceRunMetrics, self).__init__()


class ServiceRunConfig(longhaul.RunConfig):
    """
    Object we use internally to keep track of how the entire test is configured.
    """

    def __init__(self):
        super(ServiceRunConfig, self).__init__()


def set_config(config):
    pass


def get_device_id_from_event(event):
    return event.message.annotations["iothub-connection-device-id".encode()].decode()


class ServiceApp(longhaul.LonghaulMixin, reaper.ReaperMixin):
    """
    Main application object
    """

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=128)
        self.done = threading.Event()
        self.registry_manager_lock = threading.Lock()
        self.registry_manager = None
        self.eventhub_consumer_client = None
        self.metrics = ServiceRunMetrics()
        self.config = ServiceRunConfig()
        self.pingback_events = queue.Queue()
        self.pingback_events_lock = threading.Lock()
        self.last_heartbeat = time.time()
        self.shutdown_event = threading.Event()
        self.c2d_send_queue = queue.Queue()
        self.next_heartbeat_id = 1000000

        self.start_reaper()

    def get_props_from_metrics(self):
        self.metrics.run_time = datetime.datetime.now() - self.metrics.run_start

        props = {
            "runStart": str(self.metrics.run_start),
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
                "requestsReceived": self.metrics.pingback_requests_received.get_count(),
                "responsesSent": self.metrics.pingback_responses_sent.get_count(),
            },
        }
        return props

    def update_initial_reported_properties(self):
        """
        Update reported properties at the start of a run
        """
        # TODO language and version bits

        twin = Twin()
        twin.properties = TwinProperties(
            desired={"thief": {"service": self.get_props_from_metrics()}}
        )

        logger.info("Setting initial thief properties: {}".format(twin.properties))
        with self.registry_manager_lock:
            self.registry_manager.update_twin(device_id, twin, "*")

    def eventhub_dispatcher_thread(self):
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

    def c2d_sender_thread(self):
        while not (self.done.isSet() and self.c2d_send_queue.empty()):
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
                                self.registry_manager = IoTHubRegistryManager(
                                    iothub_connection_string
                                )
                        else:
                            logger.error(
                                "send_c2d_messge raised {}.  Final error. Raising.".format(e),
                                exc_info=e,
                            )
                            raise

    def pingback_thread(self):
        """
        Thread which is responsible for returning pingback response message to the
        device client on the other side of the wall.
        """
        while not self.done.isSet():
            message_ids = []
            while True:
                try:
                    event = self.pingback_events.get_nowait()
                except queue.Empty:
                    break
                message_ids.append(event.body_as_json()["thief"]["messageId"])
                self.metrics.pingback_requests_received.increment()

            if len(message_ids):
                print("pingback for {}".format(message_ids))

                message = json.dumps(
                    {"thief": {"cmd": "pingbackResponse", "messageIds": message_ids}}
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

    def heartbeat_thread(self):
        """
        Thread which is responsible for sending heartbeat messages to the other side and
        also for making sure that heartbeat messages are received often enough
        """
        while not self.done.isSet():
            logger.info("sending heartbeat with id {}".format(self.next_heartbeat_id))
            message = json.dumps(
                {"thief": {"cmd": "heartbeat", "heartbeatId": self.next_heartbeat_id}}
            )
            self.next_heartbeat_id += 1

            self.c2d_send_queue.put(
                (
                    device_id,
                    message,
                    {"contentType": "application/json", "contentEncoding": "utf-8"},
                )
            )
            self.metrics.heartbeats_sent.increment()

            seconds_since_last_heartbeat = time.time() - self.last_heartbeat
            if seconds_since_last_heartbeat > self.config.heartbeat_failure_interval:
                raise Exception(
                    "No heartbeat received for {} seconds".format(seconds_since_last_heartbeat)
                )

            time.sleep(self.config.heartbeat_interval)

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

            twin = Twin()
            twin.properties = TwinProperties(
                desired={"thief": {"service": self.get_props_from_metrics()}}
            )

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

    def shutdown(self, error):
        """
        Shutdown the test.  This function can be called from any tread in order to trigger
        the shutdown of the test
        """
        if self.shutdown_event.isSet():
            logger.info("shutdown: event is already set.  ignorning")
        else:
            if error:
                self.metrics.run_state = longhaul.FAILED
                logger.error("shutdown: triggering error shutdown", exc_info=error)
            else:
                self.metrics.run_state = longhaul.COMPLETE
                logger.info("shutdown: triggering clean shutdown")
            self.metrics.exit_reason = str(error or "Clean shutdown")
            self.shutdown_event.set()

    def main(self):
        set_config(self.config)

        self.metrics.run_start = datetime.datetime.now()
        self.metrics.run_state = longhaul.RUNNING

        with self.registry_manager_lock:
            self.registry_manager = IoTHubRegistryManager(iothub_connection_string)

        self.wait_for_device_creation()
        self.update_initial_reported_properties()

        self.eventhub_consumer_client = EventHubConsumerClient.from_connection_string(
            eventhub_connection_string, consumer_group=eventhub_consumer_group
        )

        # spin up threads that are required for operation
        self.shutdown_on_future_exit(
            self.executor.submit(self.eventhub_dispatcher_thread), name="eventhub_dispatcher_thread"
        )
        self.shutdown_on_future_exit(
            self.executor.submit(self.c2d_sender_thread), name="c2d_sender_thread"
        )
        self.shutdown_on_future_exit(
            self.executor.submit(self.pingback_thread), name="pingback_thread"
        )
        self.shutdown_on_future_exit(
            self.executor.submit(self.heartbeat_thread), name="heartbeat_thread"
        )
        self.shutdown_on_future_exit(
            self.executor.submit(self.update_thief_properties_thread),
            name="update_thief_properties_thread",
        )

        # Live my life the way I want to until it's time for me to die.
        self.shutdown_event.wait(timeout=self.config.max_run_duration or None)

        logger.info("Run is complete.  Cleaning up.")

        # stop the reaper, then set the "done" event in order to stop all other threads.
        self.stop_reaper()
        self.done.set()

        # close the eventhub consumer before shutting down the executor.  This is necessary because
        # the "receive" function that we use to receive EventHub events is blocking and doesn't
        # have a timeout.
        logger.info("closing eventhub listener")
        self.eventhub_consumer_client.close()

        # wait for all other threads to exit.  This currently waits for all threads, and it can't
        # "give up" if some threads refuse to exit.
        self.executor.shutdown()

        logger.info("Done disconnecting.  Exiting")

        if self.metrics.run_state == longhaul.FAILED:
            sys.exit(1)


if __name__ == "__main__":
    ServiceApp().main()
