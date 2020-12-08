# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import os
import logging
import platform

from opencensus.ext.azure.log_exporter import AzureEventHandler
from opencensus.ext.azure.log_exporter import AzureLogHandler

app_insights_connection_string = os.environ["THIEF_APP_INSIGHTS_CONNECTION_STRING"]

_client_type = None
_run_id = None
_hub = None
_sdk_version = None
_device_id = None
_pairing_id = None
_pool_id = None
_transport = None


def _default_value():
    """
    use a function to represent a unique value.
    """
    pass


def add_logging_properties(
    client_type=_default_value,
    run_id=_default_value,
    hub=_default_value,
    sdk_version=_default_value,
    device_id=_default_value,
    pairing_id=_default_value,
    pool_id=_default_value,
    transport=_default_value,
):
    global _client_type, _run_id, _hub, _sdk_version, _device_id, _pairing_id, _pool_id, _transport
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
    if pairing_id != _default_value:
        _pairing_id = pairing_id
    if pool_id != _default_value:
        _pool_id = pool_id
    if transport != _default_value:
        _transport = transport


def telemetry_processor_callback(envelope):
    global _client_type, _run_id, _hub, _sdk_version, _device_id, _pairing_id, _pool_id, _transport
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
    if _transport:
        envelope.data.baseData.properties["transport"] = _transport
    if _pairing_id:
        envelope.data.baseData.properties["pairingId"] = _pairing_id
    if _pool_id:
        envelope.data.baseData.properties["poolId"] = _pool_id

    return True


def get_event_logger():
    global _client_type, _run_id
    logger = logging.getLogger("thief_events.{}".format(_client_type))

    handler = AzureEventHandler(connection_string=app_insights_connection_string)
    handler.add_telemetry_processor(telemetry_processor_callback)

    handler.setLevel(logging.INFO)
    logger.addHandler(handler)

    return logger


log_handler = None


def log_to_azure_monitor(logger_name):
    global log_handler

    if not log_handler:
        log_handler = AzureLogHandler(connection_string=app_insights_connection_string)
        log_handler.add_telemetry_processor(telemetry_processor_callback)

    logging.getLogger(logger_name).addHandler(log_handler)
