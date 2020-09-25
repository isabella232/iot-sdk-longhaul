# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
set -e


function do_build {
    SHORT_VER=$1
    LONG_VER=$2
    az acr build \
        --registry ${THIEF_CONTAINER_REGISTRY_SHORTNAME} \
        --platform=linux \
        -t ${THIEF_CONTAINER_REGISTRY_HOST}/py${SHORT_VER}-linux-base \
        --build-arg BASE="python:${LONG_VER}-slim-buster" \
        --file containers/Dockerfile.python.linux.base \
        .
}

do_build 27 2.7.18
do_build 35 3.5.9
do_build 36 3.6.12
do_build 37 3.7.9
do_build 38 3.8.5
