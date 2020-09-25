This document describes communication between Thief device and service apps and IotHub.  There are several ways that this communication occurs:

* C2D and D2C are used for communication between the two proceses.  
* Reported properties are used for reporting operational metrics of the _device_ app
* Desired properties are used for reporting operational metrics of the _service_ app


## Process communication (C2D and D2C)
In many cases, the C2D and D2C payloads have the same format for the same functionality.  For example, the heartbeat sent by the service app has an identical JSON schema to the hartbeat that is sent by the device app.

In some cases, there is a request/respones paradigm.  For example, a device might send pingback D2C message, which is used to ask the service to respond, and hte service responds with a pingbackResponse C2D mesage.

In other cases, the communication is "fire-and-forget" such as heartbeat messages.  When the device sends a heartbeat message, it does not expect a response from the service app.

All thief messages are inside a "thief" object, which is part of the message payload.

### heartbeat

heartbeat messages are used to inform your corresponding app that you"re still running.  i
If an app doesn"t receive a hertbeat within some failure interval, it should exit with failure.  

example:
```json
"thief": {
  "cmd": "heartbeat",
  "heartbeatId": 17118
  }
```

fields:
| name              | type                  | value/meaning                                                     |
|-------------------|-----------------------|-------------------------------------------------------------------|
| cmd               | string                | `heartbeat`                                                       |
| heartbeatId       | integer               | id printed with logging output.  Can be used to correlate logs    |

### pingback

A pingback message is a request for your corresponding app to return a pingbackResponse to signal reception of the message.

example:
```json
"thief": {
    "cmd": "pinback",
    "messageId": "27cee6a8-6ff6-484c-ba2e-6b53335e5fea"
}
```

fields:
| name              | type                  | value/meaning                                                     |
|-------------------|-----------------------|-------------------------------------------------------------------|
| cmd               | string                | `pingback`                                                        |
| messageId         | string(guid)          | unique ID for the message being sent.                             |

### pingbackResponse

A pingbackResponse is used to indicate reception of one or more pingback messages.  The messageeIds are grouped into an array to limit the number of C2D messages that are sent.  A client will typically wait one second before sending a pingbackResponse and include all guids that were recevied within that second.  

example:
```json
"thief": {
    "cmd": "pingbackResponse",
    "messageIds": [
        "27cee6a8-6ff6-484c-ba2e-6b53335e5fea", 
        "cf434228-1790-42e1-a095-9ce7ce6b0883", 
        "aa2a5a76-13a6-4b10-a689-a37cb736eb3b"
    ]
}
```

fields:
| name              | type                  | value/meaning                                                     |
|-------------------|-----------------------|-------------------------------------------------------------------|
| cmd               | string                | `pingbackResponse`                                                |
| messageIds        | array(string(guid))   | array of pingback guids                                           |

## Common operation metrics

These metrics apply to both device and service apps.


fields:
| name              | type                  | value/meaning                                                     |
|-------------------|-----------------------|-------------------------------------------------------------------|
| runStart          | datetime              | date and time that test run began                                 |
| runTime           | timedelta             | amount of time the test has been running so far                   |
| runEnd            | datetime              | date and time that the test run finished                          |
| runState          | string                | "waiting", "running", "failed", or "complete"                     |
| exitReason        | string                | if run is failed, the reason for the failure as a freeform string |
| heartbeats        | structure             | defined below                                                     |
| pingbacks         | structure             | defined below                                                     |

hearbeat fields:
| name              | type                  | value/meaning                                                     |
|-------------------|-----------------------|-------------------------------------------------------------------|
| sent              | integer               | count of heartbeat messages sent                                  |
| received          | integer               | count of heartbeat messages received                              |

pingback fields:
| name              | type                  | value/meaning                                                     |
|-------------------|-----------------------|-------------------------------------------------------------------|
| requestsSent      | integer               | count of pingback rquests sent                                    |
| responsesReceived | integer               | count of pingback responses received                              |
| requestsReceived  | integer               | count of pingback requests received                               |
| responsesSent     | integer               | count of pingback responses sent                                  |

**__NOTE__** pingbackResponse counts are based on number of pinbackResponse messages sent, not the number of pingback messages that are being acknowledged.  These numbers are different because one pingbackResponse message can acknowledge multiple pingback requests.


## Reported Properties - operational metrics

Reported properties are used to return operational metrics from the device app to the iothub.

example:

```json
"thief": {
    "device": {
        "runStart": "2020-09-21 16:09:09.746644", 
        "runTime": "0:10:52.883547", 
        "runState": "running", 
        "exitReason": None, 
        "heartbeats": {
            "sent": 65, 
            "received": 54
        }, 
        "pingbacks": {
            "requestsSent": 1946, 
            "responsesReceived": 521, 
            "requestsReceived": 0, 
            "responsesSent": 0
        }, 
        "d2c": {
            "totalSuccessCount": 1946, 
            "totalFailureCount": 0
        }
    }
}
 ```

Base fields are defined in "Common operation metrics" above.

additional fields:
| name              | type                  | value/meaning                                                     |
|-------------------|-----------------------|-------------------------------------------------------------------|
| d2c               | structure             | defined below                                                     |

d2c fields:
| name              | type                  | value/meaning                                                     |
|-------------------|-----------------------|-------------------------------------------------------------------|
| totalSuccessCount | integer               | total number of d2c messages sent and acknowledged                |
| totalFailureCount | integer               | total number of d2c messages failed                               |



## Desired Properties - operational metrics

Desired properties are used to return operational metrics for the service app to the iothub.
This is technically a mususe of the desired properties feature because these are not actually _desired_ values.

example:
```json
"thief": {
    "service": {
        "runStart": "2020-09-21 16:09:05.355446",
        "runTime": "0:11:08.550589",
        "runState": "running",
        "exitReason": None,
        "heartbeats": {
            "sent": 67,
            "received": 62
        },
        "pingbacks": {
            "requestsSent": 0,
            "responsesReceived": 0,
            "requestsReceived": 1844,
            "responsesSent": 655
        }
    }
}
```

Base fields are defined in "Common operation metrics" above.


## Desired Properties - configuration

Desired properties can also be used to configure the thief run.  This functionality has not been defined yet.
