# webhook_server.py (또는 기존 Flask 서버 파일) 일부 추가

from flask import Flask, request, jsonify
from utils.trade_manager import handle_signal, check_exit_conditions
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
        # 현재 포지션 상태 모니터링 데이터 수집
        from utils.monitor import update_monitor_data
        update_monitor_data(symbol)
        time.sleep(5)

@app.route('/monitor')
def get_monitor_data():
    from utils.monitor import get_monitor_data
    return jsonify(get_monitor_data())

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)