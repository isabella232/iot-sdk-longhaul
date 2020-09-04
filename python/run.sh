az container delete --yes --name mycontainer --resource-group thief-runs
az container create \
    --resource-group thief-runs \
    --name mycontainer \
    --image ${LONGHAUL_CONTAINER_REGISTRY_HOST}/py38-device-linux \
    --registry-username ${LONGHAUL_CONTAINER_REGISTRY_USER} \
    --registry-password ${LONGHAUL_CONTAINER_REGISTRY_PASSWORD} \
    --restart-policy Never \
    --assign-identity ${LONGHAUL_USER_RESOURCE_ID}
