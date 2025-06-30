from flask import Flask, request, jsonify
from utils.trade_manager import handle_signal, check_exit_conditions
import threading
import time

app = Flask(__name__)

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
        time.sleep(1)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)