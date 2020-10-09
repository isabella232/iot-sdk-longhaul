# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
export THIEF_KEYVAULT_NAME="thief-kv"

TOKEN_FAILURES_ALLOWED=10
token_failures=0

echo "Getting token"
while [ "$token" == "" ]; do 
    echo "calling curl"
    response=$(curl --silent 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net' -H Metadata:true)
    token=$(echo $response | jq -r '.access_token')
    if [ "$token" == "" ]; then 
        let token_failures+=1
        if [ $token_failures == $TOKEN_FAILURES_ALLOWED ]; then
            echo "TOO MANY FAILURES. ABORTING"
            exit 1
        else
            echo "Failed ${token_failures} out of ${TOKEN_FAILURES_ALLOWED} allowed failures.  Trying again."
            sleep 2
        fi
    fi
done

function get-secret {
    bash_name=$1
    kv_name=$2
    echo "Getting ${bash_name}"
    value=$(curl --silent "https://${THIEF_KEYVAULT_NAME}.vault.azure.net/secrets/${kv_name}/?api-version=2016-10-01" -H "Authorization: Bearer $token")
    value=$(echo $value | jq -r '.value')
    export ${bash_name}=${value}
}

