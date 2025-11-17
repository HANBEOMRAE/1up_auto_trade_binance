import logging
import time
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, POLL_INTERVAL, MAX_WAIT, FEE_RATE
from app.services.buy import execute_buy
from app.services.sell import execute_sell
from app.state import get_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _wait_for(symbol: str, target_amt: float) -> bool:
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
    client = get_binance_client()
    open_orders = client.futures_get_open_orders(symbol=symbol)
    for order in open_orders:
        if order.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
            logger.info(f"[Cleanup] Canceled reduceOnly order {order['orderId']}")


def switch_position(
    symbol: str,
    action: str,
    profile: str = "webhook1",
    leverage: int | None = None,
    use_initial_capital: bool = False,  # ← /webhook2 전용 플래그
) -> dict:
    """
    profile 단위로 상태 분리:
      - profile="webhook1": /webhook
      - profile="webhook2": /webhook2
      - profile="webhook3": /webhook3

    use_initial_capital=True 이면:
      - 포지션 사이징 시 initial_capital만 사용
      - 청산 후 capital 갱신(복리) 금지
    """
    client = get_binance_client()
    state = get_state(symbol, profile)

    if DRY_RUN:
        logger.info(f"[DRY_RUN] switch_position {action} {symbol}")
        return {"skipped": "dry_run"}

    positions = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    # === BUY_STOP : 롱 청산 ===
    if action.upper() == "BUY_STOP" and current_amt > 0:
        _cancel_open_reduceonly_orders(symbol)
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=abs(current_amt),
            reduceOnly=True
        )
        _wait_for(symbol, 0.0)
        _cancel_open_reduceonly_orders(symbol)

        exit_price = _get_exit_price(client, symbol, order)
        pnl_percent = _update_capital_after_exit(
            symbol, 
            long_exit=True, 
            exit_price=exit_price,
            profile=profile,
            use_initial_capital=use_initial_capital
        )
        return {"done": "buy_stop", "exit_price": exit_price, "pnl": pnl_percent}

    # === SELL_STOP : 숏 청산 ===
    if action.upper() == "SELL_STOP" and current_amt < 0:
        _cancel_open_reduceonly_orders(symbol)
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=abs(current_amt),
            reduceOnly=True
        )
        _wait_for(symbol, 0.0)
        _cancel_open_reduceonly_orders(symbol)

        exit_price = _get_exit_price(client, symbol, order)
        pnl_percent = _update_capital_after_exit(
            symbol,
            long_exit=False,
            exit_price=exit_price,
            profile=profile,
            use_initial_capital=use_initial_capital
        )
        return {"done": "sell_stop", "exit_price": exit_price, "pnl": pnl_percent}

    # === BUY : 롱 진입(필요 시 숏 청산 후 스위치) ===
    if action.upper() == "BUY":
        if current_amt > 0:
            return {"skipped": "already_long"}

        _cancel_open_reduceonly_orders(symbol)

        if current_amt < 0:
            # 먼저 숏 청산
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=abs(current_amt),
                reduceOnly=True
            )
            _wait_for(symbol, 0.0)
            _cancel_open_reduceonly_orders(symbol)

            exit_price = _get_exit_price(client, symbol, order)
            _update_capital_after_exit(
                symbol,
                long_exit=False,
                exit_price=exit_price,
                profile=profile,
                use_initial_capital=use_initial_capital
            )

        # 롱 진입 (플래그 전파)
        return execute_buy(
            symbol,
            leverage=leverage,
            use_initial_capital=use_initial_capital,
            profile=profile
        )

    # === SELL : 숏 진입(필요 시 롱 청산 후 스위치) ===
    if action.upper() == "SELL":
        if current_amt < 0:
            return {"skipped": "already_short"}

        _cancel_open_reduceonly_orders(symbol)

        if current_amt > 0:
            # 먼저 롱 청산
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=current_amt,
                reduceOnly=True
            )
            _wait_for(symbol, 0.0)
            _cancel_open_reduceonly_orders(symbol)

            exit_price = _get_exit_price(client, symbol, order)
            _update_capital_after_exit(
                symbol,
                long_exit=True,
                exit_price=exit_price,
                profile=profile,
                use_initial_capital=use_initial_capital
            )

        # 숏 진입 (플래그 전파)
        return execute_sell(
            symbol,
            leverage=leverage,
            use_initial_capital=use_initial_capital,
            profile=profile
        )

    logger.error(f"Unknown action for switch: {action}")
    return {"skipped": "unknown_action"}


