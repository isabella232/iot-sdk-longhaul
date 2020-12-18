# Azure Monitor tagging

All telemetry sent to Azure Monitor, whether traces, metrics, or spans, should follow this guide.

## Overloading of default dimensions

A few built-in Azure Monitor fields are overloaded:

| field | overload |
| - | - |
| `cloud_RoleName` | Either `device` or `service` |
| `cloud_RoleInstance` | `runId` for the process producing telemetry. |

## customDimensions

```json
  {
    "sdkLanguageVersion": "3.8.2",
    "sdkLanguage": "python",
    "sdkVersion": "2.2.3",
    "lineNumber": "291",
    "sessionId": "a8b42042-6a08-4bcd-85c3-3d96d25ccd64",
    "fileName": "service.py",
    "deviceId": "bertk_test_device_4",
    "process": "MainProcess",
    "module": "service",
    "osType": "Linux",
    "poolId": "bertk_desktop_pool",
    "runId": "3c4d68ed-e8ac-422e-85c6-5937a92c1c43",
    "level": "INFO",
    "hub": "thief-hub-1.azure-devices.net",
    "transport": "MQTT"
  }
```

## Fields always available for device and service apps

| field | format | meaning |
| - | - | - |
| `sdkLanguageVersion` | string | see same variable in [metrics.md](./metrics.md) |
| `sdkLanguage` | string | see same variable in [metrics.md](./metrics.md) |
| `sdkVersion` | string | see same variable in [metrics.md](./metrics.md) |
| `osType` | string | see same variable in [metrics.md](./metrics.md) |
| `poolId` | string | name of service app pool being used |
| `runId` | guid | `runId` for app generating metrics |
| `hub` | string | name of hub being used for testing |

## Fields available only for device apps

| field | format | meaning |
| - | - | - |
| `transport` | string | transport being used, if available (may not be available on service apps).  One of `mqtt`, `mqttws`, `amqp`, or `amqpws` |

## Fields available for device and maybe available for service app.

Service app will include these fields if they are available, but not all service app tracing has these available.  For example, global service app setup is not specific to a device ID, so it doesn't include the `deviceId` field.

| field | format | meaning |
| - | - | - |
| `deviceId` | string | device ID being used. |
| `pairingId` | guid | pairing ID for device and service relationshiop. |

## Automatically populated fields which are meaningless for us

| field | format | meaning |
| - | - | - |
| `sessionId` | string | populated by python Azure Monitor wrappers. |
| `process` | string | populated by Python Azure Monitor wrappers. |

## Automaticly populated fields for Python traces.

| field | format | meaning |
| - | - | - |
| `level` | string | debug level for generated message |
| `module`| string | module generating message (without path and extension) |
| `lineNumber` | integer | line number in source file which is generating this message |
| `fileName` | string | filename generating message (with path and extension) |

## Metric names

Metrics sent to Azure Monitor are defined in [metrics.md](./metrics.md).
