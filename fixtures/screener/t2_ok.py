#!/usr/bin/env python3
"""A T2 fixture (004 T019): reads envelope lines on stdin, answers deliver for CASE-DELIVER, unneeded for CASE-UNNEEDED,
and nothing for the rest. Standard library only."""
import json
import sys

for line in sys.stdin:
    env = json.loads(line)
    for case, label in (("CASE-DELIVER", "deliver"), ("CASE-UNNEEDED", "unneeded")):
        if case in line:
            print(json.dumps({"mail_id": env["mail_id"], "label": label, "reason": case}))
            break
