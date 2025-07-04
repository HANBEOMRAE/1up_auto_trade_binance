# webhook_server.py

from flask import Flask, request, jsonify, render_template_string
from utils.trade_manager import handle_signal, check_exit_conditions
from utils import report
import threading
import time

app = Flask(__name__)
monitor_data = {}

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    symbol = data.get('symbol')
    signal = data.get('signal')
    if symbol and signal:
        threading.Thread(target=process_signal, args=(symbol, signal)).start()
        return jsonify({"status": "received"}), 200
    return jsonify({"error": "invalid payload"}), 400

def process_signal(symbol, signal):
    handle_signal(symbol, signal)
    while True:
        check_exit_conditions(symbol)
        from utils.monitor import update_monitor_data
        update_monitor_data(symbol)
        time.sleep(5)

@app.route('/monitor')
def monitor():
    from utils.monitor import get_monitor_data
    data = get_monitor_data()
    html = """
    <html>
    <head><meta http-equiv="refresh" content="30"></head>
    <body><h2>실시간 모니터링</h2><pre>{{ data }}</pre></body>
    </html>
    """
    return render_template_string(html, data=data)

@app.route('/report')
def report_view():
    report.init_report()
    data = report.get_report()
    html = """
    <html>
    <head><meta http-equiv="refresh" content="30"></head>
    <body><h2>트레이딩 리포트</h2><pre>{{ data }}</pre></body>
    </html>
    """
    return render_template_string(html, data=data)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
