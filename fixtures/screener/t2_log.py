#!/usr/bin/env python3
"""A T2 fixture (004 audit fix 5): logs the mail IDs of each run to $T2_LOG, waits $T2_SLEEP seconds, answers deliver
for every mail. Standard library only."""
import json
import os
import sys
import time

envs = [json.loads(line) for line in sys.stdin if line.strip()]
with open(os.environ["T2_LOG"], "a") as fh:
    fh.write(json.dumps([e["mail_id"] for e in envs]) + "\n")
time.sleep(float(os.environ.get("T2_SLEEP", "0")))
for e in envs:
    print(json.dumps({"mail_id": e["mail_id"], "label": "deliver", "reason": "log"}))
