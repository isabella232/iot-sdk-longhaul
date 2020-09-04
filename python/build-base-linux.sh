az acr run -r ${LONGHAUL_CONTAINER_REGISTRY_SHORTNAME} --platform=linux --file containers/base-linux.yaml  . || exit 1
