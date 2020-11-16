# !/bin/bash
# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

IOTHUB_RESOURCE_ID="/resourceGroups/${THIEF_RESOURCE_GROUP}/providers/Microsoft.Devices/IotHubs/${THIEF_IOTHUB_NAME}"
URI_PREFIX="https://ms.portal.azure.com/#@${THIEF_ACTIVE_DIRECTORY_TENANT}/resource/subscriptions/${THIEF_SUBSCRIPTION_ID}"
IOTHUB_URI_PREFIX="${URI_PREFIX}${IOTHUB_RESOURCE_ID}"

echo "Thief resource group:"
echo "${URI_PREFIX}/resourceGroups/${THIEF_RESOURCE_GROUP}/overview"
echo ""
echo "thief-runs resource group:"
echo "${URI_PREFIX}/resourceGroups/${THIEF_RUNS_RESOURCE_GROUP}/overview"
echo ""
echo "Device list:"
echo "${IOTHUB_URI_PREFIX}/DeviceExplorer"
echo ""
echo "App insights logs:"
echo "${URI_PREFIX}/resourceGroups/${THIEF_RESOURCE_GROUP}/providers/microsoft.insights/components/${THIEF_APP_INSIGHTS_NAME}/logs"
echo ""


