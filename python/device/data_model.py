# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
from dictionary_object import DictionaryObject
import datetime


class LongHaulDesiredProperties(DictionaryObject):
    def __init__(self):
        super(LongHaulDesiredProperties, self).__init__()
        self.system_telemetry_send_interval_in_seconds = 10
        self.max_run_duration_in_seconds = datetime.timedelta(days=3).total_seconds()

        self.d2c_sends_per_second = 5
        self.d2c_timeout_interval_in_seconds = 60
        self.d2c_failures_allowed = 0

        self.freeze()


class LongHaulReportedProperties(DictionaryObject):
    def __init__(self):
        super(LongHaulReportedProperties, self).__init__()
        self.framework_version = ""

        self.os = ""
        self.os_version = ""

        self.system_architecture = ""
        self.total_system_memory_in_mb = 0

        self.run_start = ""
        self.run_end = ""
        self.run_state = ""

        self.sdk_language = ""
        self.sdk_repo = ""
        self.sdk_branch = ""
        self.sdk_sha = ""
        self.sdk_version = ""

        self.transport = ""

        self.d2c_total_success_count = 0
        self.d2c_total_failure_count = 0

        self.freeze()


class LongHaulTelemetry(DictionaryObject):
    def __init__(self):
        super(LongHaulTelemetry, self).__init__()
        self.process_cpu_usage_percent = 0
        self.process_active_ops = 0

        self.process_voluntary_context_switches_per_second = 0
        self.process_involuntary_context_switches_per_second = 0

        self.process_resident_memory_in_mb = 0
        self.process_available_memory_in_mb = 0

        self.system_available_memory_in_mb = 0
        self.system_free_memory_in_mb = 0

        self.average_d2c_roundtrip_latency_to_gateway_in_seconds = 0
        self.d2c_in_flight_count = 0
        self.d2c_success_count = 0
        self.d2c_failure_count = 0
        self.d2c_waiting_to_verify_count = 0
        self.d2c_verified_count = 0

        self.freeze()
