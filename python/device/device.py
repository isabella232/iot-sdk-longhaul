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
from measurement import MeasureLatency
import dps
import longhaul
import reaper
import sys
from azure.iot.device import Message

logger = logging.getLogger("thief.{}".format(__name__))

logging.basicConfig(level=logging.WARNING)
# logging.getLogger("paho").setLevel(level=logging.DEBUG)
logging.getLogger("thief").setLevel(level=logging.INFO)


provisioning_host = os.environ["THIEF_DEVICE_PROVISIONING_HOST"]
id_scope = os.environ["THIEF_DEVICE_ID_SCOPE"]
group_symmetric_key = os.environ["THIEF_DEVICE_GROUP_SYMMETRIC_KEY"]
registration_id = os.environ["THIEF_DEVICE_ID"]


class DeviceRunMetrics(longhaul.RunMetrics):
    """
    Object we use internally to keep track of how a the entire test is performing.
    """

    def __init__(self):
        super(DeviceRunMetrics, self).__init__()
        self.d2c = longhaul.OperationMetrics()


class DeviceRunConfig(longhaul.RunConfig):
    """
    Object we use internally to keep track of how the entire test is configured.
    """

    def __init__(self):
        super(DeviceRunConfig, self).__init__()
        self.d2c = longhaul.OperationConfig()


def make_new_d2c_payload(message_id):
    """
    Helper function to create a unique payload which can be sent up as d2c message
    """
    msg = {"thief": {"cmd": "pingback", "messageId": message_id}}
    return Message(json.dumps(msg))


def set_config(config):
    """
    Helper function which sets our configuration.  Right now, this is hardcoded.
    Later, this will come from desired properties.
    """
    config.max_run_duration = 0
    config.d2c.operations_per_second = 3
    config.d2c.timeout_interval = 60
    config.d2c.failures_allowed = 0


