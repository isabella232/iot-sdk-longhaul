# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import logging
import os
import sys
import time
import data_model
import uuid
import json
import datetime
from measurement import ThreadSafeCounter, ThreadSafeList, MeasureLatency
from concurrent.futures import ThreadPoolExecutor

from azure.iot.device import IoTHubDeviceClient, Message

logging.basicConfig(level=logging.ERROR)
# logging.getLogger("paho").setLevel(level=logging.DEBUG)

logger = logging.getLogger(__name__)
logger.setLevel(level=logging.DEBUG)

RUNNING = "running"
FAILED = "failed"
COMPLETE = "complete"

connection_string = os.environ["IOTHUB_DEVICE_CONNECTION_STRING"]


class OperationMetric:
    """
    Object we use internally to keep track of how a particular operation is performing.
    """

    def __init__(self):
        self.inflight = ThreadSafeCounter()
        self.succeeded = ThreadSafeCounter()
        self.failed = ThreadSafeCounter()
        self.total_succeeded = ThreadSafeCounter()
        self.total_failed = ThreadSafeCounter()
        self.latency = ThreadSafeList()


class OperationConfig(object):
    """
    Object we use internally to keep track of how a particular operation is is configured.
    """

    def __init__(self):
        self.operations_per_second = 0
        self.timeout_interval_in_seconds = 0
        self.failures_allowed = 0


class LongHaulMetrics(object):
    """
    Object we use internally to keep track of how a the entire test is performing.
    """

    def __init__(self):
        self.d2c = OperationMetric()
        self.run_start = None
        self.run_end = None
        self.run_state = "waiting"


class LongHaulConfig(object):
    """
    Object we use internally to keep track of how the entire test is configured.
    """

    def __init__(self):
        self.system_telemetry_send_interval_in_seconds = 0
        self.max_run_duration_in_seconds = 0
        self.d2c = OperationConfig()


def make_new_d2c_payload():
    """
    Helper function to create a unique payload which can be sent up as d2c message
    """
    msg = {"message_id": str(uuid.uuid4()), "lh_send_response": True}
    return Message(json.dumps(msg))


def unpack_config(config):
    """
    Helper function which unpacks our configuration from the data_model.py structures
    into something a little more convenient for this module.
    """
    # TODO: get these from desired properties
    props = data_model.LongHaulDesiredProperties()
    config.system_telemetry_send_interval_in_seconds = (
        props.system_telemetry_send_interval_in_seconds
    )
    config.max_run_duration_in_seconds = props.max_run_duration_in_seconds
    config.d2c.operations_per_second = props.d2c_sends_per_second
    config.d2c.timeout_interval_in_seconds = props.d2c_timeout_interval_in_seconds
    config.d2c.failures_allowed = props.d2c_failures_allowed


