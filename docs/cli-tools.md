
## To look at list of running containers

`./scripts/get-container-list.sh` will return you a list of containers along with their status.
This info is based on the containers themselves and includes device and service containers.

A container which is still running but not communicating with IoTHub will show as "Running".
The `nov4-1-device` container below is one such example.
The first clue is that `nov4-service` is `Terminated`, but `nov1-device` and it's peers are all listed as `Running`.
Without the service app, the device apps should all stop running.

```
(longhaul) bertk@bertk-hp:~/repos/longhaul/scripts$ ./get-container-list.sh
nov12-03-device Terminated
nov12-1-device  Terminated
nov12-2-device  Terminated
nov12-service   Terminated
nov4-1-device   Running
nov4-2-device   Running
nov4-3-device   Running
nov4-service    Terminated
nov9-1-device   Running
nov9-2-device   Running
nov9-3-device   Running
(longhaul) bertk@bertk-hp:~/repos/longhaul/scripts$
```

## To look at running tests

`./scripts/get-run-list.sh` will give you a list of tests and various status metrics.
This info is based on the last reported properties from the device apps and only includes device_ids.

A failed test might still show up as running if it can't update it's `RunState` before it finishes.
The `LastUpdateTimeUtc` field is a better indication that a test is running.
One example is the `nov4-1` device.
You can see that the `LastUpdateTimeUtc` value is a few days old, which indicates that the `nov4-1` test is actuall failed.

```
(longhaul) bertk@bertk-hp:~/repos/longhaul/scripts$ ./get-run-list.sh
DeviceId             Language    LanguageVersion    LatestUpdateTimeUtc         RunState    RunTime
-------------------  ----------  -----------------  --------------------------  ----------  ------------------------
nov12-03             python      3.7.9              2020-11-12T20:21:21.243393  failed      0:15:03.956707
nov9-2               python      3.8.5              2020-11-10T14:24:48.834140  running     16:24:16.386984
oct-23-3             python      3.6.12                                         running     12 days, 2:00:00.339536
nov12-2              python      3.7.9              2020-11-12T20:18:27.255120  failed      0:15:04.180302
nov9-1               python      3.8.5              2020-11-10T14:25:10.321047  running     16:26:13.498433
nov12-1              python      3.7.9              2020-11-12T20:16:44.635109  failed      0:15:03.985116
oct23-5              python      3.6.12                                         failed      10 days, 23:47:02.511040
nov4-2               python      3.6.12             2020-11-10T14:24:22.881796  running     5 days, 19:18:03.913449
nov4-3               python      3.6.12             2020-11-10T14:25:07.525961  running     5 days, 19:17:29.188863
nov9-3               python      3.8.5              2020-11-10T14:24:41.988242  running     16:23:13.889745
oct-23-4             python      3.6.12                                         running     12 days, 1:58:26.445770
nov4-1               python      3.6.12             2020-11-10T14:24:36.065010  running     4 days, 12:10:01.907967
(longhaul) bertk@bertk-hp:~/repos/longhaul/scripts$
```

## To look at default test status

If you want to look at more reported properties for a running test, you can use `./scripts/get-run-details.sh`.

If we look at the `nov12-03` test, we can see that the `exitReason` indicates a pairing failure.

```
(longhaul) bertk@bertk-hp:~/repos/longhaul/scripts$ ./get-run-detail.sh nov12-03
[
  {
    "deviceId": "nov12-03",
    "thief": {
      "configPairingRequestSendIntervalInSeconds": 30,
      "configPairingRequestTimeoutIntervalInSeconds": 900,
      "configPropertyUpdateIntervalInSeconds": 60,
      "configReceiveMessageFillerSize": 16384,
      "configReceiveMessageIntervalInSeconds": 2,
      "configReceiveMessageMissingMessageAllowedFailureCount": 10,
      "configSendMessageArrivalAllowedFailureCount": 10,
      "configSendMessageArrivalFailureIntervalInSeconds": 3600,
      "configSendMessageBacklogAllowedFailureCount": 200,
      "configSendMessageExceptionAllowedFailureCount": 10,
      "configSendMessageOperationsPerSecond": 1,
      "configSendMessageThreadCount": 10,
      "configSendMessageUnackedAllowedFailureCount": 200,
      "configWatchdogFailureIntervalInSeconds": 300,
      "exitReason": "No resopnse to pairing requests after trying for 900 seconds",
      "language": "python",
      "languageVersion": "3.7.9",
      "latestUpdateTimeUtc": "2020-11-12T20:21:21.243393",
      "osRelease": "#1 SMP Fri Feb 21 04:01:19 UTC 2020",
      "osType": "Linux",
      "receiveMessageCountMissing": 0,
      "receiveMessageCountReceived": 0,
      "runEndUtc": "2020-11-12T20:21:21.237630+00:00",
      "runStartUtc": "2020-11-12T20:06:17.286650+00:00",
      "runState": "failed",
      "runTime": "0:15:03.956707",
      "sdkVersion": "2.4.0",
      "sendMessageCountFailures": 0,
      "sendMessageCountInBacklog": 0,
      "sendMessageCountNotReceivedByService": 30,
      "sendMessageCountReceivedByService": 0,
      "sendMessageCountSent": 30,
      "sendMessageCountUnacked": 0
    }
  }
]
```

## To look at console output from the container

This is not particularly relible compared to Azure Monitor logs, but you can look at the most recent console output from a container using `./scripts/get-container-logs.sh`

This is particularly useful if a container fails and you suspect that Azure Monitor doesn't have the logs, maybe because:
* the container failed in startup and logging to Azure Monitor hasn't started yet, or
* the container failed, and logging to Azure Monitor has either stopped or failed.

_Note_: this command takes the _container name_, not the device id.  e.g. The device_id `nov12-03` is probably  being called in the container named `nov12-03-device`, so pass `nov12-03-device` as the parameter.

```
(longhaul) bertk@bertk-hp:~/repos/longhaul/scripts$ ./get-container-logs.sh nov12-03-device
 F INFO:thief.app_base:Thread send_message_thread #4 is exited
2020-11-12T20:21:21.3862322Z stderr F INFO:thief.app_base:Thread send_message_thread #5 is exited
2020-11-12T20:21:21.3862322Z stderr F INFO:thief.app_base:Thread update_thief_properties_thread is exited
2020-11-12T20:21:21.3902464Z stderr F INFO:thief.app_base:Thread send_message_thread #1 is exited
2020-11-12T20:21:21.4241924Z stderr F INFO:thief.app_base:Thread send_message_thread #7 is exited
2020-11-12T20:21:21.6608017Z stderr F INFO:thief.app_base:Thread send_message_thread #0 is exited
2020-11-12T20:21:21.6996805Z stderr F INFO:thief.app_base:Thread send_message_thread #9 is exited
```

You can also see this log by finding your container in the thief-runs resource group in Azure portal.  Click the container in list, then select "Containers" on the left pane and then select the "Logs" tab in the middle of the right pane.

Use `./scripts/get-shortcuts.sh` if you need help finding this group.

