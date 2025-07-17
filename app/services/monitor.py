# app/services/monitor.py

import threading
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from binance import ThreadedWebsocketManager
from app.clients.binance_client import get_binance_client
from app.state import monitor_state
from app.config import POLL_INTERVAL

logger = logging.getLogger("monitor")
logger.setLevel(logging.INFO)


def _handle_order_update(msg):
    """
    ENTRY 가격·수량을 WebSocket으로 감지하여 monitor_state에 기록만 합니다.
    TP/SL 주문은 buy.py / sell.py 쪽에서 모두 처리하므로 여기서는 주문을 생성하지 않습니다.
    """
    o = msg.get("o", {})
    if msg.get("e") == "ORDER_TRADE_UPDATE" and \
       o.get("X") == "FILLED" and o.get("S") == "BUY" and o.get("o") == "MARKET":
        price = float(o.get("L", 0))
        qty   = float(o.get("q", 0))
        now   = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
        monitor_state.update({
            "symbol":        msg.get("s"),     # ex. "ETHUSDT"
            "entry_price":   price,
            "position_qty":  qty,
            "entry_time":    now,
            "first_tp_done": False,
            "second_tp_done":False,
            "sl_done":       False,
            "current_price": price,
            "pnl":           0.0
        })
        logger.info(f"[Monitor] Entry detected: {qty}@{price} at {now}")


def _poll_price_loop():
    """
    단순히 현재 가격과 PnL을 모니터링 state에 매 주기 업데이트합니다.
    TP/SL 주문은 이 스레드에서 생성하지 않습니다.
    """
    client = get_binance_client()

    while True:
        symbol = monitor_state.get("symbol")
        entry  = monitor_state.get("entry_price", 0.0)
        qty    = monitor_state.get("position_qty", 0.0)

        if symbol and entry > 0 and qty > 0:
            try:
                current     = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                now         = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
                pnl_percent = (current / entry - 1) * 100

                monitor_state.update({
                    "current_price": current,
                    "pnl":           pnl_percent,
                    "last_update":   now
                })
                logger.info(f"[Monitor] {symbol} price {current}, PnL {pnl_percent:.2f}% at {now}")
            except Exception:
                logger.exception("[Monitor] Error fetching price")

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