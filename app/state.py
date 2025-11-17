# app/state.py
from datetime import datetime
from zoneinfo import ZoneInfo

# 다중 심볼 지원을 위한 상태 저장소
# 심볼별로 모니터링 state를 분리하여 관리합니다.
monitor_states: dict[str, dict] = {}

def _make_key(symbol: str, profile: str) -> str:
    return f"{profile}:{symbol}"


def _default_state(symbol: str, profile: str) -> dict:
    now_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "profile": profile,
        "symbol":         symbol,
        "capital":        100.0,       # 초기 자본 $100
        "initial_capital": 100.0,  # 처음 기준 자본 (수익률 계산용)
        

        # 진입 정보
        "entry_price":    0.0,
        "position_qty":   0.0,
        "position_side": None,
        "entry_time":     "",

        # 현재가 & PnL
        "current_price":  0.0,
        "pnl":            0.0,
        "daily_pnl": 0.0,

        # 카운터
        "trade_count":     0,
        "long_count":      0,
        "short_count":     0,
        
        "leverage": 1,
        "last_reset":      now_str,
    }


def get_state(symbol: str, profile: str = "default") -> dict:
    """
    심볼 + 프로필 단위로 상태 관리.
    profile 예: "webhook1", "webhook2", "webhook3"
    """
    key = _make_key(symbol, profile)
    if key not in monitor_states:
        monitor_states[key] = _default_state(symbol, profile)
    return monitor_states[key]

def list_symbols(profile: str) -> list[str]:
    """
    특정 profile(webhook1/2/3)에 대해 등록된 심볼 목록 반환
    """
    prefix = f"{profile}:"
    return [k.split(":", 1)[1] for k in monitor_states.keys() if k.startswith(prefix)]