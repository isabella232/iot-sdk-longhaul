# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
set -e

if [ "$#" -ne 2 ]; then
    echo "Usage:"
    echo "$0 {service-pool-name} {service-container-name}"
    exit 1
fi
    
SERVICE_POOL=$1
SERVICE_CONTAINER_NAME=$2

SERVICE_IMAGE=py38-linux-service-pypi-2.2.3

echo "creating service client container"
az container create \
    --resource-group ${THIEF_RUNS_RESOURCE_GROUP} \
    --name ${SERVICE_CONTAINER_NAME} \
    --image ${THIEF_CONTAINER_REGISTRY_HOST}/${SERVICE_IMAGE} \
    --environment-variables "THIEF_SERVICE_POOL=${SERVICE_POOL}" \
    --registry-username ${THIEF_CONTAINER_REGISTRY_USER} \
    --registry-password ${THIEF_CONTAINER_REGISTRY_PASSWORD} \
    --restart-policy Never \
    --assign-identity ${THIEF_USER_RESOURCE_ID} 

echo SUCCESS
