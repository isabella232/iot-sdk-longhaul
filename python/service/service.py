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
from concurrent.futures import ThreadPoolExecutor
from azure.iot.hub import IoTHubRegistryManager
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
        self.registry_manager = None
        self.eventhub_consumer_client = None
        self.metrics = ServiceRunMetrics()
        self.config = ServiceRunConfig()
        self.pingback_events = queue.Queue()
        self.pingback_events_lock = threading.Lock()
        self.last_heartbeat = time.time()
        self.shutdown_event = threading.Event()

        self.start_reaper()

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
                if "thiefHeartbeat" in body:
                    logger.info("heartbeat received")
                    self.last_heartbeat = time.time()
                elif "thiefPingback" in body:
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
                message_ids.append(event.body_as_json()["messageId"])

            if len(message_ids):
                print("pingback for {}".format(message_ids))

                message = json.dumps({"thiefPingbackResponse": True, "messageIds": message_ids})

                self.registry_manager.send_c2d_message(
                    device_id,
                    message,
                    {"contentType": "application/json", "contentEncoding": "utf-8"},
                )

            time.sleep(1)

    def heartbeat_thread(self):
        """
        Thread which is responsible for sending heartbeat messages to the other side and
        also for making sure that heartbeat messages are received often enough
        """
        while not self.done.isSet():
            message = json.dumps({"thiefHeartbeat": True})

            logger.info("sending heartbeat")
            self.registry_manager.send_c2d_message(
                device_id, message, {"contentType": "application/json", "contentEncoding": "utf-8"},
            )

            seconds_since_last_heartbeat = time.time() - self.last_heartbeat
            if seconds_since_last_heartbeat > self.config.heartbeat_failure_interval:
                raise Exception(
                    "No heartbeat received for {} seconds".format(seconds_since_last_heartbeat)
                )

            time.sleep(self.config.heartbeat_interval)

    def wait_for_device_creation(self):
        # Make sure our device exists before we continue.  Since the device app and this
        # app are both starting up around the same time, it's possible that the device app
        # hasn't used DPS to crate the device yet.  Give up after 60 seconds
        start_time = time.time()
        device = None
        while not device and (time.time() - start_time) < 60:
            try:
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
                logger.info("shutdown: trigering clean shutdown")
            self.shutdown_event.set()

    def main(self):
        set_config(self.config)

        self.metrics.run_start = time.time()
        self.metrics.run_state = longhaul.RUNNING

        self.registry_manager = IoTHubRegistryManager(iothub_connection_string)

        self.wait_for_device_creation()

        self.eventhub_consumer_client = EventHubConsumerClient.from_connection_string(
            eventhub_connection_string, consumer_group=eventhub_consumer_group
        )

        # spin up threads that are required for operation
        self.shutdown_on_future_exit(
            self.executor.submit(self.eventhub_dispatcher_thread), name="eventhub_dispatcher"
        )
        self.shutdown_on_future_exit(
            self.executor.submit(self.pingback_thread), name="pingback thread"
        )
        self.shutdown_on_future_exit(
            self.executor.submit(self.heartbeat_thread), name="heartbeat thread"
        )

        # Live my life the way I want to until it's time for me to die.
        self.shutdown_event.wait(timeout=self.config.max_run_duration_in_seconds or None)

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


if __name__ == "__main__":
    ServiceApp().main()
