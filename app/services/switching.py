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
from app.state import monitor_state

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
    current = None

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


def switch_position(symbol: str, action: str) -> dict:
    """
    symbol 예: "ETHUSDT"
    action: "BUY" 또는 "SELL"
    - 현재 포지션이 action 과 같으면 건너뜀
    - 반대 포지션이 남아있으면 시장가로 청산 → 새 포지션 진입
    - 손절 카운트: 반대 청산 시점에 PnL < 0 일 때
    """
    client = get_binance_client()

    # Dry-run 스킵
    if DRY_RUN:
        logger.info(f"[DRY_RUN] switch_position {action} {symbol}")
        return {"skipped": "dry_run"}

    # 신호 받을 때마다 전체 거래 횟수 카운터 증가
    monitor_state["trade_count"] += 1

    # 1) 현재 포지션 조회
    positions = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    # BUY 신호 처리
    if action.upper() == "BUY":
        if current_amt > 0:
            return {"skipped": "already_long"}

        # 숏 포지션 청산
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

            # 청산 시 PnL 계산 → 손절 카운트
            try:
                entry_price   = monitor_state.get("entry_price", 0.0)
                current_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                pnl = (current_price / entry_price - 1) * 100
                if pnl < 0:
                    monitor_state["sl_count"]  += 1
                    monitor_state["daily_pnl"] += pnl
                    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"Stop-loss on switch SHORT→LONG: {pnl:.2f}% at {now}")
            except Exception:
                logger.exception("Failed to calc SL PnL on short close")

        # 롱 진입
        return execute_buy(symbol)

    # SELL 신호 처리
    if action.upper() == "SELL":
        if current_amt < 0:
            return {"skipped": "already_short"}

        # 롱 포지션 청산
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

            # 청산 시 PnL 계산 → 손절 카운트
            try:
                entry_price   = monitor_state.get("entry_price", 0.0)
                current_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                pnl = (entry_price / current_price - 1) * 100
                if pnl < 0:
                    monitor_state["sl_count"]  += 1
                    monitor_state["daily_pnl"] += pnl
                    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"Stop-loss on switch LONG→SHORT: {pnl:.2f}% at {now}")
            except Exception:
                logger.exception("Failed to calc SL PnL on long close")

        # 숏 진입
        return execute_sell(symbol)

    # 알 수 없는 action
    logger.error(f"Unknown action for switch: {action}")
    return {"skipped": "unknown_action"}