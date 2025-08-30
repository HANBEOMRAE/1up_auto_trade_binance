import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, POLL_INTERVAL, MAX_WAIT
from app.services.buy import execute_buy
from app.services.sell import execute_sell
from app.state import get_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def _wait_for(symbol: str, target_amt: float) -> bool:
    client = get_binance_client()
    start = time.time()
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
    client = get_binance_client()
    open_orders = client.futures_get_open_orders(symbol=symbol)
    for order in open_orders:
        if order.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
            logger.info(f"[Cleanup] Canceled reduceOnly order {order['orderId']}")

def switch_position(symbol: str, action: str) -> dict:
    client = get_binance_client()
    state = get_state(symbol)

    if DRY_RUN:
        logger.info(f"[DRY_RUN] switch_position {action} {symbol}")
        return {"skipped": "dry_run"}

    positions = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    # === BUY_STOP ===
    if action.upper() == "BUY_STOP" and current_amt > 0:
        _cancel_open_reduceonly_orders(symbol)
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=current_amt,
            reduceOnly=True
        )
        _wait_for(symbol, 0.0)
        _cancel_open_reduceonly_orders(symbol)
        _update_capital_after_exit(symbol, long_exit=True)
        return {"done": "buy_stop"}

    # === SELL_STOP ===
    if action.upper() == "SELL_STOP" and current_amt < 0:
        _cancel_open_reduceonly_orders(symbol)
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=abs(current_amt),
            reduceOnly=True
        )
        _wait_for(symbol, 0.0)
        _cancel_open_reduceonly_orders(symbol)
        _update_capital_after_exit(symbol, long_exit=False)
        return {"done": "sell_stop"}

    # === BUY ===
    if action.upper() == "BUY":
        if current_amt > 0:
            return {"skipped": "already_long"}
        _cancel_open_reduceonly_orders(symbol)
        if current_amt < 0:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=abs(current_amt),
                reduceOnly=True
            )
            _wait_for(symbol, 0.0)
            _cancel_open_reduceonly_orders(symbol)
            _update_capital_after_exit(symbol, long_exit=False)
        return execute_buy(symbol)

    # === SELL ===
    if action.upper() == "SELL":
        if current_amt < 0:
            return {"skipped": "already_short"}
        _cancel_open_reduceonly_orders(symbol)
        if current_amt > 0:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=current_amt,
                reduceOnly=True
            )
            _wait_for(symbol, 0.0)
            _cancel_open_reduceonly_orders(symbol)
            _update_capital_after_exit(symbol, long_exit=True)
        return execute_sell(symbol)

    logger.error(f"Unknown action for switch: {action}")
    return {"skipped": "unknown_action"}

def _update_capital_after_exit(symbol: str, long_exit: bool):
    client = get_binance_client()
    state = get_state(symbol)
    try:
        entry_price = state.get("entry_price", 0.0)
        current_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
        if entry_price == 0:
            logger.warning(f"[{symbol}] No entry_price found. Skipping capital update.")
            return

        pnl = (current_price / entry_price - 1) if long_exit else (entry_price / current_price - 1)
        state["capital"] *= (1 + pnl)
        state["daily_pnl"] += pnl * 100
        logger.info(f"[{symbol}] PnL {pnl*100:.2f}% applied. New capital: ${state['capital']:.2f}")
    except Exception:
        logger.exception(f"[{symbol}] Failed to update capital after exit")