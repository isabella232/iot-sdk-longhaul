# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
set -e

AZURE_IOT_DEVICE_VERSION=2.1.4

function do_build {
    PYTHON_VERSION=$1
    az acr build \
        --registry ${THIEF_CONTAINER_REGISTRY_SHORTNAME} \
        --platform=linux \
        -t ${THIEF_CONTAINER_REGISTRY_HOST}/py${PYTHON_VERSION}-linux-device-pypi-${AZURE_IOT_DEVICE_VERSION} \
        --build-arg BASE="${THIEF_CONTAINER_REGISTRY_HOST}/py${PYTHON_VERSION}-linux-base" \
        --build-arg AZURE_IOT_DEVICE_VERSION=${AZURE_IOT_DEVICE_VERSION} \
        --file containers/Dockerfile.python.linux.device.pypi \
        .
}

do_build 36

