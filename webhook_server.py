from flask import Flask, request, jsonify
from utils.trade_manager import handle_signal, check_exit_conditions
from utils.monitor import update_monitor_data
import threading
import time

app = Flask(__name__)
monitor_data = {}

@app.route('/')
def home():
    return '🚀 Webhook Server is Running', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        symbol = data.get('symbol')
        signal = data.get('signal')
        print(f"[WEBHOOK 수신] symbol: {symbol}, signal: {signal}")

        if symbol and signal:
            # 비동기 처리 (메인 서버는 바로 응답)
            threading.Thread(target=process_signal, args=(symbol, signal)).start()
            return jsonify({"status": "received"}), 200
        else:
            print("[WEBHOOK 오류] 유효하지 않은 payload")
            return jsonify({"error": "invalid payload"}), 400

    except Exception as e:
        print(f"[WEBHOOK 예외 발생] {e}")
        return jsonify({"error": "exception", "message": str(e)}), 400

def process_signal(symbol, signal):
    try:
        print(f"[PROCESS 시작] {symbol}, {signal}")
        handle_signal(symbol, signal)
        print(f"[PROCESS 실행 완료] handle_signal 호출 완료")

        while True:
            check_exit_conditions(symbol)
            update_monitor_data(symbol)
            time.sleep(5)

    except Exception as e:
        print(f"[PROCESS 예외] {e}")
