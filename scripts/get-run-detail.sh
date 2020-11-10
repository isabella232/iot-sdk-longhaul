# !/bin/bash
# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

DEVICE_ID=$1
if [ "$DEVICE_ID" == "" ]; then
    echo "Usage: $0 <device_id>"
    exit 1
fi


az iot hub query \
    -l "${THIEF_SERVICE_CONNECTION_STRING}"  \
    -q "select \
            deviceId \
            , properties.reported.thief \
        from devices \
        where deviceId = '${DEVICE_ID}' \
        " 

