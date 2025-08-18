# app/services/switching.py

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, POLL_INTERVAL, MAX_WAIT
from app.services.buy import execute_buy
from app.services.sell import execute_sell
from app.services.simple_buy import execute_simple_buy
from app.services.simple_sell import execute_simple_sell
from app.state import get_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def _wait_for(symbol: str, target_amt: float) -> bool:
    """
    target_amt > 0 : 롱 포지션 대기
    target_amt < 0 : 숏 포지션 대기
    target_amt == 0: 포지션 청산 대기
    """
    client = get_binance_client()
    start = time.time()

    while time.time() - start < MAX_WAIT:
        positions = client.futures_position_information(symbol=symbol)
        current = next(
            (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
            0.0
        )
        if target_amt > 0 and current > 0:
            return True
        if target_amt < 0 and current < 0:
            return True
        if target_amt == 0 and current == 0:
            return True
        time.sleep(POLL_INTERVAL)

    logger.warning(f"Switch timeout: target {target_amt}, current {current}")
    return False


def _cancel_open_reduceonly_orders(symbol: str):
    """⭐ reduceOnly 주문 전부 취소 (TP/SL 잔존 제거용)"""
    client = get_binance_client()
    open_orders = client.futures_get_open_orders(symbol=symbol)
    for order in open_orders:
        if order.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
            logger.info(f"[Cleanup] Canceled reduceOnly order {order['orderId']}")


def switch_position(symbol: str, action: str) -> dict:
    """
    symbol 예: "ETHUSDT"
    action: "BUY" 또는 "SELL"
    - 같은 방향이면 스킵 (TP/SL 유지)
    - 반대 포지션 있으면 청산 ➔ TP/SL 주문 클린업 ➔ 새 진입
    - 포지션 없으면 TP/SL 주문 클린업 ➔ 새 진입
    """
    client = get_binance_client()
    state = get_state(symbol)

    # Dry-run 스킵
    if DRY_RUN:
        logger.info(f"[DRY_RUN] switch_position {action} {symbol}")
        return {"skipped": "dry_run"}

    # 현재 포지션 조회
    positions = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    # BUY 신호 처리
    if action.upper() == "BUY":
        # 이미 롱 포지션이면 스킵 (TP/SL 유지)
        if current_amt > 0:
            return {"skipped": "already_long"}

        # 트레이드 카운트 증가 (실제로 진입/전환이 일어나는 경우)
        state["trade_count"] += 1

        # 신규 진입 전, 기존 TP/SL 오더 삭제
        _cancel_open_reduceonly_orders(symbol)

        # 숏 포지션이 있으면 청산
        if current_amt < 0:
            qty = abs(current_amt)
            logger.info(f"Closing SHORT {qty} @ market for {symbol}")
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            if not _wait_for(symbol, 0.0):
                return {"skipped": "close_failed"}
            _cancel_open_reduceonly_orders(symbol)

            try:
                entry_price = state.get("entry_price", 0.0)
                current_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                pnl = (current_price / entry_price - 1)
                # TP1/TP2 이미 체결된 상태면 손절로 처리하지 않음
                if not state.get("first_tp_done", False) and not state.get("second_tp_done", False):
                    state["capital"] *= (1 + pnl)
                    state["sl_count"] += 1
                    state["daily_pnl"] += pnl * 100
                    state["sl_done"] = True
                    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"[{symbol}] SL PnL {pnl*100:.2f}% applied. New capital: ${state['capital']:.2f} at {now}")
                else:
                    logger.info(f"[{symbol}] Skipped SL count because TP already done before switch.")
            except Exception:
                logger.exception("Failed to update capital on SL close")

        return execute_buy(symbol)

    # SELL 신호 처리
    if action.upper() == "SELL":
        # 이미 숏 포지션이면 스킵
        if current_amt < 0:
            return {"skipped": "already_short"}

        # 트레이드 카운트 증가
        state["trade_count"] += 1

        _cancel_open_reduceonly_orders(symbol)

        # 롱 포지션이 있으면 청산
        if current_amt > 0:
            qty = current_amt
            logger.info(f"Closing LONG {qty} @ market for {symbol}")
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            if not _wait_for(symbol, 0.0):
                return {"skipped": "close_failed"}
            _cancel_open_reduceonly_orders(symbol)

            try:
                entry_price = state.get("entry_price", 0.0)
                current_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                pnl = (entry_price / current_price - 1)
                # TP1/TP2 이미 체결된 상태면 손절로 처리하지 않음
                if not state.get("first_tp_done", False) and not state.get("second_tp_done", False):
                    state["capital"] *= (1 + pnl)
                    state["sl_count"] += 1
                    state["daily_pnl"] += pnl * 100
                    state["sl_done"] = True
                    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"[{symbol}] SL PnL {pnl*100:.2f}% applied. New capital: ${state['capital']:.2f} at {now}")
                else:
                    logger.info(f"[{symbol}] Skipped SL count because TP already done before switch.")
            except Exception:
                logger.exception("Failed to update capital on SL close")

        return execute_sell(symbol)

    logger.error(f"Unknown action for switch: {action}")
    return {"skipped": "unknown_action"}


