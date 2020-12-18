# test control

When  the device app wants to control how the service app behaves, it does so by setting values inside `properties/reported/thief/testControl`

## c2d

```json
  {
    "reported": {
      "thief": {
        "testControl": {
          "c2d": {
            "maxFillerSize": 16384,
            "messageIntervalInSeconds": 20,
            "send": true
          }
        }
      }
    }
  }
```

| field | format | meaning |
| - | - | - |
| `maxFillerSize` | integer | Maximum number of characters to include in the `filler` property of test c2d messages |
| `messageIntervalinSeconds` | integer | How many seconds to wait between each test c2d message.  Be careful: setting this too low may induce c2d throttling and c2d limits are hub-wide, so one app that sets this too low can cause all tests on the hub to fail. |
| `send` | boolean | `true` to enable c2d testing, otherwise `false`. |

