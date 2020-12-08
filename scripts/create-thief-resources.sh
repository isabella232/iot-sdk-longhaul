# !/bin/bash
# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

set -e

if [ "${THIEF_SUBSCRIPTION_ID}" == "" ]; then
    echo "ERROR: THIEF_SUBSCRIPTION_ID environment variable needs to be set"
    exit 1
fi

# region to use.  use `az account list-locations` for a list of options
THIEF_REGION=westus

# prefix to add to all resource names.
THIEF_PREFIX=${USER}3

# keyvault name.  set early since this is usually "the source of truth" on any variables
export THIEF_KEYVAULT_NAME=${THIEF_PREFIX}-thief-kv

##############################
# add the app insights az extension
##############################
echo az extension add --name application-insights

##############################
# create resoruce groups
##############################
export THIEF_RESOURCE_GROUP=${THIEF_PREFIX}-thief-rg
export THIEF_RUNS_RESOURCE_GROUP=${THIEF_PREFIX}-thief-runs-rg

BASE_CREATE_ARGS="\
    --subscription ${THIEF_SUBSCRIPTION_ID} \
    --location ${THIEF_REGION} \
    "
BASE_QUERY_ARGS="\
    --subscription ${THIEF_SUBSCRIPTION_ID} \
    "

echo az group create \
    ${BASE_CREATE_ARGS} \
    --name ${THIEF_RESOURCE_GROUP}
echo az group create \
    ${BASE_CREATE_ARGS} \
    --name ${THIEF_RUNS_RESOURCE_GROUP}

# from now on, everything we create will be in THIEF_RESOURCE_GROUP
BASE_CREATE_ARGS="\
    ${BASE_CREATE_ARGS} \
    --resource-group ${THIEF_RESOURCE_GROUP} \
    "
BASE_QUERY_ARGS="\
    ${BASE_QUERY_ARGS} \
    --resource-group ${THIEF_RESOURCE_GROUP} \
    "

##############################
# get our AD tenant
##############################

export THIEF_ACTIVE_DIRECTORY_TENANT="$(\
    az ad app list \
        --query [0].publisherDomain \
        -o tsv \
    )"

##############################
# create keyvault
##############################
echo az keyvault create \
    ${BASE_CREATE_ARGS} \
    --name ${THIEF_KEYVAULT_NAME}


##############################
# create container registry
##############################
export THIEF_CONTAINER_REGISTRY_SHORTNAME=${THIEF_PREFIX}thiefcr
echo az acr create \
    ${BASE_CREATE_ARGS} \
    --sku Standard \
    --admin-enabled true \
    --name ${THIEF_CONTAINER_REGISTRY_SHORTNAME}
export THIEF_CONTAINER_REGISTRY_PASSWORD="$(\
    az acr credential show \
        ${BASE_QUERY_ARGS} \
        --name ${THIEF_CONTAINER_REGISTRY_SHORTNAME} \
        --query passwords[0].value \
        -o tsv \
    )"
export THIEF_CONTAINER_REGISTRY_USER="$(\
    az acr credential show \
        ${BASE_QUERY_ARGS} \
        --name ${THIEF_CONTAINER_REGISTRY_SHORTNAME} \
        --query username \
        -o tsv \
    )"
export THIEF_CONTAINER_REGISTRY_HOST="$(\
    az acr show \
        ${BASE_QUERY_ARGS} \
        --name ${THIEF_CONTAINER_REGISTRY_SHORTNAME} \
        --query loginServer \
        -o tsv \
    )"


##############################
# create iothub
##############################
export THIEF_IOTHUB_NAME=${THIEF_PREFIX}-thief-hub
echo az iot hub create \
    ${BASE_CREATE_ARGS} \
    --name ${THIEF_IOTHUB_NAME} \
    --sku S1 \
    --unit 1
export THIEF_SERVICE_CONNECTION_STRING="$(\
    az iot hub show-connection-string \
        ${BASE_QUERY_ARGS} \
        --name ${THIEF_IOTHUB_NAME} \
        --policy-name iothubowner \
        --query connectionString \
        -o tsv \
    )"


##############################
# get eventhub strings
##############################
export THIEF_EVENTHUB_CONSUMER_GROUP='$default'
export THIEF_EVENTHUB_CONNECTION_STRING="$(\
    az iot hub connection-string show \
        --hub-name ${THIEF_IOTHUB_NAME} \
        --subscription ${THIEF_SUBSCRIPTION_ID} \
        --default-eventhub \
        --query connectionString \
        -o tsv \
    )"

##############################
# Create DPS instance
##############################
THIEF_DPS_INSTANCE_NAME=${THIEF_PREFIX}-thief-dps
echo az iot dps create \
    ${BASE_CREATE_ARGS} \
    --name ${THIEF_DPS_INSTANCE_NAME}
   

export THIEF_DEVICE_PROVISIONING_HOST="$(\
    az iot dps show \
        ${BASE_QUERY_ARGS} \
        --name ${THIEF_DPS_INSTANCE_NAME} \
        --query properties.deviceProvisioningHostName \
        -o tsv \
    )"
export THIEF_DEVICE_ID_SCOPE="$(\
    az iot dps show \
        ${BASE_QUERY_ARGS} \
        --name ${THIEF_DPS_INSTANCE_NAME} \
        --query properties.idScope \
        -o tsv \
    )"

THIEF_ENROLLMENT_ID=${THIEF_PREFIX}-thief-enrollment

echo az iot dps linked-hub create \
    ${BASE_CREATE_ARGS} \
    --dps-name ${THIEF_DPS_INSTANCE_NAME} \
    --connection-string "${THIEF_SERVICE_CONNECTION_STRING}"

