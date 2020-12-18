# Looking at test logs and metrics in Azure Monitor

1. Open the `thief-ai` resource in the Azure portal
2. On the left pane, click "Logs" in the "Monitoring" section

Alternatively, use the "App insights logs" shortcut from `./scripts/get-shortcuts.sh`.

Tricks:
* You can run many queries safely by adding "| limit 20" at the end to take the first 20 results
* The | is a pipe, not an or operator.
* Order matters.  limiting to 200 and sorting is different than sorting and then limiting to 200
* `contains` is expensive.  Try to use `has` instead.
* `has` works with full words.  `contains` works with substrings.
* Before you do a `has` operation, try to limit the results first.

Example queries to run:

```
// All traces in a specific 2 minutre window
traces
| where timestamp > datetime(2020-11-06 02:13:30)
| where timestamp < datetime(2020-11-06 02:15:00)
| where customDimensions.deviceId == "nov4-1"
| sort by timestamp asc
```

```
// All traces in the last 2 days from out_of_order_message_tracker.py line 61
traces
| where timestamp > ago(2d)
| where customDimensions.deviceId == "nov4-1"
| where customDimensions.module == "out_of_order_message_tracker"
| where customDimensions.lineNumber == 61
| sort by timestamp desc
| limit 200
```

```
// Message send count graph, averaged over 15 minute intervals
customMetrics
| where timestamp > ago(7d)
| where customDimensions.deviceId == "nov9-3"
| where name == "sendMessageCountSent"
| summarize avg(value) by bin(timestamp, 15m)
```

```
// logs from client.py (Paho) that don't contain the words "PINGREQ" and "PINGRESP"
traces
| where timestamp > ago(7d)
| where customDimensions.deviceId == "nov9-3"
| where customDimensions.module == "client"
| where message !has "PINGREQ" and message !has "PINGRESP"
| sort by timestamp desc
| limit 200
```

