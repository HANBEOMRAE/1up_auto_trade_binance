# app/services/sell.py

import logging
import math
import threading
import time

from fastapi import HTTPException
from binance.exceptions import BinanceAPIException
from binance.enums import SIDE_SELL, SIDE_BUY, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, TRADE_LEVERAGE, POLL_INTERVAL
from app.state import get_state

TP_MARKET = "TAKE_PROFIT_MARKET"
SL_MARKET = "STOP_MARKET"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def execute_sell(symbol: str) -> dict:
    client = get_binance_client()
    state = get_state(symbol)

    if DRY_RUN:
        logger.info(f"[DRY_RUN] SELL {symbol}")
        return {"skipped": "dry_run"}

    # 1) 시장가 진입용 재시도 로직 (최대 5회 백오프)
    def create_order_with_retry(**kwargs):
        for attempt in range(5):
            try:
                return client.futures_create_order(**kwargs)
            except BinanceAPIException as e:
                if getattr(e, "code", None) == -1008:
                    wait = 0.5 * (2 ** attempt)
                    logger.warning(f"Overloaded; retrying entry in {wait:.1f}s (attempt {attempt+1}/5)")
                    time.sleep(wait)
                    continue
                raise
        raise BinanceAPIException("Max retries exceeded for entry order")

    # 2) TP/SL 전용 무한 백오프
    def ensure_order(description, **kwargs):
        while True:
            try:
                return client.futures_create_order(**kwargs)
            except BinanceAPIException as e:
                if getattr(e, "code", None) == -1008:
                    logger.warning(f"{description} overloaded; retrying in 1s")
                    time.sleep(1)
                    continue
                logger.error(f"{description} failed: {e}")
                break
        return None

    # 3) 레버리지 설정 + 기존 reduceOnly 주문 삭제
    client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
    for o in client.futures_get_open_orders(symbol=symbol):
        if o.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=o["orderId"])

    # 4) precision 계산 및 자본 기반 allocation
    state_capital = state.get("capital", 0.0)
    mark_price    = float(client.futures_mark_price(symbol=symbol)["markPrice"])
    allocation    = state_capital * 0.98 * TRADE_LEVERAGE
    raw_qty       = allocation / mark_price

    info     = client.futures_exchange_info()
    sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
    lot_f    = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
    pr_f     = next(f for f in sym_info["filters"] if f["filterType"] == "PRICE_FILTER")

    step_size  = float(lot_f["stepSize"])
    min_qty    = float(lot_f["minQty"])
    tick_size  = float(pr_f["tickSize"])
    qty_prec   = int(round(-math.log10(step_size), 0))
    price_prec = int(round(-math.log10(tick_size), 0))

    # 5) 시장가 숏 진입 수량 계산
    qty = math.floor(raw_qty / step_size) * step_size

    # ─── 디버그 로그 ────────────────────────────
    logger.info(
        f"[DEBUG_ORDER] symbol={symbol}, capital={state_capital}, mark_price={mark_price}, "
        f"leverage={TRADE_LEVERAGE}, raw_qty={raw_qty}, step_size={step_size}, "
        f"min_qty={min_qty}, qty={qty}"
    )
    # ─────────────────────────────────────────────

    if qty < min_qty:
        msg = f"Qty {qty} < minQty {min_qty} for {symbol}"
        logger.error(msg)
        raise HTTPException(status_code=400, detail=msg)

    qty_str = f"{qty:.{qty_prec}f}"
    order = create_order_with_retry(
        symbol=symbol, side=SIDE_SELL,
        type=ORDER_TYPE_MARKET, quantity=qty_str
    )

    # 6) MARKET 주문 결과에서 직접 체결 정보 파싱
    fills = order.get("fills", [])
    if fills:
        executed_qty = sum(float(f["qty"]) for f in fills)
        entry_price  = sum(float(f["price"]) * float(f["qty"]) for f in fills) / executed_qty
    else:
        entry_price  = float(order.get("avgPrice", 0)) or mark_price
        executed_qty = float(order.get("executedQty", 0)) or qty

    logger.info(f"Entry SHORT: {executed_qty}@{entry_price}")

    # 상태에도 반영
    state.update({
        "entry_price":  entry_price,
        "position_qty": executed_qty
    })

    # 7) TP1/TP2/SL 주문
    def ceil_p(p):
        mul = 10 ** price_prec
        return math.ceil(p * mul) / mul

    # TP1 (-0.5%, 20%)
    tp1_p   = ceil_p(entry_price * 0.995)
    tp1_q   = math.floor(executed_qty * 0.20 / step_size) * step_size
    tp1_res = ensure_order(
        "TP1",
        symbol=symbol, side=SIDE_BUY, type=TP_MARKET,
        stopPrice=f"{tp1_p:.{price_prec}f}", reduceOnly=True,
        quantity=f"{tp1_q:.{qty_prec}f}"
    )
    tp1_id  = tp1_res["orderId"] if tp1_res else None

    # TP2 (-1.2%, 40% of remainder)
    rem     = executed_qty - tp1_q
    tp2_p   = ceil_p(entry_price * 0.988)
    tp2_q   = math.floor(rem * 0.40 / step_size) * step_size
    tp2_res = ensure_order(
        "TP2",
        symbol=symbol, side=SIDE_BUY, type=TP_MARKET,
        stopPrice=f"{tp2_p:.{price_prec}f}", reduceOnly=True,
        quantity=f"{tp2_q:.{qty_prec}f}"
    )
    tp2_id  = tp2_res["orderId"] if tp2_res else None

    # SL (+0.5%, full)
    sl_p    = ceil_p(entry_price * 1.005)
    sl_res  = ensure_order(
        "SL",
        symbol=symbol, side=SIDE_BUY, type=SL_MARKET,
        stopPrice=f"{sl_p:.{price_prec}f}", reduceOnly=True,
        quantity=f"{executed_qty:.{qty_prec}f}"
    )
    sl_id   = sl_res["orderId"] if sl_res else None

    logger.info(f"TP1@{tp1_p}×{tp1_q}, TP2@{tp2_p}×{tp2_q}, SL@{sl_p}×{executed_qty}")

    # 8) 모니터링 스레드
    def _monitor():
        tp1_active, tp2_active = True, True
        current_sl_id = sl_id

        while True:
            time.sleep(POLL_INTERVAL)
            open_ids = {o["orderId"] for o in client.futures_get_open_orders(symbol=symbol)}

            # TP1 체결
            if tp1_id and tp1_active and tp1_id not in open_ids:
                delta     = (entry_price - tp1_p) / entry_price
                delta_lev = delta * TRADE_LEVERAGE
                frac      = tp1_q / executed_qty
                realized  = delta_lev * frac

                # 자본에 곱셈 업데이트 (복리)
                state["capital"] *= (1 + realized)
                state["first_tp_count"] += 1
                state["daily_pnl"]       += realized * 100
                state["first_tp_done"]    = True

                client.futures_cancel_order(symbol=symbol,
                                           orderId=current_sl_id)
                new_sl_p = ceil_p(entry_price * 0.999)
                new_qty  = executed_qty - tp1_q
                new_sl   = ensure_order(
                    "SL_after_TP1",
                    symbol=symbol, side=SIDE_BUY, type=SL_MARKET,
                    stopPrice=f"{new_sl_p:.{price_prec}f}",
                    reduceOnly=True, quantity=f"{new_qty:.{qty_prec}f}"
                )
                current_sl_id = new_sl["orderId"] if new_sl else current_sl_id
                tp1_active = False

            # TP2 체결
            if tp2_id and tp2_active and tp2_id not in open_ids:
                delta     = (entry_price - tp2_p) / entry_price
                delta_lev = delta * TRADE_LEVERAGE
                frac      = tp2_q / executed_qty
                realized  = delta_lev * frac

                state["capital"] *= (1 + realized)
                state["second_tp_count"] += 1
                state["daily_pnl"]        += realized * 100
                state["second_tp_done"]    = True

                client.futures_cancel_order(symbol=symbol,
                                           orderId=current_sl_id)
                new_sl_p = ceil_p(entry_price * 0.995)
                new_qty  = executed_qty - tp1_q - tp2_q
                new_sl   = ensure_order(
                    "SL_after_TP2",
                    symbol=symbol, side=SIDE_BUY, type=SL_MARKET,
                    stopPrice=f"{new_sl_p:.{price_prec}f}",
                    reduceOnly=True, quantity=f"{new_qty:.{qty_prec}f}"
                )
                current_sl_id = new_sl["orderId"] if new_sl else current_sl_id
                tp2_active = False

            # SL 체결 감지
            pos = client.futures_position_information(symbol=symbol)
            amt = next((float(p["positionAmt"]) for p in pos if p["symbol"] == symbol), 0.0)
            if amt == 0:
                if not state.get("first_tp_done", False) and not state.get("second_tp_done", False):
                    current_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                    delta     = (entry_price - current_price) / entry_price
                    delta_lev = delta * TRADE_LEVERAGE
                    frac      = 1.0
                    realized  = delta_lev * frac

                    state["capital"] *= (1 + realized)
                    state["sl_count"]  += 1
                    state["daily_pnl"] += realized * 100
                    state["sl_done"]    = True

                break

    threading.Thread(target=_monitor, daemon=True).start()

    return {
        "sell":   {"filled": executed_qty, "entry": entry_price},
        "orders": {"tp1": tp1_id, "tp2": tp2_id, "sl": sl_id}
    }