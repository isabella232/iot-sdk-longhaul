# !/bin/bash
# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

az container logs \
    --resource-group ${THIEF_RUNS_RESOURCE_GROUP}  \
    -n $1
