# Reported properties

## System Properties

System properties are set one time at the beginning of the run and used to record details on the environment that the device client is running in.

```json
  {
    "reported": {
      "thief": {
        "systemProperties": {
          "language": "python",
          "languageVersion": "3.8.2",
          "osRelease": "#34~18.04.2-Ubuntu SMP Thu Oct 10 10:36:02 UTC 2019",
          "osType": "Linux",
          "sdkGithubBranch": null,
          "sdkGithubCommit": null,
          "sdkGithubRepo": null,
          "sdkVersion": "2.4.0"
        }
      }
    }
  }
```

| field | format | meaning |
| - | - | - |
| `language` | string | Language name, one of `python`, `node`, `.NET`, `c`, or `java` |
| `languageVersion` | string | Version of the language, such as `3.7`, `8`, etc.  Format depends on `language`. |
| `osRelease` | string | Release version of the OS.  Format depends on `osType` |
| `osType` | string | one of `Windows` or `Linux`.  Others will be added as necessary.  |
| `sdkGithubRepo` | string | If testing against code cloned from GitHub, name of repo, such as `Azure/azure-iot-sdk-python`.  Excluded if testing against code installed from a package repository. |
| `sdkGithubBranch` | string | If testing against code cloned from Github, name of branch, such as `master` Excluded if testing against code installed from a package repository. |
| `sdkGithubCommit` | string | If testing against code cloned from GitHub, sha of commit, such as `731f8fe`.  Excluded if testing against code installed from a package repository. |
| `sdkVersion` | string | Version of the device sdk, such as `2.4.0`.  Format is currently arbitrary and used only for display, so this could be freeform, such as `client 2.4.0 with mqtt 1.9.3` |

`sdkVersion` is only required if the code is running against a released SDK.
`sdkGithubRepo`, `sdkGithubBranch`, and `sdkGithubCommit` are only required if the code is running against an unreleased SDK.

## `language` and `languageVersion` formats

The following rules apply to the langauge and langaugeVersion fields:

| `langauge` | `languageVersion` rules | examples |
| - | - |
| Python | For cpython, use the PEP 440 major.minor.micro version number, followed by "async" if using the asyncio API. | `3.8.2` and `3.8.2 async` |
| node | not yet defined | |
| .NET | not yet defined | |
| c | not yet defined ||
| java | not yet defined ||

## `osType` and `osRelease` formats

`osRelease` should follow the Lq


## Test Confiuration

Test configuration is set one time at the beginning of the run and used to record details on how the test is configured.

```json
  {
    "reported": {
      "thief": {
        "config": {
          "pairingRequestSendIntervalInSeconds": 30,
          "pairingRequestTimeoutIntervalInSeconds": 900,
          "receiveMessageIntervalInSeconds": 20,
          "receiveMessageMaxFillerSize": 16384,
          "receiveMessageMissingMessageAllowedFailureCount": 100,
          "reportedPropertiesUpdateAllowedFailureCount": 100,
          "reportedPropertiesUpdateIntervalInSeconds": 10,
          "reportedPropertiesVerifyFailureIntervalInSeconds": 3600,
          "sendMessageArrivalAllowedFailureCount": 10,
          "sendMessageArrivalFailureIntervalInSeconds": 3600,
          "sendMessageBacklogAllowedFailureCount": 200,
          "sendMessageExceptionAllowedFailureCount": 10,
          "sendMessageOperationsPerSecond": 1,
          "sendMessageThreadCount": 10,
          "sendMessageUnackedAllowedFailureCount": 200,
          "thiefPropertyUpdateIntervalInSeconds": 60,
          "watchdogFailureIntervalInSeconds": 300
        }
      }
    }
  }
```

### pairing configuration

These numbers define operational parameters for the pairing process

| field | format | meaning |
| - | - | - |
| `pairingRequestSendIntervalInSeconds` | integer | When pairing, how many seconds to wait for a response before re-sending the pairing request |
| `pairingRequestTimeoutIntervalInSeconds` | integer | When pairing, how many seconds in total to attempt pairing before failing |

### general test configuration

These numbers define operational parameters for execution of the test harness.

| field | format | meaning |
| - | - | - |
| `thiefPropertyUpdateIntervalInSeconds` | integer | How often to update reported properties while running the tests.  This only applies to `sessionMetrics` and `testMetrics` properties |
| `watchdogFailureIntervalInSeconds` | integer | How often to check thread watchdogs.  If an individual thread is inactive for this many seconds, the test fails.  Exact definition of "inactive" is arbitrary and may depend on implementation. |

### c2d test configuration

These numbers define operational parameters for testing c2d.
The name `receiveMessage` is used for these properties even though the specific SDK may use a funtion with a different name, such as `receive_meeesage` or  `getNextIncomingMessageAsync`

| field | format | meaning |
| - | - | - |
| `receiveMessageIntervalInSeconds` | integer | When testing c2d, how many seconds to wait between c2d message.  This only applies to test c2d messages and does not apply to serverAck messages. |
| `receiveMessageMaxFillerSize` | integer | When testing c2d, how many characters, max, to add to c2d message as "filler" |
| `receiveMessageMissingMessageAllowedFailureCount` | integer | When testing c2d, how many mesages are allowed to be "missing" before the test fails. |

### reported property test configuration

These numbers define operational parameters for testing reported properties.

| field | format | meaning |
| - | - | - |
| `reportedPropertiesUpdateAllowedFailureCount` | integer | Count of reported property updates that are allowed to fail without causing a test-run failure |
| `reportedPropertiesUpdateIntervalInSeconds` | integer |  How often to update reported properties.  This only applies to properties under `testContent` and does not apply to things like `testMetrics` and `sessionMetrics and `testControl`. |
| `reportedPropertiesVerifyFailureIntervalInSeconds` | integer | How many seconds are allowed to elapse before a reported property that doesn't arrive at the service is considered a failure. |

### telemetry test configuratoin

These numbers define operational parameters for testing telemetry.  The name `sendMessage` is used in this context even though the specific SDK may use a function with a different name such as `send_message` or `sendEventAsync`

| field | format | meaning |
| - | - | - |
| `sendMessageOperationsPerSecond` | integer | How many `sendMessage` operations should be run per second |
| `sendMessageThreadCount` | integer | How many thrads can be running parallel `sendMessage` operations.  Some test implementations may not need multiple threads. |
| `sendMessageUnackedAllowedFailureCount` | integer | How may `sendMessage` calls are alloed to not complete before the test fails.  "unacked" and "not complete" both mean that the transport did not complete the send operation and did not fail.  The most likely cause is a message lost in transit (no PUBACK) |
| `sendMessageArrivalFailureIntervalInSeconds` | integer | Number of seconds allowed to elapse between sending a telemetry message and receiving the `serverAck` before the operation is considered failed. |
|`sendMessageArrivalAllowedFailureCount` | integer | How many messages are alloed to get lost in transit before the test fails. |
| `sendMessageBacklogAllowedFailureCount` | integer | How many messages are allowed to be queued inside the test app before the test fails.  "queued" means "scheduled to be sent but not yet in transit.  Some test implementations may not queue messages in the test app, so this value may not always be validated |
| `sendMessageExceptionAllowedFailureCount` | integer | How many `sendMessage` operations  are allowed to raise exceptions (or return failures) before the test fails. |

