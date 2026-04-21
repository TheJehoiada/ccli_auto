"""
check_usage.py
Returns exit code 0 if the FreeShow usage.json has any CCLI entries, 1 if not.
Called by ccli_weekly_report.cmd before deciding whether to copy and report.
"""
import json
import os
import re
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import variables

def has_ccli(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "ccli" in str(k).lower():
                if isinstance(v, (int, float)) and v > 0:
                    return True
                if isinstance(v, str) and re.search(r"\b\d{3,}\b", v):
                    return True
            if has_ccli(v):
                return True
    elif isinstance(obj, list):
        return any(has_ccli(i) for i in obj)
    return False

try:
    with open(variables.freeshow_usage_source, "r", encoding="utf-8") as f:
        data = json.load(f)
    sys.exit(0 if has_ccli(data) else 1)
except Exception as e:
    print(f"check_usage: error reading usage file — {e}")
    sys.exit(1)
