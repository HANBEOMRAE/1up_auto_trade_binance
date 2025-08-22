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

        # 2) 자본 및 가격 정보 가져오기
        capital    = state.get("capital", 0.0)
        mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])

        # 3) 거래 심볼 정보 가져오기 (precision, minQty 등)
        info     = client.futures_exchange_info()
        sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
        lot_f    = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
        step     = float(lot_f["stepSize"])
        min_qty  = float(lot_f["minQty"])
        qty_prec = int(round(-math.log10(step), 0))

        # 4) 진입 가능한 최소 자본 확인
        min_required_capital = (min_qty * mark_price) / TRADE_LEVERAGE
        if capital < min_required_capital:
            logger.warning(
                f"[{symbol}] Capital ${capital:.2f} too low for minQty {min_qty}. "
                f"Required: ${min_required_capital:.2f} (mark: {mark_price})"
            )
            return {
                "skipped": "capital_too_low",
                "required_capital": round(min_required_capital, 4),
                "current_capital": capital,
                "mark_price": mark_price,
                "min_qty": min_qty,
                "leverage": TRADE_LEVERAGE
            }

        # 5) 수량 계산
        quantity = (capital * 0.98 * TRADE_LEVERAGE) / mark_price
        qty = math.floor(quantity / step) * step

        # 6) 최소 수량 체크
        if qty < min_qty:
            logger.warning(f"[{symbol}] Qty {qty} < minQty {min_qty}. Skip BUY.")
            return {
                "skipped": "quantity_too_low",
                "qty": qty,
                "min_qty": min_qty,
                "mark_price": mark_price,
                "capital": capital,
                "suggestion": "Increase capital or leverage"
            }

        # 7) 시장가 매수
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