# Running Thief tests on Python

Tests have 2 components: a service app and a device app.  This document explains how to run thief tests by running both in parallel on your local dev environment.

## Setting up your environment for running the Python test apps

The Python test apps should run on any supported versions of Python, from 2.7 up to 3.8.

To set up dependencies to run the test, run the following commands:
```bash
pip install -r python/device/requirements.txt
pip install -r python/service/requirements.txt
pip install -e python/common/
```

After you do this, you may want to run `pip list` to make sure you're using the correct versions of `azure-iot-device` and `azure-iot-hub`.  In this example, you can see that I'm using the azure-iot-hub and azure-iot-device libraries from my local clone of the Python repo:
```
(longhaul) bertk@bertk-hp:~/repos/longhaul$ pip list | grep "azure"
azure-core                             1.8.1
azure-eventhub                         5.2.0
azure-iot-device                       2.2.0     /home/bertk/repos/python/azure-iot-device
azure-iot-hub                          2.2.1     /home/bertk/repos/python/azure-iot-hub
azure-iothub-provisioningserviceclient 1.2.0
opencensus-ext-azure                   1.0.4
(longhaul) bertk@bertk-hp:~/repos/longhaul$
```

## Running the tests

The best way to run the tests is with two bash windows, one for the device app and one for the eservice app.  Make sure to `source scripts/fetch-secrets.sh` for both windows.

In one windows, run `python python/device/device.py` and in the other window, run `python python/service/service.py`.

## default environment for local runs

`scripts/fetch_secrets.sh` populates environment variables for running locally.  This includes a default device ID and service pool name:

```
(longhaul) bertk@bertk-hp:~/repos/longhaul/docs$ printenv | grep "THIEF"
<< some variabled removed from this document >>
THIEF_DEVICE_ID=bertk_test_device
THIEF_SERVICE_POOL=bertk_desktop_pool
THIEF_REQUESTED_SERVICE_POOL=bertk_desktop_pool
(longhaul) bertk@bertk-hp:~/repos/longhaul/docs$
```