echo az iot dps enrollment-group create \
    --resource-group ${THIEF_RESOURCE_GROUP} \
    --subscription ${THIEF_SUBSCRIPTION_ID} \
    --iot-hub-host-name ${THIEF_IOTHUB_NAME}.azure-devices.net \
    --dps-name ${THIEF_DPS_INSTANCE_NAME} \
    --enrollment-id ${THIEF_ENROLLMENT_ID} 


export THIEF_DEVICE_GROUP_SYMMETRIC_KEY="$(\
    az iot dps enrollment-group show \
        --resource-group ${THIEF_RESOURCE_GROUP} \
        --subscription ${THIEF_SUBSCRIPTION_ID} \
        --dps-name ${THIEF_DPS_INSTANCE_NAME} \
        --enrollment-id ${THIEF_ENROLLMENT_ID} \
        --show-keys \
        -o tsv \
        --query attestation.symmetricKey.primaryKey \
    )"


##############################
# Create app insights instance
##############################

export THIEF_APP_INSIGHTS_NAME=${THIEF_IOTHUB_NAME}
echo az monitor app-insights component create \
    ${BASE_CREATE_ARGS} \
    --app ${THIEF_APP_INSIGHTS_NAME}

export THIEF_APP_INSIGHTS_CONNECTION_STRING="$(\
    az monitor app-insights component show \
        ${BASE_QUERY_ARGS} \
        --app ${THIEF_APP_INSIGHTS_NAME} \
        -o tsv \
        --query connectionString \
    )"


##############################
# Creeate our managed identity
##############################
THIEF_IDENTITY_NAME=${THIEF_PREFIX}-container-identity
echo az identity create \
    ${BASE_CREATE_ARGS} \
    --name ${THIEF_IDENTITY_NAME}

export THIEF_USER_RESOURCE_ID="$(\
    az identity show \
        ${BASE_QUERY_ARGS} \
        --name ${THIEF_IDENTITY_NAME} \
        -o tsv \
        --query id \
    )"

THIEF_USER_OBJECT_ID="$(\
    az identity show \
        ${BASE_QUERY_ARGS} \
        --name ${THIEF_IDENTITY_NAME} \
        -o tsv \
        --query principalId \
    )"

##############################
# Give our managed identity the ability to read the keyvault 
# (so we can get secrets from inside the container).
##############################

echo az keyvault set-policy \
    ${BASE_QUERY_ARGS} \
    --name ${THIEF_KEYVAULT_NAME} \
    --secret-permissions get \
    --object-id ${THIEF_USER_OBJECT_ID}

##############################
# save secrets to the keyvault
##############################

function set-secret {
    bash_name=$1
    kv_name=$2
    value="$(printenv ${bash_name})"
    az keyvault secret set \
        --subscription ${THIEF_SUBSCRIPTION_ID} \
        --vault-name ${THIEF_KEYVAULT_NAME} \
        --name ${kv_name} \
        --value "$value" 
}

set-secret THIEF_SERVICE_CONNECTION_STRING THIEF-SERVICE-CONNECTION-STRING
set-secret THIEF_DEVICE_PROVISIONING_HOST THIEF-DEVICE-PROVISIONING-HOST
set-secret THIEF_DEVICE_ID_SCOPE THIEF-DEVICE-ID-SCOPE
set-secret THIEF_DEVICE_GROUP_SYMMETRIC_KEY THIEF-DEVICE-GROUP-SYMMETRIC-KEY
set-secret THIEF_EVENTHUB_CONNECTION_STRING THIEF-EVENTHUB-CONNECTION-STRING
set-secret THIEF_EVENTHUB_CONSUMER_GROUP THIEF-EVENTHUB-CONSUMER-GROUP
set-secret THIEF_APP_INSIGHTS_CONNECTION_STRING THIEF-APP-INSIGHTS-CONNECTION-STRING
set-secret THIEF_CONTAINER_REGISTRY_HOST THIEF-CONTAINER-REGISTRY-HOST
set-secret THIEF_CONTAINER_REGISTRY_PASSWORD THIEF-CONTAINER-REGISTRY-PASSWORD
set-secret THIEF_CONTAINER_REGISTRY_USER THIEF-CONTAINER-REGISTRY-USER
set-secret THIEF_CONTAINER_REGISTRY_SHORTNAME THIEF-CONTAINER-REGISTRY-SHORTNAME
set-secret THIEF_RUNS_RESOURCE_GROUP THIEF-RUNS-RESOURCE-GROUP
set-secret THIEF_USER_RESOURCE_ID THIEF-USER-RESOURCE-ID
set-secret THIEF_RESOURCE_GROUP THIEF-RESOURCE-GROUP
set-secret THIEF_SUBSCRIPTION_ID THIEF-SUBSCRIPTION-ID
set-secret THIEF_ACTIVE_DIRECTORY_TENANT THIEF-ACTIVE-DIRECTORY-TENANT
set-secret THIEF_IOTHUB_NAME THIEF-IOTHUB-NAME
set-secret THIEF_APP_INSIGHTS_NAME THIEF-APP-INSIGHTS-NAME

echo SUCCESS!
echo ""
echo Be sure to set these variables to use your new resources:
echo ""
echo export THIEF_SUBSCRIPTION_ID=${THIEF_SUBSCRIPTION_ID}
echo export THIEF_KEYVAULT_NAME=${THIEF_KEYVAULT_NAME}
echo ""
echo Then run ./fetch-secrets.sh to populate your environment

