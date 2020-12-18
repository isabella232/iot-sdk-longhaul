# pairing process

The pairing process is a handshake with 4 steps, all done with reported and desired properties:
1. A device app says "I need a partner" by setting its reported `serviceRunId` to `null`.
2. A service app says "I'm available" by setting the devices desired `serviceRunId` to the service app's `runId` value.
3. The device app says "I choose you" by setting its reported `serviceRunId` to the `runId` of the chosen service
4. The service app finalizes the pairing by setting the devices desired `acceptedPairing` value.

Once `acceptedPairing` is set, the pairing is complete and test operation can begin.

## Step 1: device sets reported properties to start pairing.

The pairing stars with the device setting `properties/reported/thief/pairing/serviceRunId` to `null`.
This indicates that it doesn't have a paired service app and welcomes service apps to "throw their hat into the ring" by setting desired properties as described in step 2.

```json
  {
    "reported": {
      "thief": {
        "pairing": {
          "deviceRunId": "4d41c744-94bf-40ac-89bc-06f28b4dc9d2",
          "pairingId": "176e7469-9755-4a7a-af74-85f735f0f296",
          "requestedServicePool": "bertk_desktop_pool",
          "serviceRunId": null
        }
      }
    }
  }
```

| field | format | meaning |
| - | - | - |
| `deviceRunId` | guid | `runId` for the running device app instance.  re-generated each time the app launches |
| `pairingId` | guid | ID representing an individual pairing attempt.  The device generates a new pairingId each time it tries to pair (or re-pair) with a service app |
| `requestedServicePool` | string | free-form name for the pool of service apps which are known to be valid.  This is the the only value that a service app uses to decide if it can pair with the device app |
| `serviceRunId` | guid | runId of the selected service app. Since this step is starting the pairing process, this is set to `null` because no service app has been chosen yet. |

## Step 2: service sets desired properties to say that its available.

A service app can tell the device app that it's available for pairing by setting `properties/desired/thief/pairing` values as described below.

```json
  {
    "desired": {
      "thief": {
        "pairing": {
          "pairingId": "176e7469-9755-4a7a-af74-85f735f0f296",
          "serviceRunId": "23ebf618-41e2-40d7-9964-a16ae9762a1c",
          "acceptedPairing": null
        }
      }
    }
  }
```

| field | format | meaning |
| - | - | - |
| `pairingId` | guid | ID representing an individual pairing attempt.  This is included so the device can know that it's not looking at an old set of desired properties. |
| `serviceRunId` | guid | `runId` for the service app that wants to pair with the device app |
| `acceptedPairing` | guid | `none` in this step because there is no agreement (yet) between the device app and service app |

## Step 3: device sets reported properties to select service instance.

The device app selects a service instance by setting `properties/reported/thief/pairing/serviceRunId` to the service app's runId value.

```json
  {
    "reported": {
      "thief": {
        "pairing": {
          "pairingId": "176e7469-9755-4a7a-af74-85f735f0f296",
          "serviceRunId": "23ebf618-41e2-40d7-9964-a16ae9762a1c"
        }
      }
    }
  }
```

| field | format | meaning |
| - | - | - |
| `pairingId` | guid | ID representing an individual pairing attempt.  Used like a "transaction ID" so the service can know that the device is still working on the same pairing operation |
| `serviceRunId` | guid | `runId` for the service app that was selected by the device app |

## Step 4: service sets desired properties to acknowledge that the pairing is complete.

When the service app sees that it has been selected,  it sets `properties/desired/thief/pairing/acceptedPairing` to "{pairingId},{serviceRunId}".
When the deivce sees this value, it knows that the pairing is complete and it can begin communicating with the service instance.
```json
  {
    "desired": {
      "thief": {
        "pairing": {
          "pairingId": "176e7469-9755-4a7a-af74-85f735f0f296",
          "serviceRunId": "23ebf618-41e2-40d7-9964-a16ae9762a1c",
          "acceptedPairing": "176e7469-9755-4a7a-af74-85f735f0f296, 23ebf618-41e2-40d7-9964-a16ae9762a1c",
        }
      }
    }
  }
```

| field | format | meaning |
| - | - | - |
| `pairingId` | guid | ID representing an individual pairing attempt.  Used like a "transaction ID" so the device app can know that the service app is still working on the same pairing operation |
| `serviceRunId` | guid | `runId` for the service app that was selected by the device app |
| `acceptedPairing` | (guid, guid) tuple | pairingId and serviceRunId for the accepted pairing. |

*note* The decision to have the acceptedPairing be a pair of guids was done to be as explicit as possible.  Having it set to  <`pairingId`>, <`serviceRunId`> was done to make the meaning, effectively, "I, `serviceRunId`, accept the pairing with this device for the `pairindId` operation."

## Unpairing
When a device wishes to unpair with a service app, it can simply replace `properties/reported/thief/pairing/serviceRunId` with a new value or with `null`.
When the service app sees that this value has changed, it will consider the device to be "unpaird" and stop working with that device.


