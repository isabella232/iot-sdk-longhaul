# units
* Memory is always in MB
* "interval" and "latency" variables are always in seconds
* "frequency" variables are always per second
* CPU percentage is overall, so 100% of one core on a 4-core system is 25% (follows 'top' command behavior)

# open qeustions
* Do we need context switch info?  If we have CPU% is that enough?
* Naming: we are using telemetry to report stats, and we are also testing telemetry.  We don't want the messsages that we send for the purposes of testing to also contain stats becuause we want stats to flow at a different rate than the rate of test messages.  
    

# Properties

## "System" properties for things that apply regardless of what operations are being tested

Desired:
```json
{
  "longhaulTelemetrySendIntervalInSeconds": 11
}
```

Reported:
```json
{
  "frameworkVersion": "CPython 3.7.2",

  "os": "Linux",
  "osVersion": "#34~18.04.2-Ubuntu SMP Thu Oct 10 10:36:02 UTC 2019",

  "systemArchitecture": "x86_64",
  "totalSystemMemoryInMb": 8192,

  "runStart": "2020-07-12 14:14:00",
  "runEnd": "2020-07-17 14:15:321",
  "runState": "completed",

  "sdkLanguage": "python",
  "sdkRepo": "Azure/azure-iot-sdk-python",
  "sdkSha": "12fad0147368eee0708c5039cfc3ab6aa0b781ae",
  "sdkBranch": "master", 
  "sdkVersion": "2.1.2",

  "transport": "mqtt"
}
```

## "D2C" properties apply to D2C operations which are being tested and measured

Desired:
```json
{
  "d2cTimeoutIntervalInSeconds": 30,
  "d2cSendsPerSecond": 10,
  "d2cFailuresAllowed": 0
}
```


Reported: 
```json
{
  "d2cFailureCount": 0,
  "d2cSuccessCount": 1020,
}
```

# Telemetry schema for reporting longhaul stats.  

The telemetry that we send to test D2C performance is arbitrary and does not follow this schema

System telemetry, sent according to longhaulTelemetrySendInterval

```json
{
  "processCpuUsagePercent": 25.3,
  "processInvoluntaryContextSwitchesPerSecond": 2999.9,
  "processVoluntaryContextSwitchesPerSecond": 302.7,
  "processResidentMemoryInMb": 109.2,
  "processAvailableMemoryInMb":72.2,  
  "systemAvailableMemoryInMb": 3096,
  "systemFreeMemoryInMb": 2096
}
```

D2C telemetry, sent according to longhaulTelemetrySendInterval
```json
{
  "averageD2cRoundtripLatencyToGatewayInSeconds": 0.257, 
  "d2cInFlightCount": 2
}
```

