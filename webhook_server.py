from flask import Flask, request, jsonify
from utils.trade_manager import handle_signal
from utils.monitor import update_monitor_data
import threading
import time

app = Flask(__name__)

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
            signal = signal.upper()  # ✅ 대문자 변환 필수!
            threading.Thread(target=process_signal, args=(symbol, signal)).start()
            return jsonify({"status": "received"}), 200
        else:
            print("[WEBHOOK 오류] 유효하지 않은 payload")
            return jsonify({"error": "invalid payload"}), 400

    except Exception as e:
        print(f"[WEBHOOK 예외 발생] {e}")
        return jsonify({"error": "exception", "message": str(e)}), 400

# 신호 처리 및 수익률 모니터링 스레드
def process_signal(symbol, signal):
    try:
        print(f"[PROCESS 시작] {symbol}, {signal}")
        handle_signal(symbol, signal)
        print(f"[PROCESS 실행 완료] handle_signal 호출 완료")

        # 무한 루프 대신 제한된 루프로 변경 (예: 5분간 모니터링)
        for _ in range(60):
            update_monitor_data(symbol)
            time.sleep(5)

    except Exception as e:
        print(f"[PROCESS 예외] {symbol} - {e}")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)