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
    print(f"[WEBHOOK 수신] symbol: {symbol}, signal: {signal}")  # ✅ 로그 추가
    if symbol and signal:
        threading.Thread(target=process_signal, args=(symbol, signal)).start()
        return jsonify({"status": "received"}), 200
    print("[WEBHOOK 오류] 유효하지 않은 payload")  # ✅ 추가
    return jsonify({"error": "invalid payload"}), 400

def process_signal(symbol, signal):
    try:
        print(f"[PROCESS 시작] {symbol}, {signal}")  # ✅ 로그
        handle_signal(symbol, signal)
        print(f"[PROCESS 실행 완료] handle_signal 호출 완료")  # ✅ 로그
        while True:
            check_exit_conditions(symbol)
            from utils.monitor import update_monitor_data
            update_monitor_data(symbol)
            time.sleep(5)
    except Exception as e:
        print(f"[ERROR] process_signal 중 예외 발생: {e}")  # ✅ 예외 로그
