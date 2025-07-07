# app/services/monitor.py

import threading
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from binance import ThreadedWebsocketManager
from app.clients.binance_client import get_binance_client
from app.state import monitor_state
from app.config import POLL_INTERVAL, TP_RATIO, SL_RATIO

logger = logging.getLogger("monitor")
logger.setLevel(logging.INFO)


def _handle_order_update(msg):
    # ENTRY 가격·수량을 WebSocket으로 잡아두는 부분은 그대로 유지
    o = msg.get("o", {})
    if msg.get("e") == "ORDER_TRADE_UPDATE" and \
       o.get("X") == "FILLED" and o.get("S") == "BUY" and o.get("o") == "MARKET":
        price = float(o.get("L", 0))
        qty   = float(o.get("q", 0))
        now   = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
        monitor_state.update({
            "entry_price":    price,
            "position_qty":   qty,
            "entry_time":     now,
            "first_tp_done":  False,
            "second_tp_done": False,
            "sl_done":        False
        })
        logger.info(f"Entry detected: {qty}@{price} at {now}")


def _poll_price_loop():
    client = get_binance_client()
    symbol = monitor_state["symbol"]

    while True:
        qty   = monitor_state["position_qty"]
        entry = monitor_state["entry_price"]

        if qty > 0 and entry > 0:
            current     = float(client.futures_symbol_ticker(symbol=symbol)["price"])
            now         = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
            pnl_percent = (current / entry - 1) * 100

            monitor_state["current_price"] = current
            monitor_state["pnl"]           = pnl_percent

            # 1차 TP: PnL ≥ (TP_RATIO − 1)*100
            if not monitor_state["first_tp_done"] and pnl_percent >= (TP_RATIO - 1) * 100:
                tp_qty = qty * 0.3
                client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=tp_qty,
                    reduceOnly=True
                )
                monitor_state.update({
                    "first_tp_done":  True,
                    "first_tp_price": current,
                    "first_tp_qty":   tp_qty,
                    "first_tp_time":  now,
                    "first_tp_pnl":   pnl_percent,
                    "position_qty":   qty - tp_qty
                })
                monitor_state["first_tp_count"] += 1
                monitor_state["daily_pnl"]     += pnl_percent
                logger.info(f"1차 익절: {tp_qty}@{current} ({pnl_percent:.2f}% at {now})")

            # 2차 TP: PnL ≥ 1.1% (TP_RATIO_SECOND = 1.011)
            elif monitor_state["first_tp_done"] \
                 and not monitor_state["second_tp_done"] \
                 and pnl_percent >= (1.011 - 1) * 100:
                tp2_qty = monitor_state["position_qty"] * 0.5
                client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=tp2_qty,
                    reduceOnly=True
                )
                monitor_state.update({
                    "second_tp_done":  True,
                    "second_tp_price": current,
                    "second_tp_qty":   tp2_qty,
                    "second_tp_time":  now,
                    "second_tp_pnl":   pnl_percent,
                    "position_qty":    monitor_state["position_qty"] - tp2_qty
                })
                monitor_state["second_tp_count"] += 1
                monitor_state["daily_pnl"]       += pnl_percent
                logger.info(f"2차 익절: {tp2_qty}@{current} ({pnl_percent:.2f}% at {now})")

            # SL: PnL ≤ -0.5% (or +0.1% after 1차)
            sl_threshold = - (1 - SL_RATIO) * 100  # SL_RATIO=0.995 → −0.5%
            if monitor_state["first_tp_done"]:
                sl_threshold = (1.001 - 1) * 100     # +0.1% 손절 트레일링

            if not monitor_state["sl_done"] and pnl_percent <= sl_threshold:
                sl_qty = monitor_state["position_qty"]
                client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=sl_qty,
                    reduceOnly=True
                )
                monitor_state.update({
                    "sl_done":      True,
                    "sl_price":     current,
                    "sl_qty":       sl_qty,
                    "sl_time":      now,
                    "sl_pnl":       pnl_percent,
                    "position_qty": 0
                })
                monitor_state["sl_count"]  += 1
                monitor_state["daily_pnl"] += pnl_percent
                logger.info(f"손절 실행: {sl_qty}@{current} ({pnl_percent:.2f}% at {now})")

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
        logger.info("WebsocketManager initialized")
    except Exception:
        logger.exception("WebsocketManager 초기화 실패")
        return

    thread = threading.Thread(target=_poll_price_loop, daemon=True)
    thread.start()
    logger.info("Price polling thread started")