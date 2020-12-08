# Copyright (c) Microsoft. All rights reserved.  # Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
set -e
script_dir=$(cd "$(dirname "$0")" && pwd)

function usage {
    echo "USAGE: ${0} [--platform platform] --langauge language_short_name --library library [--source library_source] --version library_version [--tag extra_tag]"
    echo "  ex: ${0} --platform linux --language py37 --library device --version 2.3.0"
    exit 1
}

source ${script_dir}/_parse_args $*

if [ "${LANGUAGE_SHORT_NAME}" == "" ]; then
    echo "ERROR: language_short_name is required"
    usage
fi
if [ "${LIBRARY}" == "" ]; then
    echo "ERROR: library is required"
    usage
fi
if [ ${LIBRARY_VERSION} == "" ]; then
    echo "ERROR: library_version is required"
    usage
fi

source ${script_dir}/_get_base_env ${PLATFORM} ${LANGUAGE_SHORT_NAME}

IMAGE_NAME=${LANGUAGE_SHORT_NAME}-${PLATFORM}-${LIBRARY}-${LIBRARY_SOURCE}-${LIBRARY_VERSION} 
DOCKER_FILE=${LANGUAGE}/containers/Dockerfile.${LANGUAGE}.${PLATFORM}.${LIBRARY}.${LIBRARY_SOURCE}

echo building ${IMAGE_NAME}
echo from ${DOCKER_FILE}

TAGS="-t ${THIEF_CONTAINER_REGISTRY_HOST}/${IMAGE_NAME}"
if [ "${EXTRA_TAG}" != "" ]; then
    echo "also taging as ${IMAGE_NAME}:${EXTRA_TAG}"
    TAGS="${TAGS} -t ${THIEF_CONTAINER_REGISTRY_HOST}/${IMAGE_NAME}:${EXTRA_TAG}"
fi

pushd ${script_dir}/..
az acr build \
    --registry ${THIEF_CONTAINER_REGISTRY_SHORTNAME} \
    --subscription ${THIEF_SUBSCRIPTION_ID} \
    --platform=${PLATFORM} \
    ${TAGS} \
    --build-arg BASE="${THIEF_CONTAINER_REGISTRY_HOST}/${BASE_DOCKER_IMAGE_NAME}"  \
    --build-arg LIBRARY_VERSION=${LIBRARY_VERSION} \
    --file ${DOCKER_FILE} \
    .