def switch_position2(symbol: str, action: str) -> dict:
    """
    symbol 예: "ETHUSDT"
    action: "BUY" 또는 "SELL"
    - 같은 방향이면 스킵 (TP/SL 유지)
    - 반대 포지션 있으면 청산 ➔ TP/SL 주문 클린업 ➔ 새 진입
    - 포지션 없으면 TP/SL 주문 클린업 ➔ 새 진입
    """
    client = get_binance_client()
    state = get_state(symbol)

    # Dry-run 스킵
    if DRY_RUN:
        logger.info(f"[DRY_RUN] switch_position {action} {symbol}")
        return {"skipped": "dry_run"}

    # 현재 포지션 조회
    positions = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    # BUY 신호 처리
    if action.upper() == "BUY":
        # 이미 롱 포지션이면 스킵 (TP/SL 유지)
        if current_amt > 0:
            return {"skipped": "already_long"}

        # 트레이드 카운트 증가 (실제로 진입/전환이 일어나는 경우)
        state["trade_count"] += 1

        # 신규 진입 전, 기존 TP/SL 오더 삭제
        _cancel_open_reduceonly_orders(symbol)

        # 숏 포지션이 있으면 청산
        if current_amt < 0:
            qty = abs(current_amt)
            logger.info(f"Closing SHORT {qty} @ market for {symbol}")
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            if not _wait_for(symbol, 0.0):
                return {"skipped": "close_failed"}
            _cancel_open_reduceonly_orders(symbol)

            try:
                entry_price = state.get("entry_price", 0.0)
                current_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                pnl = (current_price / entry_price - 1)
                # TP1/TP2 이미 체결된 상태면 손절로 처리하지 않음
                if not state.get("first_tp_done", False) and not state.get("second_tp_done", False):
                    state["capital"] *= (1 + pnl)
                    state["sl_count"] += 1
                    state["daily_pnl"] += pnl * 100
                    state["sl_done"] = True
                    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"[{symbol}] SL PnL {pnl*100:.2f}% applied. New capital: ${state['capital']:.2f} at {now}")
                else:
                    logger.info(f"[{symbol}] Skipped SL count because TP already done before switch.")
            except Exception:
                logger.exception("Failed to update capital on SL close")

        return execute_simple_buy(symbol)

    # SELL 신호 처리
    if action.upper() == "SELL":
        # 이미 숏 포지션이면 스킵
        if current_amt < 0:
            return {"skipped": "already_short"}

        # 트레이드 카운트 증가
        state["trade_count"] += 1

        _cancel_open_reduceonly_orders(symbol)

        # 롱 포지션이 있으면 청산
        if current_amt > 0:
            qty = current_amt
            logger.info(f"Closing LONG {qty} @ market for {symbol}")
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            if not _wait_for(symbol, 0.0):
                return {"skipped": "close_failed"}
            _cancel_open_reduceonly_orders(symbol)

            try:
                entry_price = state.get("entry_price", 0.0)
                current_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                pnl = (entry_price / current_price - 1)
                # TP1/TP2 이미 체결된 상태면 손절로 처리하지 않음
                if not state.get("first_tp_done", False) and not state.get("second_tp_done", False):
                    state["capital"] *= (1 + pnl)
                    state["sl_count"] += 1
                    state["daily_pnl"] += pnl * 100
                    state["sl_done"] = True
                    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"[{symbol}] SL PnL {pnl*100:.2f}% applied. New capital: ${state['capital']:.2f} at {now}")
                else:
                    logger.info(f"[{symbol}] Skipped SL count because TP already done before switch.")
            except Exception:
                logger.exception("Failed to update capital on SL close")

        return execute_simple_sell(symbol)

    logger.error(f"Unknown action for switch: {action}")
    return {"skipped": "unknown_action"}