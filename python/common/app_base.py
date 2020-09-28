# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import logging
import six
import abc
import time
import datetime
import sys


logger = logging.getLogger("thief.{}".format(__name__))

WAITING = "waiting"
RUNNING = "running"
FAILED = "failed"
COMPLETE = "complete"


@six.add_metaclass(abc.ABCMeta)
class AppBase(object):
    @abc.abstractmethod
    def disconnect(self):
        pass

    def run_threads(self, threads_to_launch):
        # Launch the threads.
        running_threads = []
        for (threadproc, name) in threads_to_launch:
            running_threads.append((name, self.executor.submit(threadproc)))

        loop_start_time = time.time()
        while self.metrics.run_state == RUNNING:
            for (name, future) in running_threads:
                if future.done():
                    try:
                        error = future.exception(timeout=0)
                    except Exception as e:
                        error = e
                    if not error:
                        error = Exception("{} thread exited prematurely".format(name))

                    logger.error(
                        "Future {} is complete because of exception {}".format(name, error),
                        exc_info=error,
                    )
                    self.metrics.run_state = FAILED
                    self.metrics.exit_reason = str(error)

                elif not future.running():
                    error = Exception(
                        "Unexpected: Future {} is not running and not done".format(name)
                    )
                    logger.error(str(error), exc_info=error)
                    self.metrics.run_state = FAILED
                    self.metrics.exit_reason = str(error)

            if self.config.max_run_duration and (
                time.time() - loop_start_time > self.config.max_run_duration
            ):
                self.metrics.run_state = COMPLETE
                self.metrics.exit_rason = "Run passed after {}".format(
                    datetime.timedelta(self.config.max_run_duration)
                )

            time.sleep(1)

        logger.info("Run is complete.  Cleaning up.")
        self.metrics.run_end_utc = datetime.datetime.now(datetime.timezone.utc)
        self.done.set()
        logger.info("Waiting up to 60 seconds for  all threads to exit")

        wait_start = time.time()
        while len(running_threads) and (time.time() - wait_start < 60):
            new_list = []
            for (name, future) in running_threads:
                if future.done():
                    error = future.exception()
                    if error:
                        logger.warning("Thread {} raised {} on teardown".format(name, error))
                    logger.info("Thread {} is exited".format(name))
                else:
                    new_list.append((name, future))
                running_threads = new_list

        if len(running_threads):
            logger.warning(
                "Some threads refused to exit: {}".format([t[0] for t in running_threads])
            )
        else:
            logger.info("All threads exited.  Disconnecting")
            self.executor.shutdown()
            self.disconnect()
            logger.info("Done disconnecting.  Exiting")

        if self.metrics.run_state == FAILED:
            logger.info("Forcing exit")
            sys.exit(1)