def _get_exit_price(client, symbol: str, order: dict) -> float:
    """주문 ID 기반으로 청산 평균 체결가(avgPrice) 조회"""
    order_id = order.get("orderId")
    try:
        filled_order = client.futures_get_order(symbol=symbol, orderId=order_id)
        return float(
            filled_order.get("avgPrice")
            or client.futures_mark_price(symbol=symbol)["markPrice"]
        )
    except Exception as e:
        logger.warning(f"[Exit] Failed to fetch avgPrice: {e}")
        return float(client.futures_mark_price(symbol=symbol)["markPrice"])


def _update_capital_after_exit(
    symbol: str,
    long_exit: bool,
    exit_price: float,
    profile: str = "webhook1",
    use_initial_capital: bool = False
) -> float:
    """
    포지션 청산 후 PnL 계산 및 상태 업데이트.
    - 수익률 계산 시 거래 수수료 포함:
        raw_pnl = (가격변화 × 레버리지)
        net_pnl = raw_pnl - (FEE_RATE * 레버리지 * 2)   # 왕복 수수료

    - use_initial_capital=True:
        capital 미변경(복리 금지), PnL/로그만 기록
    - use_initial_capital=False:
        capital에 복리 반영
    반환값: pnl 퍼센트(%) float
    """
    state = get_state(symbol, profile)
    try:
        entry_price = state.get("entry_price", 0.0)
        position_qty = abs(state.get("position_qty", 0.0))
        leverage = state.get("leverage", 1)

        if entry_price == 0 or position_qty == 0:
            logger.warning(f"[{symbol}] No entry_price or qty found. Skipping capital update.")
            return 0.0

        # 가격 변화로 인한 PnL (레버리지 반영 전)
        if long_exit:
            price_change = (exit_price / entry_price - 1.0)
        else:
            price_change = (entry_price / exit_price - 1.0)

        # 레버리지 반영
        raw_pnl = price_change * leverage  # 예: +1% * 5배 = +5%
        
        # 거래 수수료(왕복) 반영
        fee_per_side = FEE_RATE * leverage       # 한 쪽 수수료
        total_fee = fee_per_side * 2             # 진입 + 청산
        net_pnl = raw_pnl - total_fee            # 최종 수익률(배수 아님)

        if use_initial_capital:
            # /webhook2, /wehbook3: 복리 금지
            logger.info(
                f"[{profile}:{symbol}] Exit @ {exit_price:.4f}, Entry @ {entry_price:.4f}, "
                f"RawPnL {raw_pnl*100:.2f}% - Fee {total_fee*100:.2f}% = Net {net_pnl*100:.2f}% "
                f"(NO compounding)"
            )
        else:
            # /webhook: 기존 복리
            capital_before = state["capital"]
            state["capital"] = capital_before * (1.0 + net_pnl)
            logger.info(
                f"[{profile}:{symbol}] Exit @ {exit_price:.4f}, Entry @ {entry_price:.4f}, "
                f"RawPnL {raw_pnl*100:.2f}% - Fee {total_fee*100:.2f}% = Net {net_pnl*100:.2f}%"
            )
            logger.info(
                f"[{profile}:{symbol}] Capital ${capital_before:.2f} "
                f"→ ${state['capital']:.2f}"
            )

        # 공통 후처리
        state["daily_pnl"] = state.get("daily_pnl", 0.0) + net_pnl * 100.0
        state["entry_price"] = 0.0
        state["position_qty"] = 0.0
        state["position_side"] = None

        return net_pnl * 100.0

    except Exception:
        logger.exception(f"[{profile}:{symbol}] Failed to update capital after exit")
        return 0.0