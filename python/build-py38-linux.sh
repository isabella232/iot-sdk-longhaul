az acr build -r ${LONGHAUL_CONTAINER_REGISTRY_SHORTNAME} -t py38-device-linux --build-arg BASE=${LONGHAUL_CONTAINER_REGISTRY_HOST}/py38-base-linux -f ./containers/Dockerfile.python.device.linux .
