# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
set -e

if [ "$#" -ne 2 ]; then
    echo "Usage:"
    echo "$0 {service-pool-name} {device-id}"
    exit 1
fi

SERVICE_POOL=$1
DEVICE_ID=$2

DEVICE_IMAGE=py36-linux-device-pypi-2.3.0
DEVICE_CONTAINER_NAME=${DEVICE_ID}-device

echo "creating device client container"
az container create \
    --resource-group ${THIEF_RUNS_RESOURCE_GROUP} \
    --name ${DEVICE_CONTAINER_NAME} \
    --image ${THIEF_CONTAINER_REGISTRY_HOST}/${DEVICE_IMAGE} \
    --environment-variables "THIEF_DEVICE_ID=${DEVICE_ID}" "THIEF_REQUESTED_SERVICE_POOL=${SERVICE_POOL}" \
    --registry-username ${THIEF_CONTAINER_REGISTRY_USER} \
    --registry-password ${THIEF_CONTAINER_REGISTRY_PASSWORD} \
    --restart-policy Never \
    --assign-identity ${THIEF_USER_RESOURCE_ID} 

echo SUCCESS

