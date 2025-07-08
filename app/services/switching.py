# app/services/switching.py

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, POLL_INTERVAL, MAX_WAIT
from app.services.buy import execute_buy
from app.services.sell import execute_sell
from app.state import monitor_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def _wait_for(symbol: str, target_amt: float) -> bool:
    client = get_binance_client()
    start = time.time()
    current = None

    while time.time() - start < MAX_WAIT:
        positions = client.futures_position_information(symbol=symbol)
        current = next(
            (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
            0.0
        )
        if target_amt > 0 and current > 0:
            return True
        if target_amt < 0 and current < 0:
            return True
        if target_amt == 0 and current == 0:
            return True
        time.sleep(POLL_INTERVAL)

    logger.warning(f"Switch timeout: target {target_amt}, current {current}")
    return False

def _cancel_open_reduceonly_orders(symbol: str):
    """⭐ reduceOnly 주문 전부 취소 (TP/SL 잔존 제거용)"""
    client = get_binance_client()
    open_orders = client.futures_get_open_orders(symbol=symbol)
    for order in open_orders:
        if order.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
            logger.info(f"[Cleanup] Canceled reduceOnly order {order['orderId']}")

def switch_position(symbol: str, action: str) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] switch_position {action} {symbol}")
        return {"skipped": "dry_run"}

    monitor_state["trade_count"] += 1

    positions = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    if action.upper() == "BUY":
        if current_amt > 0:
            return {"skipped": "already_long"}

        if current_amt < 0:
            qty = abs(current_amt)
            logger.info(f"Closing SHORT {qty} @ market for {symbol}")
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            if not _wait_for(symbol, 0.0):
                return {"skipped": "close_failed"}

            # ⭐ 포지션 닫힌 후 reduceOnly 주문 전부 취소
            _cancel_open_reduceonly_orders(symbol)

            try:
                entry_price   = monitor_state.get("entry_price", 0.0)
                current_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                pnl = (current_price / entry_price - 1) * 100
                if pnl < 0:
                    monitor_state["sl_count"]  += 1
                    monitor_state["daily_pnl"] += pnl
                    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"Stop-loss on switch SHORT→LONG: {pnl:.2f}% at {now}")
            except Exception:
                logger.exception("Failed to calc SL PnL on short close")

        return execute_buy(symbol)

    if action.upper() == "SELL":
        if current_amt < 0:
            return {"skipped": "already_short"}

        if current_amt > 0:
            qty = current_amt
            logger.info(f"Closing LONG {qty} @ market for {symbol}")
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            if not _wait_for(symbol, 0.0):
                return {"skipped": "close_failed"}

            # ⭐ 포지션 닫힌 후 reduceOnly 주문 전부 취소
            _cancel_open_reduceonly_orders(symbol)

            try:
                entry_price   = monitor_state.get("entry_price", 0.0)
                current_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                pnl = (entry_price / current_price - 1) * 100
                if pnl < 0:
                    monitor_state["sl_count"]  += 1
                    monitor_state["daily_pnl"] += pnl
                    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"Stop-loss on switch LONG→SHORT: {pnl:.2f}% at {now}")
            except Exception:
                logger.exception("Failed to calc SL PnL on long close")

        return execute_sell(symbol)

    logger.error(f"Unknown action for switch: {action}")
    return {"skipped": "unknown_action"}