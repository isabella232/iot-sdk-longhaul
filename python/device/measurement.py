# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import statistics
import threading
import contextlib
import datetime


class ThreadSafeCounter(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.value = 0

    def set(self, value):
        with self.lock:
            self.value = value

    def add(self, value):
        with self.lock:
            self.value += value

    def increment(self):
        self.add(1)

    def decrement(self):
        self.add(-1)

    def get_count(self):
        with self.lock:
            return self.value

    def extract_count(self):
        with self.lock:
            value = self.value
            self.value = 0
            return value


class ThreadSafeList(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.list = []

    def append(self, value):
        with self.lock:
            self.list.append(value)

    def clear(self):
        with self.lock:
            self.list.clear()

    def get_average(self):
        with self.lock:
            return statistics.mean(self.list)

    def extract_average(self):
        with self.lock:
            average = 0
            if len(self.list):
                average = statistics.mean(self.list)
                self.list.clear()
            return average

    def extract_list(self):
        with self.lock:
            old_list = self.list
            self.list = []
            return old_list


class MeasureLatency(contextlib.AbstractContextManager):
    def __init__(self, tracker=None):
        self.start_time = None
        self.end_time = None
        self.tracker = tracker

    def __enter__(self):
        self.start_time = datetime.datetime.now()

    def __exit__(self, *args):
        self.end_time = datetime.datetime.now()
        if self.tracker:
            self.tracker.add_sample(self.get_latency())

    def get_latency(self):
        if self.start_time:
            if self.end_time:
                return (self.end_time - self.start_time).total_seconds()
            else:
                return (datetime.datetime.now() - self.start_time).total_seconds()
        else:
            return 0
