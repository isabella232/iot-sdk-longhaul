set -e

AZURE_IOT_SERVICE_VERSION=2.2.1

function do_build {
    PYTHON_VERSION=$1
    az acr build \
        --registry ${THIEF_CONTAINER_REGISTRY_SHORTNAME} \
        --platform=linux \
        -t ${THIEF_CONTAINER_REGISTRY_HOST}/py${PYTHON_VERSION}-linux-service-pypi-${AZURE_IOT_SERVICE_VERSION} \
        --build-arg BASE="${THIEF_CONTAINER_REGISTRY_HOST}/py${PYTHON_VERSION}-linux-base" \
        --build-arg AZURE_IOT_SERVICE_VERSION=${AZURE_IOT_SERVICE_VERSION} \
        --file containers/Dockerfile.python.linux.service.pypi \
        .
}

do_build 38

