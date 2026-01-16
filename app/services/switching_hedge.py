# app/services/switching_hedge.py

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET

from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, POLL_INTERVAL, MAX_WAIT, FEE_RATE
from app.state import get_state
from app.services.hedge_orders import execute_hedge_entry

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

VALID_ACTIONS = {"BUY", "SELL", "BUY_STOP", "SELL_STOP"}


def _ensure_hedge_mode(client) -> None:
    try:
        mode = client.futures_get_position_mode()
        if not mode.get("dualSidePosition"):
            client.futures_change_position_mode(dualSidePosition=True)
    except Exception as e:
        logger.warning("ensure_hedge_mode failed: %s", e)


def _get_positions(client, symbol: str) -> list[dict]:
    return client.futures_position_information(symbol=symbol)


def _side_amt(positions: list[dict], symbol: str, side: str) -> float:
    # side: "LONG" or "SHORT"
    for p in positions:
        if p.get("symbol") == symbol and p.get("positionSide") == side:
            return float(p.get("positionAmt", 0.0))
    return 0.0


def _any_open(positions: list[dict], symbol: str) -> bool:
    return _side_amt(positions, symbol, "LONG") != 0.0 or _side_amt(positions, symbol, "SHORT") != 0.0


def _enforce_leverage_policy_state_based(client, symbol: str, requested_leverage: int, profile: str) -> dict | None:
    """
    ✅ state 기반 레버리지 정책 (네가 원한 방식)
    - 포지션이 없으면: requested_leverage를 state에 저장하고 거래소에 set 시도
    - 포지션이 있으면: state에 저장된 leverage를 "고정값"으로 사용하고 요청값은 무시
      (읽기 기반 정책 제거: positions에서 leverage가 안 내려오는 환경 대응)
    """
    state = get_state(symbol, profile)
    positions = _get_positions(client, symbol)
    has_open = _any_open(positions, symbol)

    saved = int(state.get("hedge_symbol_leverage", 0) or 0)

    if not has_open:
        # ✅ 신규 진입 구간: 요청 leverage로 고정
        state["hedge_symbol_leverage"] = requested_leverage
        state["leverage"] = requested_leverage  # (호환/로그용)

        # 거래소 세팅 시도 (실패하면 거래 자체를 막는 게 안전)
        try:
            client.futures_change_leverage(symbol=symbol, leverage=requested_leverage)
        except Exception as e:
            return {"skipped": f"failed_to_set_leverage:{e}"}

        return None

    # ✅ 포지션이 열려있으면: saved leverage가 기준
    # saved가 비어있으면(서버 재시작 등) 요청 leverage로 복구
    if saved <= 0:
        state["hedge_symbol_leverage"] = requested_leverage
        state["leverage"] = requested_leverage
        saved = requested_leverage

    # 요청 leverage가 다르게 와도, 여기서 스킵하지 않고 "saved로 강제"하는 방식
    # (원하면 mismatch일 때 스킵하도록 바꿀 수도 있음)
    state["leverage"] = saved

    # (선택) 거래소에도 saved로 보정 세팅 시도 — 실패해도 주문은 진행 가능하니 warning만
    try:
        client.futures_change_leverage(symbol=symbol, leverage=saved)
    except Exception as e:
        logger.warning(f"[{profile}:{symbol}] futures_change_leverage failed while open (continue): {e}")

    return None


def _wait_for_side_close(symbol: str, position_side: str) -> bool:
    client = get_binance_client()
    start = time.time()
    while time.time() - start < MAX_WAIT:
        positions = _get_positions(client, symbol)
        amt = _side_amt(positions, symbol, position_side)
        if amt == 0.0:
            return True
        time.sleep(POLL_INTERVAL)
    logger.warning("Close timeout: %s %s", symbol, position_side)
    return False


def _get_exit_price(client, symbol: str, order: dict) -> float:
    order_id = order.get("orderId")
    try:
        filled = client.futures_get_order(symbol=symbol, orderId=order_id)
        avg = filled.get("avgPrice")
        if avg:
            return float(avg)
    except Exception as e:
        logger.warning("[Exit] Failed to fetch avgPrice: %s", e)

    return float(client.futures_mark_price(symbol=symbol)["markPrice"])


def _sync_state_from_exchange(symbol: str, profile: str) -> None:
    client = get_binance_client()
    state = get_state(symbol, profile)

    positions = client.futures_position_information(symbol=symbol)

    long_qty = 0.0
    long_entry = 0.0
    long_u = 0.0

    short_qty = 0.0
    short_entry = 0.0
    short_u = 0.0

    for p in positions:
        if p.get("symbol") != symbol:
            continue

        ps = p.get("positionSide")
        amt = float(p.get("positionAmt", 0.0))
        entry = float(p.get("entryPrice", 0.0))
        upnl = float(p.get("unRealizedProfit", 0.0))

        if ps == "LONG":
            long_qty = amt
            long_entry = entry
            long_u = upnl
        elif ps == "SHORT":
            short_qty = amt  # 보통 음수
            short_entry = entry
            short_u = upnl

    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")

    state["hedge"]["long"]["qty"] = long_qty
    state["hedge"]["long"]["entry_price"] = long_entry
    state["hedge"]["long"]["unrealized_pnl"] = long_u
    state["hedge"]["long"]["update_time"] = now

    state["hedge"]["short"]["qty"] = short_qty
    state["hedge"]["short"]["entry_price"] = short_entry
    state["hedge"]["short"]["unrealized_pnl"] = short_u
    state["hedge"]["short"]["update_time"] = now


