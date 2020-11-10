# !/bin/bash
# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

az iot hub query \
    -l "${THIEF_SERVICE_CONNECTION_STRING}"  \
    -q "select \
            deviceId \
            , properties.reported.thief.runState \
            , properties.reported.thief.language \
            , properties.reported.thief.runTime \
            , properties.reported.thief.latestUpdateTimeUtc \
            , properties.reported.thief.languageVersion \
            , properties.reported.thief.sendMessageCountSent \
            , properties.reported.thief.sendMessageCountNotReceivedByService \
            , properties.reported.thief.sendMessageCountUnacked \
            , properties.reported.thief.sendMessageCountFailures \
            , properties.reported.thief.receiveMessageCountReceived \
            , properties.reported.thief.receiveMessageCountMissing \
        from devices \
        " \
    -o table

