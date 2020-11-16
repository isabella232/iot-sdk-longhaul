# Setting up your Thief environment.

The scripts currently require a Debian-based Linux distro (Debian, Ubuntu, or Mint).

## Step 1. Install the Azure CLI.

To install the Azure CLI, it should be one command:

```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
```

If you need help, full instructions for installing the CLI are [here](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli-apt?view=azure-cli-latest)

If bash can't find `az` command, you may need to add ~/bin to your path.
```bash
export PATH=$PATH:~/bin
```

## Step 2. Login to to the azure CLI

Use the `az login` command to log in to your azure account.

```bash
az login
```

## Step 3. Fetch Thief environment variables.

Use `source scripts/fetch-secrets.sh` to fetch the necessary secrets from the Thief keyvault.

**__Note: Since this script populates the bash environment, you will need to run this script for every new bash prompt you open__**


```
(longhaul) bertk@bertk-hp:~/repos/longhaul$ source scripts/fetch-secrets.sh
Setting THIEF_DEVICE_ID
setting THIEF_SERVICE_POOL
setting THIEF_REQUESTED_SERVICE_POOL
Fetching THIEF_SERVICE_CONNECTION_STRING
Fetching THIEF_DEVICE_PROVISIONING_HOST
Fetching THIEF_DEVICE_ID_SCOPE
Fetching THIEF_DEVICE_GROUP_SYMMETRIC_KEY
Fetching THIEF_EVENTHUB_CONNECTION_STRING
Fetching THIEF_EVENTHUB_CONSUMER_GROUP
Fetching THIEF_APP_INSIGHTS_CONNECTION_STRING
Fetching THIEF_CONTAINER_REGISTRY_HOST
Fetching THIEF_CONTAINER_REGISTRY_PASSWORD
Fetching THIEF_CONTAINER_REGISTRY_USER
Fetching THIEF_CONTAINER_REGISTRY_SHORTNAME
Fetching THIEF_RUNS_RESOURCE_GROUP
Fetching THIEF_USER_RESOURCE_ID
Fetching THIEF_RESOURCE_GROUP
Fetching THIEF_SUBSCRIPTION_ID
Fetching THIEF_ACTIVE_DIRECTORY_TENANT
Done fetching secrets
(longhaul) bertk@bertk-hp:~/repos/longhaul$
```

