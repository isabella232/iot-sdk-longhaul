# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import os
import logging
import contextlib
import platform

from opencensus.ext.azure.log_exporter import AzureEventHandler
from opencensus.ext.azure.log_exporter import AzureLogHandler
from opencensus.ext.azure.trace_exporter import AzureExporter
from opencensus.trace.samplers import AlwaysOnSampler
from opencensus.trace.tracer import Tracer
from opencensus.trace.propagation.text_format import TextFormatPropagator
from opencensus.trace.span_context import SpanContext
from opencensus.trace.trace_options import TraceOptions
import opencensus.log


ai_connection_string = os.environ["THIEF_AI_CONNECTION_STRING"]
device_id = os.environ["THIEF_DEVICE_ID"]


def _get_telemetry_processor_callback(client_type):
    def telemetry_processor_callback(envelope):
        # TODO: add more custom dimensions here
        envelope.tags["ai.cloud.role"] = client_type
        envelope.tags["ai.cloud.roleInstance"] = "{}-{}".format(device_id, client_type)
        envelope.data.baseData.properties["osType"] = platform.system()
        envelope.data.baseData.properties["deviceId"] = device_id
        envelope.data.baseData.properties["clientType"] = client_type
        return True

    return telemetry_processor_callback


def get_event_logger(client_type):
    logger = logging.getLogger("thief_events.{}".format(client_type))

    handler = AzureEventHandler(connection_string=ai_connection_string)
    handler.add_telemetry_processor(_get_telemetry_processor_callback(client_type))

    handler.setLevel(logging.INFO)
    logger.addHandler(handler)

    return logger


log_handler = None


def log_to_azure_monitor(logger_name, client_type=None):
    global log_handler

    if not log_handler:
        if not client_type:
            raise Exception(
                "client_type needs to be passed on the first call to log_to_azure_monitor"
            )

        log_handler = AzureLogHandler(connection_string=ai_connection_string)
        log_handler.add_telemetry_processor(_get_telemetry_processor_callback(client_type))
    else:
        if client_type:
            raise Exception(
                "client_type should not be passed after first call to log_to_azure_monitor"
            )

    logging.getLogger(logger_name).addHandler(log_handler)


log_exporter = None


def enable_tracing(client_type):
    global log_exporter

    if not log_exporter:
        log_exporter = AzureExporter(connection_string=ai_connection_string)
        log_exporter.add_telemetry_processor(_get_telemetry_processor_callback(client_type))


formatter = TextFormatPropagator()


class DependencyTracer(contextlib.AbstractContextManager):
    """
    DependencyTracer is an object that helps us trace code paths in Azure Monitor.

    The names are confusing right now because we're using opencensus "tracing" objects, but they
    show up in Azure Monitor as "dependency" records (because we're using this to observe
    dependencies between different code paths), and some places refer to dependency tracking
    as "distributed tracing".

    The things that show up in Azure Monitor as "traces" are actually logging records.

    So, sometimes "traces" means "dependencies" and sometimes it means "logging".  Confused?
    Don't worry, you'll get used to it.
    """

    def __init__(self, operation_name, context_dict=None):
        global log_exporter

        if not log_exporter:
            raise Exception("enable_tracing needs to be called before creating this object")

        if context_dict:
            self.tracer = Tracer(
                exporter=log_exporter,
                sampler=AlwaysOnSampler(),
                span_context=self.dict_to_span_context(context_dict),
            )
        else:
            self.tracer = Tracer(exporter=log_exporter, sampler=AlwaysOnSampler())

        self.outer_span = self.tracer.span(operation_name)
        self.outer_span.__enter__()
        self.span_id = self.outer_span.span_id

    def __del__(self):
        self.end_operation()

    def __exit__(self, *exc_info):
        # so we can use DependencyTracer as a context manager
        self.end_operation(*exc_info)

    def span(self, name):
        return self.tracer.span(name)

    def end_operation(self, exception_type=None, exception_value=None, traceback=None):
        if self.outer_span:
            self.outer_span.__exit__(exception_type, exception_value, traceback)
            self.outer_span = None
        if self.tracer:
            self.tracer.finish()
            self.tracer = None

    def get_logging_tags(self):
        """
        this function returns a dict that can be used to attach logging messages to the current
        Azure Monitor span.  We do this so we can correlate log messages with the span that they
        belong to.  This code is based on opencensus.log.get_log_attrs()
        """
        return {
            opencensus.log.TRACE_ID_KEY: self.tracer.span_context.trace_id,
            opencensus.log.SPAN_ID_KEY: self.tracer.span_context.span_id,
            opencensus.log.SAMPLING_DECISION_KEY: self.tracer.span_context.trace_options.get_enabled(),
        }

    def span_to_dict(self):
        """
        return a dict with the current tracing context.  This can be sent to other code and
        reconstituted into a TracerObject using the "context_dict" paramter on the Tracer
        initializer.
        """
        context_dict = {}
        formatter.to_carrier(span_context=self.tracer.span_context, carrier=context_dict)
        return context_dict

    def dict_to_span_context(self, context_dict):
        """
        return a SpanContext object based on the dictionary passed in.  This is used to
        reconstitue Tracer objects based on a context dictionary that was created using the
        span_to_dict function.
        """
        # from_carrier crashes deserializing trace_options -- it was clearly not tested.
        # return formatter.from_carrier(carrier)
        return SpanContext(
            trace_id=context_dict["opencensus-trace-traceid"],
            span_id=context_dict["opencensus-trace-spanid"],
            trace_options=TraceOptions(context_dict["opencensus-trace-traceoptions"]),
            from_header=True,
        )
