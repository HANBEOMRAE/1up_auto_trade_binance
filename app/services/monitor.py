# app/services/monitor.py

import threading
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from binance import ThreadedWebsocketManager
from app.clients.binance_client import get_binance_client
from app.state import monitor_states, get_state
from app.config import POLL_INTERVAL

logger = logging.getLogger("monitor")
logger.setLevel(logging.INFO)


def _handle_order_update(msg):
    """
    ENTRY 가격·수량을 WebSocket으로 감지하여 monitor_states에 기록합니다.
    TP/SL 주문은 buy.py / sell.py 쪽에서 모두 처리하므로 여기서는 주문을 생성하지 않습니다.
    """
    o = msg.get("o", {})
    if msg.get("e") == "ORDER_TRADE_UPDATE" and \
       o.get("X") == "FILLED" and o.get("S") == "BUY" and o.get("o") == "MARKET":
        symbol = msg.get("s")  # ex. "ETHUSDT"
        state = get_state(symbol)
        price = float(o.get("L", 0))
        qty   = float(o.get("q", 0))
        now   = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
        state.update({
            "symbol":        symbol,
            "entry_price":   price,
            "position_qty":  qty,
            "entry_time":    now,
            "first_tp_done": False,
            "second_tp_done":False,
            "sl_done":       False,
            "current_price": price,
            "pnl":           0.0
        })
        logger.info(f"[Monitor] Entry detected for {symbol}: {qty}@{price} at {now}")


def _poll_price_loop():
    client = get_binance_client()

    while True:
        # 각 심볼별 상태 순회
        for symbol, state in monitor_states.items():
            entry = state.get("entry_price", 0.0)
            qty   = state.get("position_qty", 0.0)

            # 진입 정보가 없으면 건너뜀
            if not symbol or entry <= 0 or qty <= 0:
                continue

            try:
                ticker = client.futures_symbol_ticker(symbol=symbol)
                # 리스트 반환 시, 해당 심볼 정보 찾기
                if isinstance(ticker, list):
                    ticker = next((t for t in ticker if t.get("symbol") == symbol), None)
                    if ticker is None:
                        raise ValueError(f"No ticker data for {symbol}")

                current = float(ticker["price"])
                now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
                pnl_percent = (current / entry - 1) * 100

                state.update({
                    "current_price": current,
                    "pnl":           pnl_percent,
                    "last_update":   now
                })
                logger.info(f"[Monitor] {symbol} price {current}, PnL {pnl_percent:.2f}% at {now}")
            except Exception:
                logger.exception(f"[Monitor] Error fetching price for {symbol}")

        time.sleep(POLL_INTERVAL)


def start_monitor():
    client = get_binance_client()
    twm = ThreadedWebsocketManager(
        api_key=client.API_KEY,
        api_secret=client.API_SECRET
    )

    try:
        twm.start()
        twm.start_futures_user_socket(callback=_handle_order_update)
        logger.info("[Monitor] WebSocket manager started")
    except Exception:
        logger.exception("[Monitor] Failed to start WebSocket manager")
        return

    thread = threading.Thread(target=_poll_price_loop, daemon=True)
    thread.start()
    logger.info("[Monitor] Price polling thread started")