# Copyright (c) Microsoft. All rights reserved.  # Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

# this is a temporary script that will hopefully disappear

set -e
script_dir=$(cd "$(dirname "$0")" && pwd)

LANGUAGE=py36
DEVICE_VERSION=2.4.0
SERVICE_VERSION=2.2.3
TAG=nov16

${script_dir}/build-image.sh --language ${LANGUAGE} --library service --version ${SERVICE_VERSION} --tag ${TAG}
${script_dir}/build-image.sh --language ${LANGUAGE} --library device --version ${DEVICE_VERSION} --tag ${TAG}

${script_dir}/run-container.sh --language ${LANGUAGE} --library service --version ${SERVICE_VERSION} --tag ${TAG} --pool ${TAG}
${script_dir}/run-container.sh --language ${LANGUAGE} --library device --version ${DEVICE_VERSION} --tag ${TAG} --pool ${TAG} --device_id ${TAG}-1
${script_dir}/run-container.sh --language ${LANGUAGE} --library device --version ${DEVICE_VERSION} --tag ${TAG} --pool ${TAG} --device_id ${TAG}-2
${script_dir}/run-container.sh --language ${LANGUAGE} --library device --version ${DEVICE_VERSION} --tag ${TAG} --pool ${TAG} --device_id ${TAG}-3

