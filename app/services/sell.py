# ✅ 수정된 sell.py

import logging
import math
from fastapi import HTTPException
from binance.enums import SIDE_SELL, ORDER_TYPE_MARKET
from binance.exceptions import BinanceAPIException
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, TRADE_LEVERAGE
from app.state import get_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def execute_sell(symbol: str) -> dict:
    client = get_binance_client()
    state = get_state(symbol)

    if DRY_RUN:
        logger.info(f"[DRY_RUN] SELL {symbol}")
        return {"skipped": "dry_run"}

    client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)

    capital = state.get("capital", 0.0)
    mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
    allocation = capital * 0.98 * TRADE_LEVERAGE
    raw_qty = allocation / mark_price

    info = client.futures_exchange_info()
    sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
    lot_f = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
    step = float(lot_f["stepSize"])
    min_qty = float(lot_f["minQty"])
    qty_prec = int(round(-math.log10(step), 0))

    qty = math.floor(raw_qty / step) * step
    if qty < min_qty:
        raise HTTPException(status_code=400, detail=f"Qty {qty} < minQty {min_qty}")

    qty_str = f"{qty:.{qty_prec}f}"
    order = client.futures_create_order(
        symbol=symbol, side=SIDE_SELL,
        type=ORDER_TYPE_MARKET, quantity=qty_str
    )

    entry = float(order.get("avgPrice") or mark_price)
    logger.info(f"[SELL] {symbol} {qty}@{entry}")
    state.update({
        "entry_price": entry,
        "position_qty": -qty
    })
    return {"sell": {"filled": qty, "entry": entry}}