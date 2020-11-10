# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
set -e
script_dir=$(cd "$(dirname "$0")" && pwd)

echo "---------------"
echo "building $1 $2"
echo "---------------"

source ${script_dir}/_get_base_env $1 $2

pushd ${script_dir}/..
az acr build \
    --registry ${THIEF_CONTAINER_REGISTRY_SHORTNAME} \
    --platform=${PLATFORM} \
    -t ${THIEF_CONTAINER_REGISTRY_HOST}/${BASE_DOCKER_IMAGE_NAME} \
    ${BASE_DOCKER_BUILD_ARGS} \
    --file ${BASE_DOCKER_DOCKERFILE} \
    .