def _apply_compounding_after_exit(
    symbol: str,
    profile: str,
    exit_side: str,  # "LONG" or "SHORT"
    exit_price: float,
    use_initial_capital: bool,
    leverage: int,
) -> float:
    """
    exit_side 별 수익률 계산 + (복리모드면) capital 갱신.
    net_pnl = raw_pnl - 왕복수수료(FEE_RATE * leverage * 2)
    반환: pnl_percent(%)
    """
    state = get_state(symbol, profile)

    if exit_side == "LONG":
        entry = float(state["hedge"]["long"]["entry_price"])
        if entry <= 0:
            return 0.0
        price_change = (exit_price / entry - 1.0)
    else:
        entry = float(state["hedge"]["short"]["entry_price"])
        if entry <= 0:
            return 0.0
        price_change = (entry / exit_price - 1.0)

    raw_pnl = price_change * leverage
    total_fee = (FEE_RATE * leverage) * 2
    net_pnl = raw_pnl - total_fee

    if not use_initial_capital:
        before = float(state.get("capital", 0.0))
        state["capital"] = before * (1.0 + net_pnl)

    state["daily_pnl"] = state.get("daily_pnl", 0.0) + net_pnl * 100.0
    return net_pnl * 100.0


def switch_position_hedge(
    symbol: str,
    action: str,
    leverage: int,
    profile: str,
    use_initial_capital: bool,
) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        return {"skipped": "dry_run"}

    action = action.upper()
    if action not in VALID_ACTIONS:
        return {"skipped": "unknown_action"}

    _ensure_hedge_mode(client)

    # ✅ state 기반 레버리지 정책 적용
    # - 포지션 없으면: 요청 leverage 고정 + 거래소 set
    # - 포지션 있으면: state leverage로 강제(요청 leverage 무시)
    if action in ("BUY", "SELL"):
        policy = _enforce_leverage_policy_state_based(client, symbol, leverage, profile)
        if policy is not None:
            return policy

        # enforce에서 state["leverage"]를 saved로 맞춰놨으니 여기서 최종 leverage를 다시 가져옴
        state = get_state(symbol, profile)
        leverage = int(state.get("hedge_symbol_leverage", leverage))
    else:
        # STOP은 레버리지 정책과 무관하게 청산 진행
        state = get_state(symbol, profile)
        # PnL 계산용으로는 state leverage를 쓰는 게 더 일관적
        leverage = int(state.get("hedge_symbol_leverage", leverage))
        state["leverage"] = leverage

    # ✅ BUY: LONG 추가진입 (스킵 없음)
    if action == "BUY":
        res = execute_hedge_entry(
            symbol=symbol,
            position_side="LONG",
            leverage=leverage,
            profile=profile,
            use_initial_capital=use_initial_capital,
        )
        _sync_state_from_exchange(symbol, profile)
        return res

    # ✅ SELL: SHORT 추가진입 (스킵 없음)
    if action == "SELL":
        res = execute_hedge_entry(
            symbol=symbol,
            position_side="SHORT",
            leverage=leverage,
            profile=profile,
            use_initial_capital=use_initial_capital,
        )
        _sync_state_from_exchange(symbol, profile)
        return res

    # STOP 처리 전에 최신 포지션 동기화
    _sync_state_from_exchange(symbol, profile)
    positions = _get_positions(client, symbol)

    # ✅ BUY_STOP: LONG만 청산
    if action == "BUY_STOP":
        long_amt = _side_amt(positions, symbol, "LONG")
        if long_amt <= 0:
            return {"skipped": "no_long_position"}

        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=str(abs(long_amt)),
            positionSide="LONG",
        )
        _wait_for_side_close(symbol, "LONG")
        exit_price = _get_exit_price(client, symbol, order)

        pnl = _apply_compounding_after_exit(
            symbol=symbol,
            profile=profile,
            exit_side="LONG",
            exit_price=exit_price,
            use_initial_capital=use_initial_capital,
            leverage=leverage,
        )

        _sync_state_from_exchange(symbol, profile)
        return {"done": "buy_stop", "exit_price": exit_price, "pnl": pnl}

    # ✅ SELL_STOP: SHORT만 청산
    if action == "SELL_STOP":
        short_amt = _side_amt(positions, symbol, "SHORT")
        if short_amt >= 0:
            return {"skipped": "no_short_position"}

        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=str(abs(short_amt)),
            positionSide="SHORT",
        )
        _wait_for_side_close(symbol, "SHORT")
        exit_price = _get_exit_price(client, symbol, order)

        pnl = _apply_compounding_after_exit(
            symbol=symbol,
            profile=profile,
            exit_side="SHORT",
            exit_price=exit_price,
            use_initial_capital=use_initial_capital,
            leverage=leverage,
        )

        _sync_state_from_exchange(symbol, profile)
        return {"done": "sell_stop", "exit_price": exit_price, "pnl": pnl}

    return {"skipped": "unknown_action"}