az acr run -r ${LONGHAUL_CONTAINER_REGISTRY_SHORTNAME} --platform=windows --file containers/base-windows.yaml  . || exit 1
