# Test Content

## serviceAckResponse c2d messages

`serviceAckResponse` messages are used to acknowledge events that the device app is unable to observe.
A `serviceAckId` could represent a telemetry message, a reported property write, or any other operation that needs to be verified by the service.
The `serviceAckId` is created by the device and passed to the service app as part of an operation.
When the service app verifies that operation, it returns the `serviceAckId` to the device inside a `serviceAckResponse` c2d message.
When the device receives an operation's `serviceAckId` back, it knows that the operation has been verified by the service app.
Because c2d is a fairly expensive operation, the service app gathers `serviceAckIds` in batches to send to the device app.

```json
  {
    "thief": {
      "cmd": "serviceAckResponse",
      "serviceRunId": "ca3ceadb-ac57-4cca-8f9b-c5d1e4dc189d",
      "pairingId": "3887e97e-6f06-484b-a5bf-1753913866d3",
      "serviceAcks": [
        {
          "serviceAckId": "3fcef06d-1240-4323-bc20-da664cbcdac7"
        },
        {
          "serviceAckId": "7157db96-4da3-4192-a789-9082ee782a5c"
        }
      ]
    }
  }
```

| field | format | meaning |
| - | - | - |
| `cmd` | string | Must be `serviceAckResponse` to indicate that this c2d message is a `serviceAckResponse`. |
| `serviceRunId` | guid | `serviceRunId` for the service app sending the `serviceAckResponse`. |
| `pairingId` |  guid | `pairingId` that represents the pairing between the device and service apps. |
| `serviceAcks` | array | Array of service acks |
| `serviceAckId` | guid | `serviceAckId` being acknowledged |

## test telemetry with serviceAckRequest properties

Telemetry is tested by sending a telemetry message with a `serviceAckId`.
When the service app receives telemetry with `cmd` == `serviceAckRequest`, it acknowledges receipt by returning the `serviceAckId` to the device app inside a `serviceAckResponse` c2d message.

```json
  {
    "thief": {
      "cmd": "serviceAckRequest",
      "serviceAckId": "c9d16db5-18b9-44c5-a8a6-c50623b6b050",
      "serviceAckType": "telemetry",
      "sessionMetrics": {
        "exitReason": null,
        "latestUpdateTimeUtc": "2020-12-14T21:29:18.545099",
        "pairingId": "66197452-3132-491a-8977-154665ab853c",
        "runEndUtc": null,
        "runStartUtc": "2020-12-14T21:29:13.569490+00:00",
        "runState": "running",
        "runTime": "0:00:04.975579"
      },
      "systemHealthMetrics": {
        "processBytesInAllHeaps": 2641256448,
        "processCpuPercent": 0.0,
        "processPrivateBytes": 25743360,
        "processWorkingSet": 40263680,
        "processWorkingSetPrivate": 25743360
      },
      "testMetrics": {
        "receiveMessageCountMissing": 0,
        "receiveMessageCountReceived": 0,
        "reportedPropertiesCountAdded": 0,
        "reportedPropertiesCountAddedAndVerifiedByServiceApp": 0,
        "reportedPropertiesCountRemoved": 0,
        "reportedPropertiesCountRemovedAndVerifiedByServiceApp": 0,
        "sendMessageCountFailures": 0,
        "sendMessageCountInBacklog": 0,
        "sendMessageCountNotReceivedByServiceApp": 0,
        "sendMessageCountReceivedByServiceApp": 0,
        "sendMessageCountSent": 0,
        "sendMessageCountUnacked": 0
      }
    }
  }
```

| field | format | meaning |
| - | - | - |
| `cmd | string | must be `serviceAckRequest` in order to cause a `serviceAckResponse` from the service. |
| `serviceAckId` | guid | guid allocated by the device app to represent this operation.  Returned in a `serviceAckResponse` message when this telemetry is received by the service app. |
| `serviceAckType` | string | must be `telemetry` to represent the type of service ack to return. |
| `sessionMetrics` | dict | Arbitrary content to inflate the message size for testing.  See [metrics.md](./metrics.md) for details |
| `systemHealthMetrics` | dict | Arbitrary content to inflate the message size for testing.  See [metrics.md](./metrics.md) for details |
| `sendMessageCountUnacked` | dict | Arbitrary content to inflate the message size for testing.  See [metrics.md](./metrics.md) for details |

## test reported properties

Reported properties are tested by adding an object to `properties/reported/thief/testContent/reportedPropertyTest` with a pair of `serviceAckId` guids.
When the service app observes the add of the property, it returns the `addServiceAckId` to the device app inside a `serviceAckResponse` c2d message.
When the device receives the `addServiceAckId` back from the service, it proceeds to remove the property.
When the service app observes the removal of hte property, it returns the `removeServiceAckId` to the device app inside a `saerviceAckResponse` c2d message.

```json
  {
    "reported": {
      "thief": {
        "testContent": {
          "reportedPropertyTest": {
            "prop_2": {
              "addServiceAckId": "1c984433-5593-4a62-bfbf-b4dae6c72bb9",
              "removeServiceAckId": "18fdb3ec-50fd-4406-a575-8d81a46ed3a1"
            },
            "prop_3": {
              "addServiceAckId": "d0f4dea5-e937-4e59-bd08-3efcfff452e0",
              "removeServiceAckId": "891cdf2d-992a-4e8d-a7de-ccb624da7796"
            }
          }
        }
      }
    }
  }
```

| field | format | meaning |
| - | - | - |
| `reportedPropertyTest` | dict | object containing all test content for testing reported properties |
| `prop_x` | dict | object representing a single reported property operation. First operation is held in `prop_1`, followed by `prop_2` and so on. |
| `addServiceAckId` | guid | `serviceAckId` to return to the device app when the service app first observes this property being added. |
| `removeServiceAckId` | guid | `serviceAckId` to return to the device app when the service app first observes this property being removed. |

## test c2d messages

When c2d testing is enabled (via `properties/reported/thief/testControl/c2d`), the service sends a continuous stream of c2d messages using the configuration stored in the `testControl/c2d` structure.
The device app uses the `testC2dMessageIndex` value to keep track of messages received from the service, including dropped messages.

```json
  {
    "thief": {
      "cmd": "testC2d",
      "serviceRunId": "3e5917f2-3625-4431-92fa-45b184a25498",
      "pairingId": "64186b84-48d3-4831-8f09-85edc31dc133",
      "firstMessage": true,
      "testC2dMessageIndex": 0,
      "filler": "5EGILSQJHVG7T3WK8N59ITEJZRAS1IGEGCRMC3MGA"
    }
  }
```

| field | format | meaning |
| - | - | - |
| `cmd` | string | must be `testC2d` |
| `serviceRunId` | guid |  `runid` for the service app sending this message |
| `pairingId` | guid | `pairingid` representing the device/service app pairing |
| `firstMessage` | boolean | `true` if this is the first message in the sequence, otherwise `false` |
| `testC2dMessageIndex` | integer | index for this message, starts at 0 and increments by 1 for each message sent |
| `filler` | string | random text to pad out the c2d message to an arbitrary size.  Maximum filler size set inside the `testControl/c2d` structure. |
