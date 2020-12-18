# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import logging
import six
import abc
import time
import datetime
import sys
import platform
import os
import traceback
import pdb
from system_health_telemetry import SystemHealthTelemetry


logger = logging.getLogger("thief.{}".format(__name__))

WAITING = "waiting"
RUNNING = "running"
FAILED = "failed"
COMPLETE = "complete"
INTERRUPTED = "interrupted"


class WorkerThreadInfo(object):
    """
    This structure holds information about a running worker thread.
    """

    def __init__(self, threadproc, name):
        self.threadproc = threadproc
        self.name = name
        self.future = None
        self.watchdog_epochtime = None


@six.add_metaclass(abc.ABCMeta)
class AppBase(object):
    def __init__(self):
        self.system_health_telemetry = SystemHealthTelemetry()
        self.paused = False

    @abc.abstractmethod
    def disconnect(self):
        pass

    def pre_shutdown(self):
        pass

    def pause_all_threads(self):
        """
        Pause all threads.  Used to stop all background threads while debugging
        """
        self.paused = True

    def unpause_all_threads(self):
        """
        Unpause all threads.  Used when debugging is finished.
        """
        self.paused = False

    def is_paused(self):
        """
        return True if threads should be paused.
        """
        return self.paused

    def breakpoint(self):
        """
        Pause all threads and break into the debugger
        """
        self.pause_all_threads()
        pdb.set_trace()

    def get_system_properties(self, version):
        return {
            "language": "python",
            "languageVersion": platform.python_version(),
            "sdkVersion": version,
            "sdkGithubRepo": os.getenv("THIEF_SDK_GIT_REPO"),
            "sdkGithubBranch": os.getenv("THIEF_SDK_GIT_BRANCH"),
            "sdkGithubCommit": os.getenv("THIEF_SDK_GIT_COMMIT"),
            "osType": platform.system(),
            "osRelease": platform.version(),
        }

    def get_system_health_telemetry(self):
        props = {
            "processCpuPercent": self.system_health_telemetry.process_cpu_percent,
            "processWorkingSet": self.system_health_telemetry.process_working_set,
            "processBytesInAllHeaps": self.system_health_telemetry.process_bytes_in_all_heaps,
            "processPrivateBytes": self.system_health_telemetry.process_private_bytes,
            "processWorkingSetPrivate": self.system_health_telemetry.process_working_set_private,
        }
        return props

    def _dump_all_threads(self):
        """
        Dump all threads for debugging
        """
        if six.PY3:
            for thread_id, frame in sys._current_frames().items():
                logger.warning("Stack for thread {}".format(thread_id))
                logger.warning(str(traceback.format_stack(frame)))

    def run_threads(self, threads_to_launch):
        # Launch the threads.
        for worker in threads_to_launch:
            logger.info("Launching {}".format(worker.name))
            worker.future = self.executor.submit(worker.threadproc, worker)
            worker.watchdog_epochtime = time.time()

        loop_start_epochtime = time.time()
        while self.metrics.run_state == RUNNING:
            if self.is_paused():
                time.sleep(1)
                continue

            try:
                # Check all of our threads for failure, unexpected condition, or watchdog timeout
                for worker in threads_to_launch:
                    if worker.future.done():
                        try:
                            error = worker.future.exception(timeout=0)
                        except Exception as e:
                            error = e
                        if not error:
                            error = Exception("{} thread exited prematurely".format(worker.name))

                        logger.error(
                            "Future {} is complete because of exception {}".format(
                                worker.name, error
                            ),
                            exc_info=error,
                        )
                        worker.future = None
                        self.metrics.run_state = FAILED
                        self.metrics.exit_reason = str(error)

                    elif not worker.future.running():
                        error = Exception(
                            "Unexpected: Future {} is not running and not done".format(worker.name)
                        )
                        logger.error(str(error), exc_info=error)
                        worker.future = None
                        self.metrics.run_state = FAILED
                        self.metrics.exit_reason = str(error)

                    elif (
                        time.time() - worker.watchdog_epochtime
                        > self.config.watchdog_failure_interval_in_seconds
                    ):
                        reason = "Future {} has not responded for {} seconds.  Failing".format(
                            worker.name, time.time() - worker.watchdog_epochtime
                        )
                        error = Exception(reason)
                        logger.error(reason)
                        self._dump_all_threads()
                        worker.future = None
                        self.metrics.run_state = FAILED
                        self.metrics.exit_reason = str(error)

                # If we're still running, check to see if we're done.  If not, sleep and loop again.
                if self.metrics.run_state == RUNNING:

                    if self.config.max_run_duration and (
                        time.time() - loop_start_epochtime > self.config.max_run_duration
                    ):
                        self.metrics.run_state = COMPLETE
                        self.metrics.exit_rason = "Run passed after {}".format(
                            datetime.timedelta(self.config.max_run_duration)
                        )
                    else:
                        time.sleep(1)

            except (KeyboardInterrupt, Exception) as e:
                logger.error("Exception {} caught in main loop".format(e), exc_info=True)
                self.metrics.run_state = INTERRUPTED if isinstance(e, KeyboardInterrupt) else FAILED
                self.metrics.exit_reason = "Main thread raised {}".format(type(e))

        logger.info("Run is complete.  Cleaning up.")
        logger.info(
            "state = {}, exit reason = {}".format(self.metrics.run_state, self.metrics.exit_reason)
        )
        self.metrics.run_end_utc = datetime.datetime.now(datetime.timezone.utc)
        self.done.set()
        self.pre_shutdown()
        logger.info("Waiting up to 60 seconds for  all threads to exit")

        wait_start_epochtime = time.time()
        running_threads = list(threads_to_launch)
        while len(running_threads) and (time.time() - wait_start_epochtime < 60):
            new_list = []
            for worker in running_threads:
                if not worker.future:
                    # must be a crashed thread.  Ignore it.
                    pass
                elif worker.future.done():
                    error = worker.future.exception()
                    if error:
                        logger.warning("Thread {} raised {} on teardown".format(worker.name, error))
                    logger.info("Thread {} is exited".format(worker.name))
                else:
                    new_list.append(worker)
                running_threads = new_list

        if len(running_threads):
            logger.warning(
                "Some threads refused to exit: {}".format([w.name for w in running_threads])
            )
        else:
            logger.info("All threads exited.  Disconnecting")
            self.executor.shutdown()
            self.disconnect()
            logger.info("Done disconnecting.  Exiting")

        if self.metrics.run_state == FAILED:
            logger.error("Forcing exit")
            raise Exception("Run failed: {}".format(self.metrics.exit_reason))
