# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import logging
import six
import abc
from measurement import ThreadSafeCounter, ThreadSafeList


logger = logging.getLogger("thief.{}".format(__name__))

WAITING = "waiting"
RUNNING = "running"
FAILED = "failed"
COMPLETE = "complete"


class OperationMetrics(object):
    """
    Object we use internally to keep track of how a particular operation (such as D2C) is performing.
    """

    def __init__(self):
        self.inflight = ThreadSafeCounter()
        self.succeeded = ThreadSafeCounter()
        self.failed = ThreadSafeCounter()
        self.verified = ThreadSafeCounter()
        self.total_succeeded = ThreadSafeCounter()
        self.total_failed = ThreadSafeCounter()
        self.latency = ThreadSafeList()


class OperationConfig(object):
    """
    Object we use internally to keep track of how a particular operation (such as D2C) is is configured.
    """

    def __init__(self):
        self.operations_per_second = 0
        self.timeout_interval = 0
        self.failures_allowed = 0


class RunMetrics(object):
    """
    Object we use internally to keep track of how a the entire test is performing.
    """

    def __init__(self):
        self.run_start = None
        self.run_time = None
        self.run_state = WAITING
        self.exit_reason = None
        self.heartbeats_sent = ThreadSafeCounter()
        self.heartbeats_received = ThreadSafeCounter()
        self.pingback_requests_sent = ThreadSafeCounter()
        self.pingback_responses_received = ThreadSafeCounter()
        self.pingback_requests_received = ThreadSafeCounter()
        self.pingback_responses_sent = ThreadSafeCounter()


class RunConfig(object):
    """
    Object we use internally to keep track of how the entire test is configured.
    """

    def __init__(self):
        self.max_run_duration = 0
        self.heartbeat_interval = 10
        self.heartbeat_failure_interval = 30
        self.thief_telemetry_send_interval = 10
        self.thief_property_update_interval = 10


@six.add_metaclass(abc.ABCMeta)
class LonghaulMixin(object):
    """
    The LonghaulMixin is used to add support for longhaul testing.  In particular, it adds
    support for recording metrics based on operatoin successes and failures
    """

    def update_metrics_on_completion(self, future, config, metrics):
        """
        Add a callback to record success and failure metrics for the given future
        """

        def local_callback(future, error):
            if error:
                metrics.failed.increment()
                metrics.total_failed.increment()
                if metrics.total_failed.get_count() > config.failures_allowed:
                    self.shutdown(error)
            else:
                metrics.succeeded.increment()
                metrics.total_succeeded.increment()

        self.callback_on_future_exit(future, local_callback, config.timeout_interval)
