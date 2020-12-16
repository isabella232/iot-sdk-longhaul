# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

script_dir=$(cd "$(dirname "$0")" && pwd)

source ${script_dir}/_fetch-functions.sh

echo Fetching secrets
get-secret THIEF_SERVICE_CONNECTION_STRING THIEF-SERVICE-CONNECTION-STRING
get-secret THIEF_IOTHUB_NAME THIEF-IOTHUB-NAME
get-secret THIEF_EVENTHUB_CONNECTION_STRING THIEF-EVENTHUB-CONNECTION-STRING
get-secret THIEF_EVENTHUB_CONSUMER_GROUP THIEF-EVENTHUB-CONSUMER-GROUP
get-secret THIEF_APP_INSIGHTS_CONNECTION_STRING THIEF-APP-INSIGHTS-CONNECTION-STRING
echo Done fetching secrets

