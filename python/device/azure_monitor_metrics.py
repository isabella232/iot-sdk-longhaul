# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import os
from opencensus.ext.azure import metrics_exporter
from opencensus.stats import aggregation as aggregation_module
from opencensus.stats import measure as measure_module
from opencensus.stats import stats as stats_module
from opencensus.stats import view as view_module
from opencensus.tags import tag_map as tag_map_module
import azure_monitor

stats = stats_module.stats
view_manager = stats.view_manager
stats_recorder = stats.stats_recorder

app_insights_connection_string = os.environ["THIEF_APP_INSIGHTS_CONNECTION_STRING"]


class MetricsReporter(object):
    def __init__(self):
        self.exporter = metrics_exporter.new_metrics_exporter(
            connection_string=app_insights_connection_string
        )
        self.exporter.add_telemetry_processor(azure_monitor.telemetry_processor_callback)
        view_manager.register_exporter(self.exporter)
        self.mmap = stats_recorder.new_measurement_map()
        self.tmap = tag_map_module.TagMap()

    def add_integer_measurement(self, python_name, metric_name, description, units):
        new_measure = measure_module.MeasureInt(metric_name, description, units)
        new_view = view_module.View(
            metric_name, description, [], new_measure, aggregation_module.LastValueAggregation()
        )
        view_manager.register_view(new_view)

        def new_setter(value):
            self.mmap.measure_int_put(new_measure, value)

        setattr(self, "set_{}".format(python_name), new_setter)

    def add_float_measurement(self, python_name, metric_name, description, units):
        new_measure = measure_module.MeasureFloat(metric_name, description, units)
        new_view = view_module.View(
            metric_name, description, [], new_measure, aggregation_module.LastValueAggregation()
        )
        view_manager.register_view(new_view)

        def new_setter(value):
            self.mmap.measure_float_put(new_measure, value)

        setattr(self, "set_{}".format(python_name), new_setter)

    def record(self):
        self.mmap.record(self.tmap)
