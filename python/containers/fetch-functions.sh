export THIEF_KEYVAULT_NAME="thief-kv"

echo "Getting token"
while [ "$token" == "" ]; do 
    response=$(curl --silent 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net' -H Metadata:true)
    echo "response is ${response}"
    token=$(echo $response | jq -r '.access_token')
    if [ "$token" == "" ]; then sleep 2; fi
done

function get-secret {
    bash_name=$1
    kv_name=$2
    echo "Getting ${bash_name}"
    value=$(curl --silent "https://${THIEF_KEYVAULT_NAME}.vault.azure.net/secrets/${kv_name}/?api-version=2016-10-01" -H "Authorization: Bearer $token")
    value=$(echo $value | jq -r '.value')
    export ${bash_name}=${value}
}

