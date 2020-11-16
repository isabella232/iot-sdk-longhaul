# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

script_dir=$(cd "$(dirname "$0")" && pwd)

source ${script_dir}/_fetch-functions.sh

echo Fetching secrets
get-secret THIEF_DEVICE_PROVISIONING_HOST THIEF-DEVICE-PROVISIONING-HOST
get-secret THIEF_DEVICE_ID_SCOPE THIEF-DEVICE-ID-SCOPE
get-secret THIEF_DEVICE_GROUP_SYMMETRIC_KEY THIEF-DEVICE-GROUP-SYMMETRIC-KEY
get-secret THIEF_APP_INSIGHTS_CONNECTION_STRING THIEF-APP-INSIGHTS-CONNECTION-STRING
echo Done fetching secrets

