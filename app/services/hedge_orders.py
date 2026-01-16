# app/services/hedge_orders.py

import logging
import math
from fastapi import HTTPException
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import BUY_PCT
from app.state import get_state
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def execute_hedge_entry(
    symbol: str,
    position_side: str,       # "LONG" | "SHORT"
    leverage: int,
    profile: str,
    use_initial_capital: bool,
) -> dict:
    """
    Hedge Mode 진입 주문(추가매수/추가진입 포함)
    - LONG: side=BUY, positionSide=LONG
    - SHORT: side=SELL, positionSide=SHORT

    사이징:
    - use_initial_capital=True  -> state['initial_capital'] 기준
    - use_initial_capital=False -> state['capital'] 기준(복리)
    """
    client = get_binance_client()
    state = get_state(symbol, profile)

    if position_side not in ("LONG", "SHORT"):
        raise HTTPException(status_code=400, detail="position_side must be LONG or SHORT")

    base_capital = (
        float(state.get("initial_capital", 0.0))
        if use_initial_capital
        else float(state.get("capital", 0.0))
    )
    if base_capital <= 0:
        raise HTTPException(status_code=400, detail="base_capital must be > 0")

    mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])

    # ✅ 기존 buy/sell.py 스타일: allocation 기반 사이징
    allocation = base_capital * BUY_PCT * leverage
    raw_qty = allocation / mark_price

    # LOT_SIZE 규칙에 맞춰 수량 보정
    info = client.futures_exchange_info()
    sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
    lot_f = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
    step = float(lot_f["stepSize"])
    min_qty = float(lot_f["minQty"])
    qty_prec = int(round(-math.log10(step), 0)) if step > 0 else 0

    qty = math.floor(raw_qty / step) * step
    if qty < min_qty:
        raise HTTPException(status_code=400, detail=f"Qty {qty} < minQty {min_qty}")

    qty_str = f"{qty:.{qty_prec}f}"

    # Hedge 진입 side 결정
    side = SIDE_BUY if position_side == "LONG" else SIDE_SELL

    order = client.futures_create_order(
        symbol=symbol,
        side=side,
        type=ORDER_TYPE_MARKET,
        quantity=qty_str,
        positionSide=position_side,  # ⭐ 핵심
    )

    logger.info(
        f"[HEDGE_ENTRY] {profile}:{symbol} {position_side} "
        f"lev={leverage} qty={qty_str} mark={mark_price} "
        f"(base={'initial_capital' if use_initial_capital else 'capital'}={base_capital})"
    )

    # (선택) webhook5/6 상태 기록: 마지막 진입 주문 정보
    if position_side == "LONG":
        state["hedge_long_add_count"] = state.get("hedge_long_add_count", 0) + 1
        state["hedge"]["long"]["last_order_qty"] = float(qty_str)
        state["hedge"]["long"]["last_order_time"] = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    else:
        state["hedge_short_add_count"] = state.get("hedge_short_add_count", 0) + 1
        state["hedge"]["short"]["last_order_qty"] = float(qty_str)
        state["hedge"]["short"]["last_order_time"] = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")

    state["trade_count"] = state.get("trade_count", 0) + 1

    return {"entry": {"positionSide": position_side, "qty": float(qty_str), "mark": mark_price}, "order": order}