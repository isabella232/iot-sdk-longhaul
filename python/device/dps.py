# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import base64
import hmac
import hashlib
from azure.iot.device import ProvisioningDeviceClient
from azure.iot.device import IoTHubDeviceClient


def _derive_device_key(registration_id, group_symmetric_key):
    """
    The unique device ID and the group master key should be encoded into "utf-8"
    After this the encoded group master key must be used to compute an HMAC-SHA256 of the encoded registration ID.
    Finally the result must be converted into Base64 format.
    The device key is the "utf-8" decoding of the above result.
    """
    message = registration_id.encode("utf-8")
    signing_key = base64.b64decode(group_symmetric_key.encode("utf-8"))
    signed_hmac = hmac.HMAC(signing_key, message, hashlib.sha256)
    device_key_encoded = base64.b64encode(signed_hmac.digest())
    return device_key_encoded.decode("utf-8")


def create_device_client_using_dps_group_key(
    provisioning_host, registration_id, id_scope, group_symmetric_key
):
    device_key = _derive_device_key(registration_id, group_symmetric_key)
    provisioning_client = ProvisioningDeviceClient.create_from_symmetric_key(
        provisioning_host=provisioning_host,
        registration_id=registration_id,
        id_scope=id_scope,
        symmetric_key=device_key,
    )
    registration_result = provisioning_client.register()

    if registration_result.status != "assigned":
        raise Exception("Invalid rgistration status: {}".result.status)

    client = IoTHubDeviceClient.create_from_symmetric_key(
        symmetric_key=device_key,
        hostname=registration_result.registration_state.assigned_hub,
        device_id=registration_result.registration_state.device_id,
    )
    return client
