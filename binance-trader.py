from utils.trade_manager import set_leverage, handle_signal, check_exit_conditions
from utils.logger import Logger
import time

logger = Logger()

def main():
    symbol = "BTCUSDT"
    set_leverage(symbol, 5)

    logger.log("자동매매 시작")

    while True:
        # 예시: 신호 수신 처리
        signal = 'BUY'  # 실제 환경에서는 웹훅 또는 다른 모듈에서 신호 수신
        handle_signal(symbol, signal)
        check_exit_conditions(symbol)
        time.sleep(1)

if __name__ == "__main__":
    main()