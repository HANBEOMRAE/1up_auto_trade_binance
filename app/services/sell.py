import logging
import math
import threading
import time

from binance.exceptions import BinanceAPIException
from binance.enums import SIDE_SELL, SIDE_BUY, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, TRADE_LEVERAGE, POLL_INTERVAL

TP_MARKET = "TAKE_PROFIT_MARKET"
SL_MARKET = "STOP_MARKET"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def execute_sell(symbol: str) -> dict:
    client = get_binance_client()
    if DRY_RUN:
        logger.info(f"[DRY_RUN] SELL {symbol}")
        return {"skipped": "dry_run"}

    try:
        # 1) 레버리지 설정 + 기존 reduceOnly 주문 삭제
        client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
        for o in client.futures_get_open_orders(symbol=symbol):
            if o.get("reduceOnly"):
                client.futures_cancel_order(symbol=symbol, orderId=o["orderId"])

        # 2) precision 계산
        balances     = client.futures_account_balance()
        usdt_balance = float(next(b["balance"] for b in balances if b["asset"] == "USDT"))
        mark_price   = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        allocation   = usdt_balance * 0.98 * TRADE_LEVERAGE
        raw_qty      = allocation / mark_price

        info     = client.futures_exchange_info()
        sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
        lot_f    = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
        pr_f     = next(f for f in sym_info["filters"] if f["filterType"] == "PRICE_FILTER")

        step_size  = float(lot_f["stepSize"])
        min_qty    = float(lot_f["minQty"])
        tick_size  = float(pr_f["tickSize"])
        qty_prec   = int(round(-math.log10(step_size),0))
        price_prec = int(round(-math.log10(tick_size),0))

        # 3) 시장가 숏 진입
        qty = math.floor(raw_qty / step_size) * step_size
        if qty < min_qty:
            logger.warning(f"Qty {qty} < minQty {min_qty}. Skip SELL.")
            return {"skipped": "quantity_too_low"}
        qty_str = f"{qty:.{qty_prec}f}"

        order = client.futures_create_order(
            symbol=symbol, side=SIDE_SELL,
            type=ORDER_TYPE_MARKET, quantity=qty_str
        )
        details      = client.futures_get_order(symbol=symbol, orderId=order["orderId"])
        entry_price  = float(details["avgPrice"])
        executed_qty = float(details["executedQty"])
        logger.info(f"Entry SHORT: {executed_qty}@{entry_price}")

        # 4) TP1/TP2/SL 주문
        def ceil_p(p):
            mul = 10 ** price_prec
            return math.ceil(p * mul) / mul

        # TP1 (-0.3%, 20%)
        tp1_p  = ceil_p(entry_price * 0.997)
        tp1_q  = math.floor(executed_qty * 0.20 / step_size) * step_size
        tp1_id = client.futures_create_order(
            symbol=symbol, side=SIDE_BUY, type=TP_MARKET,
            stopPrice=f"{tp1_p:.{price_prec}f}",
            reduceOnly=True, quantity=f"{tp1_q:.{qty_prec}f}"
        )["orderId"]

        # TP2 (-1.1%, 50% of remainder)
        rem     = executed_qty - tp1_q
        tp2_p   = ceil_p(entry_price * 0.989)
        tp2_q   = math.floor(rem * 0.50 / step_size) * step_size
        tp2_id  = client.futures_create_order(
            symbol=symbol, side=SIDE_BUY, type=TP_MARKET,
            stopPrice=f"{tp2_p:.{price_prec}f}",
            reduceOnly=True, quantity=f"{tp2_q:.{qty_prec}f}"
        )["orderId"]

        # SL (+0.3%, full)
        sl_p    = ceil_p(entry_price * 1.003)
        sl_id   = client.futures_create_order(
            symbol=symbol, side=SIDE_BUY, type=SL_MARKET,
            stopPrice=f"{sl_p:.{price_prec}f}",
            reduceOnly=True, quantity=f"{executed_qty:.{qty_prec}f}"
        )["orderId"]

        logger.info(f"TP1@{tp1_p}×{tp1_q}, TP2@{tp2_p}×{tp2_q}, SL@{sl_p}×{executed_qty}")

        # 5) 모니터링 스레드: open_orders 기반으로 체결 감지 및 SL 재설정
        def _monitor():
            tp1_active, tp2_active = True, True
            current_sl_id = sl_id

            while True:
                time.sleep(POLL_INTERVAL)
                open_ids = {o["orderId"] for o in client.futures_get_open_orders(symbol=symbol)}

                # TP1 체결 → SL 재배치 (+0.1%) with remaining qty
                if tp1_active and tp1_id not in open_ids:
                    try:
                        client.futures_cancel_order(symbol=symbol, orderId=current_sl_id)
                        new_sl_p = ceil_p(entry_price * 0.999)
                        new_qty = executed_qty - tp1_q
                        new_sl = client.futures_create_order(
                            symbol=symbol, side=SIDE_BUY, type=SL_MARKET,
                            stopPrice=f"{new_sl_p:.{price_prec}f}",
                            reduceOnly=True,
                            quantity=f"{new_qty:.{qty_prec}f}"
                        )
                        current_sl_id = new_sl["orderId"]
                        logger.info(f"Moved SL to +0.1% @ {new_sl_p} for qty {new_qty}")
                    except Exception as e:
                        logger.exception(f"Error relocating SL after TP1: {e}")
                    tp1_active = False

                # TP2 체결 → SL 재배치 (+0.1%) with further reduced qty
                if tp2_active and tp2_id not in open_ids:
                    try:
                        client.futures_cancel_order(symbol=symbol, orderId=current_sl_id)
                        new_sl_p = ceil_p(entry_price * 0.999)
                        new_qty = executed_qty - tp1_q - tp2_q
                        new_sl = client.futures_create_order(
                            symbol=symbol, side=SIDE_BUY, type=SL_MARKET,
                            stopPrice=f"{new_sl_p:.{price_prec}f}",
                            reduceOnly=True,
                            quantity=f"{new_qty:.{qty_prec}f}"
                        )
                        current_sl_id = new_sl["orderId"]
                        logger.info(f"Moved SL to +0.1% @ {new_sl_p} for qty {new_qty}")
                    except Exception as e:
                        logger.exception(f"Error relocating SL after TP2: {e}")
                    tp2_active = False

                # SL 체결 감지: 포지션 전량 청산 시 모니터 종료
                pos = client.futures_position_information(symbol=symbol)
                amt = float(next(p["positionAmt"] for p in pos if p["symbol"]==symbol))
                if amt == 0:
                    logger.info("SL hit → position closed")
                    break

        threading.Thread(target=_monitor, daemon=True).start()

        return {
            "sell":   {"filled": executed_qty, "entry": entry_price},
            "orders": {"tp1": tp1_id, "tp2": tp2_id, "sl": sl_id}
        }

    except BinanceAPIException as e:
        logger.error(f"Sell order failed: {e}")
        return {"skipped": "api_error", "error": str(e)}
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return {"skipped": "unexpected_error", "error": str(e)}