# !/bin/bash
# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

if [ "${BASH_SOURCE-}" = "$0" ]; then
    echo "You must source this script: \$ source $0" >&2
    exit 33
fi

THIEF_KEYVAULT_NAME="thief-kv"

function get-secret {
    bash_name=$1
    kv_name=$2
    echo "Fetching ${bash_name}"
    value=$(az keyvault secret show --vault-name ${THIEF_KEYVAULT_NAME} --name ${kv_name} | jq -r ".value")
    export ${bash_name}=${value}
}

# This script is intended for developer workstations.  When these tests runs in the cloud,
# they use a different mechanism to get secrets.
#
# Since this is a developer workstation, set the device ID and run IDs so the developer runs
# all communicate with each other instead of accidentally pairing with service apps that are
# running in the cloud.
echo "Setting THIEF_DEVICE_ID"
export THIEF_DEVICE_ID=${USER}_test_device
echo "Setting THIEF_SERVICE_APP_RUN_ID"
export THIEF_SERVICE_APP_RUN_ID=${USER}_service_app_run_id
echo "setting THIEF_REQUESTED_SERVICE_APP_RUN_ID"
export THIEF_REQUESTED_SERVICE_APP_RUN_ID=${THIEF_SERVICE_APP_RUN_ID}

get-secret THIEF_SERVICE_CONNECTION_STRING THIEF-SERVICE-CONNECTION-STRING
get-secret THIEF_DEVICE_PROVISIONING_HOST THIEF-DEVICE-PROVISIONING-HOST
get-secret THIEF_DEVICE_ID_SCOPE THIEF-DEVICE-ID-SCOPE
get-secret THIEF_DEVICE_GROUP_SYMMETRIC_KEY THIEF-DEVICE-GROUP-SYMMETRIC-KEY
get-secret THIEF_EVENTHUB_CONNECTION_STRING THIEF-EVENTHUB-CONNECTION-STRING
get-secret THIEF_EVENTHUB_CONSUMER_GROUP THIEF-EVENTHUB-CONSUMER-GROUP
get-secret THIEF_AI_CONNECTION_STRING THIEF-AI-CONNECTION-STRING
get-secret THIEF_CONTAINER_REGISTRY_HOST THIEF-CONTAINER-REGISTRY-HOST
get-secret THIEF_CONTAINER_REGISTRY_PASSWORD THIEF-CONTAINER-REGISTRY-PASSWORD
get-secret THIEF_CONTAINER_REGISTRY_USER THIEF-CONTAINER-REGISTRY-USER
get-secret THIEF_CONTAINER_REGISTRY_SHORTNAME THIEF-CONTAINER-REGISTRY-SHORTNAME
get-secret THIEF_RUNS_RESOURCE_GROUP THIEF-RUNS-RESOURCE-GROUP
get-secret THIEF_USER_RESOURCE_ID THIEF-USER-RESOURCE-ID
echo Done fetching secrets

