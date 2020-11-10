import os
import subprocess
import json
import time
import datetime

devices_to_check = ["nov4-1", "nov4-2", "nov4-3"]

last_report_time = datetime.datetime.min
report_interval = datetime.timedelta(minutes=30)

lastValues = None
while True:
    values = {}

    if datetime.datetime.now() - last_report_time > report_interval:
        print(str(datetime.datetime.now()))
        last_report_time = datetime.datetime.now()

    process = subprocess.run(
        "az iot hub query -l '{}' -q 'select deviceId, properties.reported.thief.runTime from devices'".format(
            os.environ["THIEF_SERVICE_CONNECTION_STRING"]
        ),
        capture_output=True,
        encoding="utf-8",
        shell=True,
    )
    if process.returncode != 0:
        print("process returned {}".format(process.returncode))
        print("STDOUT:")
        print(process.stdout)
        print("STDERR:")
        print(process.stderr)
    else:
        query_results = json.loads(process.stdout)
        for rec in query_results:
            if "deviceId" and "runTime" in rec:
                values[rec["deviceId"]] = rec["runTime"]

        if lastValues:
            for device_id in devices_to_check:
                if values[device_id] == lastValues[device_id]:
                    print(
                        "{}: device {} has same runTime as last time: {}".format(
                            datetime.datetime.now(), device_id, values[device_id]
                        )
                    )

        lastValues = values
        time.sleep(300)
