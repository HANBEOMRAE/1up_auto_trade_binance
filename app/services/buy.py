import logging
import math
import threading
import time
from binance.exceptions import BinanceAPIException
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, TRADE_LEVERAGE, POLL_INTERVAL
from app.state import monitor_state

TP_MARKET = "TAKE_PROFIT_MARKET"
SL_MARKET = "STOP_MARKET"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def round_step_size(value: float, step_size: float, round_up=False) -> float:
    precision = int(round(-math.log10(step_size), 0))
    factor = 10 ** precision
    if round_up:
        return math.ceil(value * factor) / factor
    else:
        return math.floor(value * factor) / factor

def execute_buy(symbol: str) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] BUY {symbol}")
        return {"skipped": "dry_run"}

    try:
        client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
        logger.info(f"Leverage set to {TRADE_LEVERAGE}x for {symbol}")

        for order in client.futures_get_open_orders(symbol=symbol):
            if order.get("reduceOnly"):
                client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
                logger.info(f"Canceled reduceOnly order {order['orderId']}")

        balances     = client.futures_account_balance()
        usdt_balance = float(next(b["balance"] for b in balances if b["asset"] == "USDT"))
        mark_price   = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        allocation   = usdt_balance * 0.98 * TRADE_LEVERAGE
        raw_qty      = allocation / mark_price

        info            = client.futures_exchange_info()
        sym_info        = next(s for s in info["symbols"] if s["symbol"] == symbol)
        lot_filter      = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
        price_filter    = next(f for f in sym_info["filters"] if f["filterType"] == "PRICE_FILTER")
        step_size       = float(lot_filter["stepSize"])
        min_qty         = float(lot_filter["minQty"])
        tick_size       = float(price_filter["tickSize"])

        qty = round_step_size(raw_qty, step_size, round_up=False)
        if qty < min_qty:
            logger.warning(f"Qty {qty} < minQty {min_qty}. Skipping BUY.")
            return {"skipped": "quantity_too_low"}

        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=str(qty)
        )
        logger.info(f"Market BUY submitted: {order}")

        details      = client.futures_get_order(symbol=symbol, orderId=order["orderId"])
        entry_price  = float(details["avgPrice"])
        executed_qty = float(details["executedQty"])
        monitor_state["entry_price"] = entry_price
        logger.info(f"Entry LONG: {executed_qty}@{entry_price}")

        tp1_price = round_step_size(entry_price * 1.005, tick_size, round_up=True)
        tp1_qty   = round_step_size(executed_qty * 0.30, step_size, round_up=False)
        order_tp1 = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=TP_MARKET,
            stopPrice=str(tp1_price),
            reduceOnly=True,
            quantity=str(tp1_qty)
        )

        remain_after_tp1 = executed_qty - tp1_qty
        tp2_qty   = round_step_size(remain_after_tp1 * 0.50, step_size, round_up=False)
        tp2_price = round_step_size(entry_price * 1.011, tick_size, round_up=True)
        order_tp2 = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=TP_MARKET,
            stopPrice=str(tp2_price),
            reduceOnly=True,
            quantity=str(tp2_qty)
        )

        sl_price = round_step_size(entry_price * 0.995, tick_size, round_up=True)
        order_sl = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
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

        def _monitor_tp1():
            try:
                while True:
                    time.sleep(POLL_INTERVAL)
                    tp1_info = client.futures_get_order(symbol=symbol, orderId=order_tp1["orderId"])
                    if tp1_info.get("status") == "FILLED":
                        client.futures_cancel_order(symbol=symbol, orderId=order_sl["orderId"])
                        logger.info(f"Canceled original SL order {order_sl['orderId']} after TP1 fill")

                        new_sl_price = round_step_size(entry_price * 1.001, tick_size, round_up=True)
                        new_sl_order = client.futures_create_order(
                            symbol=symbol,
                            side=SIDE_SELL,
                            type=SL_MARKET,
                            stopPrice=str(new_sl_price),
                            reduceOnly=True,
                            quantity=str(remain_after_tp1)
                        )
                        logger.info(
                            f"Moved SL to +0.1% @ {new_sl_price} x{remain_after_tp1}, "
                            f"new SL orderId {new_sl_order['orderId']}"
                        )

                        # 🔥 SL 이동 후 감시 → SL 체결되면 trigger 표시
                        while True:
                            time.sleep(POLL_INTERVAL)
                            sl_info = client.futures_get_order(symbol=symbol, orderId=new_sl_order["orderId"])
                            if sl_info.get("status") == "FILLED":
                                logger.info("SL triggered after TP1")
                                monitor_state["sl_triggered"] = True
                                break
                        break
            except Exception as e:
                logger.exception(f"Error monitoring TP1: {e}")

        threading.Thread(target=_monitor_tp1, daemon=True).start()

        return {
            "buy": {"filled": executed_qty, "entry": entry_price},
            "orders": {
                "tp1_orderId": order_tp1["orderId"],
                "tp2_orderId": order_tp2["orderId"],
                "sl_orderId":  order_sl["orderId"],
            }
        }

    except BinanceAPIException as e:
        logger.error(f"Buy order failed: {e}")
        return {"skipped": "api_error", "error": str(e)}
    except Exception as e:
        logger.exception(f"Unexpected error in execute_buy: {e}")
        return {"skipped": "unexpected_error", "error": str(e)}