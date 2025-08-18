# app/services/simple_buy.py

import logging
import math
from binance.enums import SIDE_BUY, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import TRADE_LEVERAGE, DRY_RUN
from app.state import get_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def execute_simple_buy(symbol: str):
    client = get_binance_client()
    state = get_state(symbol)

    if DRY_RUN:
        logger.info(f"[DRY_RUN] BUY {symbol}")
        return {"skipped": "dry_run"}

    try:
        # 1) 레버리지 설정
        client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)

        # 2) 자본 및 수량 계산
        capital    = state.get("capital", 0.0)
        mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        quantity   = (capital * 0.98 * TRADE_LEVERAGE) / mark_price

        # 3) precision 계산
        info     = client.futures_exchange_info()
        sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
        lot_f    = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
        step     = float(lot_f["stepSize"])
        min_qty  = float(lot_f["minQty"])
        qty_prec = int(round(-math.log10(step), 0))

        qty = math.floor(quantity / step) * step
        if qty < min_qty:
            logger.warning(f"Qty {qty} < minQty {min_qty}. Skip BUY.")
            return {"skipped": "quantity_too_low"}

        # 4) 시장가 매수 주문
        qty_str = f"{qty:.{qty_prec}f}"
        order = client.futures_create_order(
            symbol=symbol, side=SIDE_BUY,
            type=ORDER_TYPE_MARKET, quantity=qty_str
        )
        details = client.futures_get_order(symbol=symbol, orderId=order["orderId"])
        entry   = float(details["avgPrice"])

        logger.info(f"[BUY] {symbol} {qty}@{entry}")
        return {"buy": {"filled": qty, "entry": entry}}

    except Exception as e:
        logger.exception(f"[BUY FAIL] {symbol}: {e}")
        return {"error": str(e)}