# !/bin/bash
# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

container_list=$(az container list \
    --resource-group ${THIEF_RUNS_RESOURCE_GROUP} \
    --query "[].name" \
    -o tsv)

for container in $container_list; do
    az container show \
        --resource-group ${THIEF_RUNS_RESOURCE_GROUP} \
        --name ${container} \
        --query "{Name: name, State: containers[0].instanceView.currentState.state}" \
        -o tsv
done
    