class DeviceApp(longhaul.LonghaulMixin, reaper.ReaperMixin):
    """
    Main application object
    """

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=128)
        self.done = threading.Event()
        self.client = None
        self.metrics = DeviceRunMetrics()
        self.config = DeviceRunConfig()
        self.d2c_set_lock = threading.Lock()
        self.d2c_confirmed = set()
        self.d2c_unconfirmed = set()
        self.last_heartbeat = time.time()
        self.shutdown_event = threading.Event()
        self.next_heartbeat_id = 0

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
            "d2c": {
                "totalSuccessCount": self.metrics.d2c.total_succeeded.get_count(),
                "totalFailureCount": self.metrics.d2c.total_failed.get_count(),
            },
        }
        return props

    def update_initial_reported_properties(self):
        """
        Update reported properties at the start of a run
        """
        # TODO: language and version bits
        props = {"thief": {"device": self.get_props_from_metrics()}}

        logger.info("Setting initial thief properties: {}".format(props))
        self.client.patch_twin_reported_properties(props)

    def test_d2c_thread(self):
        """
        Thread to continuously send d2c messages throughout the longhaul run
        """

        def send_single_d2c_message():
            message_id = str(uuid.uuid4())
            data = make_new_d2c_payload(message_id)
            latency = MeasureLatency()

            try:
                self.metrics.d2c.inflight.increment()
                with latency:
                    self.client.send_message(data)
                self.metrics.d2c.latency.append(latency.get_latency())
                with self.d2c_set_lock:
                    self.d2c_unconfirmed.add(message_id)

            finally:
                self.metrics.d2c.inflight.decrement()
            self.metrics.pingback_requests_sent.increment()

        while not self.done.isSet():
            # submit a thread for the new event
            send_future = self.executor.submit(send_single_d2c_message)

            self.update_metrics_on_completion(send_future, self.config.d2c, self.metrics.d2c)

            # sleep until we need to send again
            self.done.wait(1 / self.config.d2c.operations_per_second)

    def send_thief_telemetry_thread(self):
        """
        Thread to occasionally send telemetry containing information about how the test
        is progressing
        """
        done = False

        while not done:
            # setting this at the begining and checking at the end guarantees one last update
            # before the thread dies
            if self.done.isSet():
                done = True

            props = {
                "thief": {
                    "cmd": "telemetry",
                    "averageD2cRoundtripLatencyToGatewayInSeconds": self.metrics.d2c.latency.extract_average(),
                    "d2cInFlightCount": self.metrics.d2c.inflight.get_count(),
                    "d2cSuccessCount": self.metrics.d2c.succeeded.extract_count(),
                    "d2cFailureCount": self.metrics.d2c.failed.extract_count(),
                }
            }

            msg = Message(json.dumps(props))
            msg.content_type = "application/json"
            msg.content_encoding = "utf-8"
            logger.info("Sending thief telementry: {}".format(msg.data))
            self.client.send_message(msg)

            self.done.wait(self.config.thief_telemetry_send_interval)

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

            props = {"thief": {"device": self.get_props_from_metrics()}}

            logger.info("updating thief props: {}".format(props))
            self.client.patch_twin_reported_properties(props)

            self.done.wait(self.config.thief_property_update_interval)

    def dispatch_c2d_thread(self):
        """
        Thread which continuously receives c2d messages throughout the test run.
        This will be soon renamed to be the c2d_dispatcher_thread.
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
                elif cmd == "pingbackResponse":
                    self.metrics.pingback_responses_received.increment()
                    # TODO: move to queue
                    list = thief.get("messageIds")
                    if list:
                        with self.d2c_set_lock:
                            for message_id in list:
                                self.d2c_confirmed.add(message_id)
                            remove = self.d2c_confirmed & self.d2c_unconfirmed
                            print("received {} items.  Removed {}".format(len(list), len(remove)))
                            self.metrics.d2c.verified.add(len(remove))
                            self.d2c_confirmed -= remove
                            self.d2c_unconfirmed -= remove

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
                self.metrics.run_state = longhaul.FAILED
                logger.error("shutdown: triggering error shutdown", exc_info=error)
            else:
                self.metrics.run_state = longhaul.COMPLETE
                logger.info("shutdown: trigering clean shutdown")
            self.metrics.exit_reason = str(error or "Clean shutdown")
            self.shutdown_event.set()

    def main(self):
        set_config(self.config)

        self.metrics.run_start = datetime.datetime.now()
        self.metrics.run_state = longhaul.RUNNING

        # Create our client and push initial properties
        self.client = dps.create_device_client_using_dps_group_key(
            provisioning_host=provisioning_host,
            registration_id=registration_id,
            id_scope=id_scope,
            group_symmetric_key=group_symmetric_key,
        )
        self.update_initial_reported_properties()

        # spin up threads that are required for operation
        self.shutdown_on_future_exit(
            future=self.executor.submit(self.heartbeat_thread), name="heartbeat_thread"
        )
        self.shutdown_on_future_exit(
            future=self.executor.submit(self.dispatch_c2d_thread), name="c2d_dispatch_thread"
        )
        self.shutdown_on_future_exit(
            future=self.executor.submit(self.send_thief_telemetry_thread),
            name="send_thief_telemetry_thread",
        )
        self.shutdown_on_future_exit(
            future=self.executor.submit(self.update_thief_properties_thread),
            name="update_thief_properties_thread",
        )

        # Spin up worker threads for each thing we're testing.
        self.shutdown_on_future_exit(
            future=self.executor.submit(self.test_d2c_thread), name="test_d2c_thead"
        )

        # Live my life the way I want to until it's time for me to die.
        self.shutdown_event.wait(timeout=self.config.max_run_duration or None)

        logger.info("Run is complete.  Cleaning up.")

        # set the run state before setting the done event.  This gives the thief threads one more
        # chance to push their status before we start shutting down
        if self.metrics.run_state != longhaul.FAILED:
            self.metrics.run_state = longhaul.COMPLETE

        # stop the reaper, then set the "done" event in order to stop all other threads.
        self.stop_reaper()
        self.done.set()

        logger.info("Waiting for all threads to exit")

        # wait for all other threads to exit.  This currently waits for all threads, and it can't
        # "give up" if some threads refuse to exit.
        self.executor.shutdown()

        logger.info("All threads exited.  Disconnecting")

        self.client.disconnect()

        logger.info("Done disconnecting.  Exiting")

        if self.metrics.run_state == longhaul.FAILED:
            sys.exit(1)


if __name__ == "__main__":
    DeviceApp().main()
