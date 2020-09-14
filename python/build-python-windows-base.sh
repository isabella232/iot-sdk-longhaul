set -e


function do_build {
    SHORT_VER=$1
    LONG_VER=$2
    az acr build \
        --registry ${THIEF_CONTAINER_REGISTRY_SHORTNAME} \
        --platform=windows \
        -t ${THIEF_CONTAINER_REGISTRY_HOST}/py${SHORT_VER}-windows-base \
        --build-arg BASE="python:${LONG_VER}-windowsservercore-1809" \
        --file containers/Dockerfile.python.windows.base \
        .
}

do_build 37 3.7.9
do_build 38 3.8.5
