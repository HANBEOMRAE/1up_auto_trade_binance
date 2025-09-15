import logging
import math
from fastapi import HTTPException
from binance.enums import SIDE_BUY, ORDER_TYPE_MARKET
from binance.exceptions import BinanceAPIException
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, TRADE_LEVERAGE
from app.state import get_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def execute_buy(symbol: str, leverage: int = None) -> dict:
    client = get_binance_client()
    state = get_state(symbol)

    if DRY_RUN:
        logger.info(f"[DRY_RUN] BUY {symbol}")
        return {"skipped": "dry_run"}

    # ✅ 레버리지 설정 (우선순위: 인자 > 설정값)
    leverage_to_use = leverage or TRADE_LEVERAGE
    client.futures_change_leverage(symbol=symbol, leverage=leverage_to_use)

    capital = state.get("capital", 0.0)
    mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
    allocation = capital * 0.98 * leverage_to_use
    raw_qty = allocation / mark_price

    # 거래 수량 계산
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

    # ✅ 주문 생성
    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY,
        type=ORDER_TYPE_MARKET,
        quantity=qty_str
    )

    # ✅ 주문 상세 재조회 → avgPrice 보정
    order_id = order.get("orderId")
    try:
        filled_order = client.futures_get_order(symbol=symbol, orderId=order_id)
        entry = float(filled_order.get("avgPrice") or mark_price)
    except Exception as e:
        logger.warning(f"[BUY] Failed to fetch avgPrice via orderId {order_id}: {e}")
        entry = mark_price

    logger.info(f"[BUY] {symbol} {qty}@{entry}")

    # 상태 저장 (레버리지 포함)
    state.update({
        "entry_price": entry,
        "position_qty": qty,
        "current_price": entry,
        "position_side": "long",
        "leverage": leverage_to_use
    })

    return {"buy": {"filled": qty, "entry": entry}}