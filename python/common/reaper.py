# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import six
import abc
import time
import threading
import logging

logger = logging.getLogger("thief.{}".format(__name__))


class ReaperObject(object):
    def __init__(self, future, callback, timeout):
        self.future = future
        self.callback = callback
        self.expiry_time = time.time() + timeout
        self.callback_called = False


@six.add_metaclass(abc.ABCMeta)
class ReaperMixin(object):
    """
    The ReaperMixin adds support for a "Reaper" thread.  The reaper thread is responsible for
    taking care of other threads when they die.  It's also responsible for recording when
    thrads live too ong.
    """

    def start_reaper(self):
        """
        start the reaper thread.  This is like an "init" function which is rsponsible for
        doing everything necessary to star the reaper
        """
        self.reaper_list = set()
        self.reaper_list_lock = threading.Lock()
        self.reaper_callback_lock = threading.Lock()
        self.reaper_shutdown_event = threading.Event()

        self.reaper_future = self.executor.submit(self.reaper_thread)
        self.shutdown_on_future_exit(self.reaper_future, "reaper")

    def stop_reaper(self):
        """
        Stop the resper.  This can e called from any thread to stop the reaper thread.
        """
        if not self.reaper_shutdown_event.isSet():
            logger.info("Stopping reaper")
            self.reaper_shutdown_event.set()

    def shutdown_on_future_exit(self, future, name):
        """
        This function is used to tell the reaper that the passed future is so important
        tha$t the entire test fails if/when this future completes
        """

        def local_callback(f):
            e = f.exception()
            if e:
                logger.error(
                    "Future {} is complete because of exception {}".format(name, e), exc_info=e
                )
            else:
                logger.warning("Future {} is complete with no exception".format(name))
            if not self.reaper_shutdown_event.isSet():
                self.shutdown(e or Exception("{} thread exited prematurely".format(name)))
                self.reaper_shutdown_event.set()

        future.add_done_callback(local_callback)

    def callback_on_future_exit(self, future, callback, timeout=None):
        """
        This funciton is used to tell the reper to call the passed callback under two conditions:
        1. the future exits or
        2. the passed timeout is elapsed.

        If the callbck is called because of timeout, it will not be called again wif/when the
        future actually completes.
        """

        obj = ReaperObject(future, callback, timeout)

        def local_callback(f):
            with self.reaper_list_lock:
                self.reaper_list.discard(obj)
            with self.reaper_callback_lock:
                do_callback = not obj.callback_called
                obj.callback_called = True
            if do_callback:
                obj.callback(f, f.exception())

        future.add_done_callback(local_callback)

    def reaper_thread(self):
        """
        This function is the actual threadproc for the reaper.
        """
        while not self.reaper_shutdown_event.isSet():
            with self.reaper_list_lock:
                reaper_objects = list(self.reaper_list)

            now = time.time()
            for obj in reaper_objects:
                if now > obj.expiry_time:
                    with self.reaper_list_lock:
                        self.reaper_list.discard(obj)
                    with self.reaper_callback_lock:
                        do_callback = not obj.callback_called
                        obj.callback_called = True
                    if do_callback:
                        obj.callback(obj.future, TimeoutError("Operation timed out"))

            self.reaper_shutdown_event.wait(1)
