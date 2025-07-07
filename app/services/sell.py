import logging
import math
import threading
import time
from binance.exceptions import BinanceAPIException
from binance.enums import SIDE_SELL, SIDE_BUY, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, TRADE_LEVERAGE, POLL_INTERVAL

# 문자열 상수로 TP/SL 마켓 주문 타입 지정
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
        # 1) 레버리지 설정
        client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
        logger.info(f"Leverage set to {TRADE_LEVERAGE}x for {symbol}")

        # 2) 기존 reduceOnly 주문 삭제
        for order in client.futures_get_open_orders(symbol=symbol):
            if order.get("reduceOnly"):
                client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
                logger.info(f"Canceled reduceOnly order {order['orderId']}")

        # 3) 진입량 계산
        balances     = client.futures_account_balance()
        usdt_balance = float(next(b["balance"] for b in balances if b["asset"] == "USDT"))
        mark_price   = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        allocation   = usdt_balance * 0.98 * TRADE_LEVERAGE
        raw_qty      = allocation / mark_price

        # LOT_SIZE & PRICE_FILTER 정보 조회
        info            = client.futures_exchange_info()
        sym_info        = next(s for s in info["symbols"] if s["symbol"] == symbol)
        lot_filter      = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
        price_filter    = next(f for f in sym_info["filters"] if f["filterType"] == "PRICE_FILTER")
        step_size       = float(lot_filter["stepSize"])
        min_qty         = float(lot_filter["minQty"])
        tick_size       = float(price_filter["tickSize"])
        price_precision = int(round(-math.log10(tick_size), 0))

        # 주문 가능한 수량으로 내림
        qty = math.floor(raw_qty / step_size) * step_size
        if qty < min_qty:
            logger.warning(f"Qty {qty} < minQty {min_qty}. Skipping SELL.")
            return {"skipped": "quantity_too_low"}

        # 4) 시장가 진입 (숏)
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=str(qty)
        )
        logger.info(f"Market SELL submitted: {order}")

        # 체결 정보 조회
        details      = client.futures_get_order(symbol=symbol, orderId=order["orderId"])
        entry_price  = float(details["avgPrice"])
        executed_qty = float(details["executedQty"])
        logger.info(f"Entry SHORT: {executed_qty}@{entry_price}")

        # 5) TP/SL 주문 걸기
        # 1차 TP: -0.5% → 30%
        tp1_price        = round(entry_price * 0.995, price_precision)
        tp1_qty          = math.floor(executed_qty * 0.30 / step_size) * step_size
        order_tp1        = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=TP_MARKET,
            stopPrice=str(tp1_price),
            reduceOnly=True,
            quantity=str(tp1_qty)
        )

        # 2차 TP: -1.1% → 남은 물량의 50%
        remain_after_tp1 = executed_qty - tp1_qty
        tp2_qty          = math.floor(remain_after_tp1 * 0.50 / step_size) * step_size
        tp2_price        = round(entry_price * 0.989, price_precision)
        order_tp2        = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=TP_MARKET,
            stopPrice=str(tp2_price),
            reduceOnly=True,
            quantity=str(tp2_qty)
        )

        # 기본 SL: +0.5% → 전체 수량
        sl_price = round(entry_price * 1.005, price_precision)
        order_sl = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=SL_MARKET,
            stopPrice=str(sl_price),
            reduceOnly=True,
            quantity=str(executed_qty)
        )

        logger.info(
            f"Placed TP1 @ {tp1_price} x{tp1_qty}, "
            f"TP2 @ {tp2_price} x{tp2_qty}, "
            f"SL @ {sl_price} x{executed_qty}"
        )

        # 6) TP1 체결 모니터링 및 SL 이동
        def _monitor_tp1():
            try:
                while True:
                    time.sleep(POLL_INTERVAL)
                    tp1_info = client.futures_get_order(symbol=symbol, orderId=order_tp1["orderId"])
                    if tp1_info.get("status") == "FILLED":
                        # 기존 SL 취소
                        client.futures_cancel_order(symbol=symbol, orderId=order_sl["orderId"])
                        logger.info(f"Canceled original SL order {order_sl['orderId']} after TP1 fill")

                        # 남은 물량에 대해 SL 재설정 (+0.1%)
                        new_sl_price = round(entry_price * 1.001, price_precision)
                        new_sl_order = client.futures_create_order(
                            symbol=symbol,
                            side=SIDE_BUY,
                            type=SL_MARKET,
                            stopPrice=str(new_sl_price),
                            reduceOnly=True,
                            quantity=str(remain_after_tp1)
                        )
                        logger.info(
                            f"Moved SL to +0.1% @ {new_sl_price} x{remain_after_tp1}, "
                            f"new SL orderId {new_sl_order['orderId']}"
                        )
                        break
            except Exception as e:
                logger.exception(f"Error monitoring TP1: {e}")

        threading.Thread(target=_monitor_tp1, daemon=True).start()

        return {
            "sell": {"filled": executed_qty, "entry": entry_price},
            "orders": {
                "tp1_orderId": order_tp1["orderId"],
                "tp2_orderId": order_tp2["orderId"],
                "sl_orderId":  order_sl["orderId"],
            }
        }

    except BinanceAPIException as e:
        logger.error(f"Sell order failed: {e}")
        return {"skipped": "api_error", "error": str(e)}
    except Exception as e:
        logger.exception(f"Unexpected error in execute_sell: {e}")
        return {"skipped": "unexpected_error", "error": str(e)}