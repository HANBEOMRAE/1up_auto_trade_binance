import logging
import math
from fastapi import HTTPException
from binance.enums import SIDE_SELL, ORDER_TYPE_MARKET
from binance.exceptions import BinanceAPIException
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, TRADE_LEVERAGE, BUY_PCT
from app.state import get_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def execute_sell(
    symbol: str,
    leverage: int | None = None,
    use_initial_capital: bool = False,
    profile : str = "webhook1"
) -> dict:
    """
    - use_initial_capital=True: state['initial_capital'] 기준 사이징 (/webhook2,3)
    - use_initial_capital=False: state['capital'] 기준 사이징(복리, /webhook)
    - profile: "webhook1" | "webhook2" | "webhook3"
    """
    client = get_binance_client()
    state = get_state(symbol, profile)

    if DRY_RUN:
        logger.info(f"[DRY_RUN] SELL {symbol}")
        return {"skipped": "dry_run"}

    # 레버리지 설정 (우선순위: 인자 > 글로벌 설정)
    leverage_to_use = leverage or TRADE_LEVERAGE
    client.futures_change_leverage(symbol=symbol, leverage=leverage_to_use)

    # ⬇️ 핵심: 사이징 기준 자본 선택
    base_capital = (
        state.get("initial_capital", 0.0)
        if use_initial_capital
        else state.get("capital", 0.0)
    )
    
    # 수량 계산
    mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
    allocation = base_capital * BUY_PCT * leverage_to_use
    raw_qty = allocation / mark_price

    # 거래소 LOT_SIZE 규칙에 맞춰 수량 보정
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

    # 시장가 숏 진입
    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_SELL,
        type=ORDER_TYPE_MARKET,
        quantity=qty_str
    )

    # 주문 상세 재조회 → avgPrice 보정
    order_id = order.get("orderId")
    try:
        filled_order = client.futures_get_order(symbol=symbol, orderId=order_id)
        entry = float(filled_order.get("avgPrice") or mark_price)
    except Exception as e:
        logger.warning(f"[SELL] Failed to fetch avgPrice via orderId {order_id}: {e}")
        entry = mark_price

    logger.info(
        f"[SELL] {profile}:{symbol} {qty}@{entry} "
        f"(base={'initial_capital' if use_initial_capital else 'capital'})"
    )

    # 상태 저장 (진입 정보 및 카운트)
    state.update({
        "entry_price":   entry,
        "position_qty":  -qty,
        "current_price": entry,
        "position_side": "short",
        "leverage":      leverage_to_use,
        "short_count":   state.get("short_count", 0) + 1,
        "trade_count":   state.get("trade_count", 0) + 1
    })

    return {"sell": {"filled": qty, "entry": entry}}