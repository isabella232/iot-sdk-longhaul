# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import platform
import os
import psutil


class SystemHealthTelemetry(object):
    def __init__(self):
        self.process = psutil.Process(os.getpid())
        self.system = platform.system()

    @property
    def process_cpu_percent(self):
        return self.process.cpu_percent()

    @property
    def process_working_set(self):
        return self.process.memory_info().rss

    @property
    def process_bytes_in_all_heaps(self):
        return self.process.memory_info().vms

    @property
    def process_private_bytes(self):
        if self.system == "Linux":
            memory_info = self.process.memory_info()
            # from /proc/{pid}/statm - equal to (VmRSS - (RssFile+RssShmem)) from /proc/{pid}/status
            return memory_info.rss - memory_info.shared
        elif self.system == "Windows":
            # from SYSTEM_PROCESS_INFORMATION.PrivatePageCount
            return self.memory_info().pivate
        else:
            # osx, aix, bsd -- undefined
            return 0

    @property
    def process_working_set_private(self):
        return self.process_private_bytes
