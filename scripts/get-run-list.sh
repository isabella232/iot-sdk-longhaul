# !/bin/bash
# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

az iot hub query \
    -l "${THIEF_SERVICE_CONNECTION_STRING}"  \
    --subscription $THIEF_SUBSCRIPTION_ID \
    -q "select \
            deviceId \
            , properties.reported.thief.sessionMetrics.runState \
            , properties.reported.thief.systemProperties.language \
            , properties.reported.thief.sessionMetrics.runTime \
            , properties.reported.thief.sessionMetrics.latestUpdateTimeUtc \
            , properties.reported.thief.systemProperties.languageVersion \
        from devices \
        " \
    -o table

