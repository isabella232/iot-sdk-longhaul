# --retry, --retry-delay, and --connect-timeout needed here because the environment might not be compeltely spun up at this point. 
# later curl calls don't seem to need this
token=$(curl --silent --retry 5 --retry-delay 5 --connect-timeout 10 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net' -H Metadata:true | jq -r ".access_token")

function get_secret {
    secret_name=$1
    raw=$(curl --silent "https://thief-kv.vault.azure.net/secrets/${secret_name}/?api-version=2016-10-01" -H "Authorization: Bearer $token")
    value=$(echo "${raw}" | jq -r ".value")
    echo ${value}
}

echo Fetching secrets
export APPLICATIONINSIGHTS_CONNECTION_STRING=$(get_secret APPLICATIONINSIGHTS-CONNECTION-STRING)
export LONGHAUL_PROVISIONING_HOST=$(get_secret LONGHAUL-PROVISIONING-HOST)
export LONGHAUL_ID_SCOPE=$(get_secret LONGHAUL-ID-SCOPE)
export LONGHAUL_GROUP_SYMMETRIC_KEY=$(get_secret LONGHAUL-GROUP-SYMMETRIC-KEY)
echo Done fetching secrets