class App(object):
    """
    Main application object
    """

    def __init__(self):
        # We use the thread pool for many short-lived functions
        # make the pool big so it doesn't become a limiting factor.
        # we want to measure the SDK,  We don't want queueing to get in the way of our measurement.
        self.executor = ThreadPoolExecutor(max_workers=128)
        self.client = None
        self.currently_running_operations = ThreadSafeList()
        self.metrics = LongHaulMetrics()
        self.config = LongHaulConfig()

    def update_initial_properties(self):
        """
        Update reported properties at the start of a run
        """
        p = data_model.LongHaulReportedProperties()
        # toto: update these values
        p.framework_version = ""

        p.os = ""
        p.os_version = ""

        p.system_architecture = ""
        p.total_system_memory_in_mb = 0

        p.run_start = datetime.datetime.now()
        p.run_state = "waiting"

        p.sdk_language = "python"
        p.sdk_repo = ""
        p.sdk_branch = ""
        p.sdk_sha = ""
        p.sdk_version = ""

        p.transport = ""

        props = p.to_dict()
        logger.debug("updating props: {}".format(props))
        self.client.patch_twin_reported_properties(props)

    def d2c_thread(self, config, metrics):
        """
        Thread to continuously send d2c messages throughout the longhaul run
        """

        def send_single_d2c_message():
            data = make_new_d2c_payload()
            latency = MeasureLatency()

            try:
                metrics.inflight.increment()
                with latency:
                    self.client.send_message(data)
                metrics.latency.append(latency.get_latency())

            finally:
                metrics.inflight.decrement()

            return time.time()

        while not self.done:
            # submit a thread for the new event
            send_future = self.executor.submit(send_single_d2c_message)
            # timeout is based on when the task is submitted, not when it actually starts running
            send_future.timeout_time = time.time() + config.timeout_interval_in_seconds
            send_future.config = self.config.d2c
            send_future.metrics = self.metrics.d2c

            # add to thread-safe list of futures
            self.currently_running_operations.append(send_future)

            # sleep until we need to send again
            time.sleep(1 / config.operations_per_second)

    def send_telemetry_thread(self):
        """
        Thread to occasionally send telemetry containing information about how the test
        is progressing
        """
        done = False

        while not done:
            # setting this at the begining and checking at the end guarantees one last update
            # before the thread dies
            if self.done:
                done = True

            t = data_model.LongHaulTelemetry()
            t.process_cpu_usage_percent = 0

            t.process_voluntary_context_switches_per_second = 0
            t.process_involuntary_context_switches_per_second = 0

            t.process_resident_memory_in_mb = 0
            t.process_available_memory_in_mb = 0

            t.system_available_memory_in_mb = 0
            t.system_free_memory_in_mb = 0

            t.average_d2c_roundtrip_latency_to_gateway_in_seconds = (
                self.metrics.d2c.latency.extract_average()
            )
            t.d2c_in_flight_count = self.metrics.d2c.inflight.get_count()
            t.d2c_success_count = self.metrics.d2c.succeeded.extract_count()
            t.d2c_failure_count = self.metrics.d2c.failed.extract_count()

            msg = Message(json.dumps(t.to_dict()))
            msg.content_type = "application/json"
            msg.content_encoding = "utf-8"
            logger.debug("Selnding telementry: {}".format(msg.data))
            self.client.send_message(msg)

            time.sleep(self.config.system_telemetry_send_interval_in_seconds)

    def update_properties_thread(self):
        """
        Thread which occasionally sends reported properties with information about how the
        test is progressing
        """
        done = False

        while not done:
            # setting this at the begining and checking at the end guarantees one last update
            # before the thread dies
            if self.done:
                done = True

            p = data_model.LongHaulReportedProperties()
            p.d2c_total_success_count = self.metrics.d2c.total_succeeded.get_count()
            p.d2c_total_failure_count = self.metrics.d2c.total_failed.get_count()
            p.run_state = self.metrics.run_state
            if self.metrics.run_end:
                p.run_end = self.metrics.run_end

            props = p.to_dict()
            logger.debug("updating props: {}".format(props))
            self.client.patch_twin_reported_properties(props)

            time.sleep(self.config.system_telemetry_send_interval_in_seconds)

    def receive_message_thread(self):
        """
        Thread which continuously receives c2d messages throughout the test run
        """
        done = False
        while not done:
            print("waiting")
            msg = self.client.receive_message()
            print(msg.__dict__)

    def main(self):
        # collection of Future objects for all of the threads that are running continuously
        # these are stored in a local variable because no other thread procs should need this.
        loop_futures = []

        unpack_config(self.config)

        # Create our client and push initial properties
        self.client = IoTHubDeviceClient.create_from_connection_string(connection_string)
        self.update_initial_properties()

        # Spin up our worker threads.
        loop_futures.append(
            self.executor.submit(self.d2c_thread, self.config.d2c, self.metrics.d2c)
        )
        loop_futures.append(self.executor.submit(self.send_telemetry_thread))
        loop_futures.append(self.executor.submit(self.update_properties_thread))
        loop_futures.append(self.executor.submit(self.receive_message_thread))

        self.metrics.run_start = time.time()
        self.metrics.run_state = RUNNING

        try:
            while True:
                # most work happens in other threads, so we sleep except for when we're
                # checking status
                time.sleep(1)

                # Make sure the loops are still running.  Force a failure if they fail.
                error = None
                for future in loop_futures:
                    if future.done():
                        self.done = True
                        error = Exception("Unexpected loop_futures exit")
                        try:
                            future.result()
                        except Exception as e:
                            error = e
                if error:
                    raise error

                # check for completed operations.  Count failures, but don't fail unless
                # we exceeed the limit
                for future in self.currently_running_operations.extract_list():
                    future_failed = False
                    if future.done():
                        end_time = future.result()
                        if end_time > future.timeout_time:
                            future_failed = True
                    elif time.time() > future.timeout_time:
                        future_failed = True
                    else:
                        self.currently_running_operations.append(future)

                    if future_failed:
                        future.metrics.failed.increment()
                        future.metrics.total_failed.increment()
                        if future.metrics.failed.get_count() > future.config.failures_allowed:
                            raise Exception("Failure count exceeded")
                    else:
                        future.metrics.succeeded.increment()
                        future.metrics.total_succeeded.increment()

        except Exception:
            self.metrics.run_state = FAILED
            raise
        else:
            self.metrics.run_state = COMPLETE
        finally:
            self.done = True
            # finish all loop futures.  Ones that report test results will run one more time.
            for future in loop_futures:
                future.result()
            self.client.disconnect()

        print("app is done")
        sys.exit(0 if self.metrics.run_state == COMPLETE else 1)


if __name__ == "__main__":
    App().main()
