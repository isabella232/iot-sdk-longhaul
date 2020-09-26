# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import logging
import six
import abc


logger = logging.getLogger("thief.{}".format(__name__))


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
