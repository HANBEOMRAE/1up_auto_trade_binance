import logging
import time
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

def switch_position(symbol: str, action: str, leverage: int = None) -> dict:
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
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=abs(current_amt),
            reduceOnly=True
        )
        _wait_for(symbol, 0.0)
        _cancel_open_reduceonly_orders(symbol)

        # ✅ 체결가 조회
        exit_price = _get_exit_price(client, symbol, order)
        _update_capital_after_exit(symbol, long_exit=True, exit_price=exit_price)
        return {"done": "buy_stop"}

    # === SELL_STOP ===
    if action.upper() == "SELL_STOP" and current_amt < 0:
        _cancel_open_reduceonly_orders(symbol)
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=abs(current_amt),
            reduceOnly=True
        )
        _wait_for(symbol, 0.0)
        _cancel_open_reduceonly_orders(symbol)

        exit_price = _get_exit_price(client, symbol, order)
        _update_capital_after_exit(symbol, long_exit=False, exit_price=exit_price)
        return {"done": "sell_stop"}

    # === BUY ===
    if action.upper() == "BUY":
        if current_amt > 0:
            return {"skipped": "already_long"}
        _cancel_open_reduceonly_orders(symbol)
        if current_amt < 0:
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=abs(current_amt),
                reduceOnly=True
            )
            _wait_for(symbol, 0.0)
            _cancel_open_reduceonly_orders(symbol)
            exit_price = _get_exit_price(client, symbol, order)
            _update_capital_after_exit(symbol, long_exit=False, exit_price=exit_price)
        return execute_buy(symbol, leverage=leverage)

    # === SELL ===
    if action.upper() == "SELL":
        if current_amt < 0:
            return {"skipped": "already_short"}
        _cancel_open_reduceonly_orders(symbol)
        if current_amt > 0:
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=current_amt,
                reduceOnly=True
            )
            _wait_for(symbol, 0.0)
            _cancel_open_reduceonly_orders(symbol)
            exit_price = _get_exit_price(client, symbol, order)
            _update_capital_after_exit(symbol, long_exit=True, exit_price=exit_price)
        return execute_sell(symbol, leverage=leverage)

    logger.error(f"Unknown action for switch: {action}")
    return {"skipped": "unknown_action"}

def _get_exit_price(client, symbol: str, order: dict) -> float:
    """주문 ID 기반으로 청산 평균 체결가(avgPrice) 조회"""
    order_id = order.get("orderId")
    try:
        filled_order = client.futures_get_order(symbol=symbol, orderId=order_id)
        return float(filled_order.get("avgPrice") or client.futures_mark_price(symbol=symbol)["markPrice"])
    except Exception as e:
        logger.warning(f"[Exit] Failed to fetch avgPrice: {e}")
        return float(client.futures_mark_price(symbol=symbol)["markPrice"])

def _update_capital_after_exit(symbol: str, long_exit: bool, exit_price: float):
    state = get_state(symbol)
    try:
        entry_price = state.get("entry_price", 0.0)
        position_qty = abs(state.get("position_qty", 0.0))
        leverage = state.get("leverage", 1)

        if entry_price == 0 or position_qty == 0:
            logger.warning(f"[{symbol}] No entry_price or qty found. Skipping capital update.")
            return

        pnl = (exit_price / entry_price - 1) if long_exit else (entry_price / exit_price - 1)
        pnl *= leverage   # ✅ 레버리지 반영

        capital_before = state["capital"]
        state["capital"] *= (1 + pnl)
        state["daily_pnl"] += pnl * 100
        state["entry_price"] = 0.0
        state["position_qty"] = 0.0

        logger.info(f"[{symbol}] Exit @ {exit_price:.4f}, Entry @ {entry_price:.4f}, PnL {pnl*100:.2f}%")
        logger.info(f"[{symbol}] Capital ${capital_before:.2f} → ${state['capital']:.2f}")

    except Exception:
        logger.exception(f"[{symbol}] Failed to update capital after exit")