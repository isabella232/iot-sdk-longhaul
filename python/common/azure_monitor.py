# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import os
import logging
import platform

from opencensus.ext.azure.log_exporter import AzureEventHandler
from opencensus.ext.azure.log_exporter import AzureLogHandler

ai_connection_string = os.environ["THIEF_AI_CONNECTION_STRING"]

_client_type = None
_run_id = None
_hub = None
_sdk_version = None
_device_id = None


def _default_value():
    pass


def configure_logging(
    client_type=_default_value,
    run_id=_default_value,
    hub=_default_value,
    sdk_version=_default_value,
    device_id=_default_value,
):
    global _client_type, _run_id, _hub, _sdk_version, _device_id
    if client_type != _default_value:
        _client_type = client_type
    if run_id != _default_value:
        _run_id = run_id
    if hub != _default_value:
        _hub = hub
    if sdk_version != _default_value:
        _sdk_version = sdk_version
    if device_id != _default_value:
        _device_id = device_id


def telemetry_processor_callback(envelope):
    global _client_type, _run_id, _hub, _sdk_version, _device_id
    envelope.tags["ai.cloud.role"] = _client_type
    envelope.tags["ai.cloud.roleInstance"] = _run_id
    envelope.data.baseData.properties["osType"] = platform.system()
    if _device_id:
        envelope.data.baseData.properties["deviceId"] = _device_id
    if _hub:
        envelope.data.baseData.properties["hub"] = _hub
    envelope.data.baseData.properties["runId"] = _run_id
    envelope.data.baseData.properties["sdkLanguage"] = "python"
    envelope.data.baseData.properties["sdkLanguageVersion"] = platform.python_version()
    envelope.data.baseData.properties["sdkVersion"] = _sdk_version
    envelope.data.baseData.properties["transport"] = "mqtt"

    return True


def get_event_logger():
    global _client_type, _run_id
    logger = logging.getLogger("thief_events.{}".format(_client_type))

    handler = AzureEventHandler(connection_string=ai_connection_string)
    handler.add_telemetry_processor(telemetry_processor_callback)

    handler.setLevel(logging.INFO)
    logger.addHandler(handler)

    return logger


log_handler = None


def log_to_azure_monitor(logger_name):
    global log_handler

    if not log_handler:
        log_handler = AzureLogHandler(connection_string=ai_connection_string)
        log_handler.add_telemetry_processor(telemetry_processor_callback)

    logging.getLogger(logger_name).addHandler(log_handler)
