# app/state.py
from datetime import datetime
from zoneinfo import ZoneInfo

# 다중 심볼 지원을 위한 상태 저장소
# 심볼별로 모니터링 state를 분리하여 관리합니다.
monitor_states: dict[str, dict] = {}


def _default_state(symbol: str) -> dict:
    """
    각 심볼별 기본 상태 템플릿을 반환합니다.
    """
    now_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "symbol":         symbol,
        "capital":        50.0,       # 초기 자본 $50

        # 진입 정보
        "entry_price":    0.0,
        "position_qty":   0.0,
        "entry_time":     "",

        # 1차 익절 정보
        "first_tp_done":  False,
        "first_tp_price": 0.0,
        "first_tp_qty":   0.0,
        "first_tp_time":  "",
        "first_tp_pnl":   0.0,

        # 2차 익절 정보
        "second_tp_done":  False,
        "second_tp_price": 0.0,
        "second_tp_qty":   0.0,
        "second_tp_time":  "",
        "second_tp_pnl":   0.0,

        # 손절 정보
        "sl_done":        False,
        "sl_price":       0.0,
        "sl_qty":         0.0,
        "sl_time":        "",
        "sl_pnl":         0.0,

        # 현재가 & PnL
        "current_price":  0.0,
        "pnl":            0.0,

        # 일일 정산용 카운터
        "trade_count":     0,
        "first_tp_count":  0,
        "second_tp_count": 0,
        "sl_count":        0,
        "daily_pnl":       0.0,
        "last_reset":      now_str,
    }


def get_state(symbol: str) -> dict:
    """
    심볼별 모니터 상태를 반환합니다. 없으면 기본 템플릿으로 생성합니다.
    """
    if symbol not in monitor_states:
        monitor_states[symbol] = _default_state(symbol)
    return monitor_states[symbol]