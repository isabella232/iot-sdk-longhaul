THIEF_KEYVAULT_NAME="thief-kv"

echo "Getting token"
while [ "$token" == "" ]; do 
    token=$(curl --silent 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net' -H Metadata:true | jq -r ".access_token")
    [ $? -eq 0 ] || sleep 2
done

function get-secret {
    bash_name=$1
    kv_name=$2
    echo "Getting ${bash_name}"
    value=$(curl --silent "https://${THIEF_KEYVAULT_NAME}.vault.azure.net/secrets/${kv_name}/?api-version=2016-10-01" -H "Authorization: Bearer $token")
    echo $value
    value=$(echo $value | jq -r '.value')
    echo $value
    export ${bash_name}=${value}
}

echo Fetching secrets
get-secret THIEF_SERVICE_CONNECTION_STRING THIEF-SERVICE-CONNECTION-STRING
get-secret THIEF_EVENTHUB_CONNECTION_STRING THIEF-EVENTHUB-CONNECTION-STRING
get-secret THIEF_EVENTHUB_CONSUMER_GROUP THIEF-EVENTHUB-CONSUMER-GROUP
echo Done fetching secrets

