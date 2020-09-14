# Resource usage:
The thief runtime uses a number of azure resources.

We need something to test against:
1. An IoTHub instance to test against.
2. A DPS instance to provision devices for testing

To analyze the performance of the libraries, we use:
3. A log analytics instance to hold telemetry
4. An application insights instance to accept the telemetry
5. A storage account to act as a backing store for the telemetry
_These resources are collectively known as Azure Monitor_

To actually run the tests, we need:
6. A key vault to hold secrets
7. A container registry to hold docker images
8. A resource group to hold the running containers.
9. Individual container instances for each run, one instance for the service SDK, and another instance for the device SDK.

