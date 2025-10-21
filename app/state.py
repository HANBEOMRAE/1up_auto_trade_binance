# app/state.py
from datetime import datetime
from zoneinfo import ZoneInfo

# 다중 심볼 지원을 위한 상태 저장소
# 심볼별로 모니터링 state를 분리하여 관리합니다.
monitor_states: dict[str, dict] = {}


def _default_state(symbol: str) -> dict:
    now_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "symbol":         symbol,
        "capital":        100.0,       # 초기 자본 $100

        # 진입 정보
        "entry_price":    0.0,
        "position_qty":   0.0,
        "entry_time":     "",

        # 현재가 & PnL
        "current_price":  0.0,
        "pnl":            0.0,

        # 일일 정산용
        "trade_count":     0,
        "long_count":      0,
        "short_count":     0,
        "initial_capital": 100.0,  # 처음 기준 자본 (수익률 계산용)
        "last_reset":      now_str,
    }


def get_state(symbol: str) -> dict:
    """
    심볼별 모니터 상태를 반환합니다. 없으면 기본 템플릿으로 생성합니다.
    """
    if symbol not in monitor_states:
        monitor_states[symbol] = _default_state(symbol)
    return monitor_states[symbol]
