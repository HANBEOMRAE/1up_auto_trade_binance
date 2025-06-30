# report.py
import json
import os

REPORT_FILE = "trade_report.json"

def init_report():
    if not os.path.exists(REPORT_FILE):
        data = {
            "tp1_count": 0,
            "tp2_count": 0,
            "stop_loss_count": 0
        }
        with open(REPORT_FILE, "w") as f:
            json.dump(data, f)

def update_report(event):
    with open(REPORT_FILE, "r") as f:
        data = json.load(f)
    if event in data:
        data[event] += 1
    with open(REPORT_FILE, "w") as f:
        json.dump(data, f)

def get_report():
    with open(REPORT_FILE, "r") as f:
        return json.load(f)