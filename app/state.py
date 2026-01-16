# app/state.py
from datetime import datetime
from zoneinfo import ZoneInfo

monitor_states: dict[str, dict] = {}

def _make_key(symbol: str, profile: str) -> str:
    return f"{profile}:{symbol}"


def _default_state(symbol: str, profile: str) -> dict:
    now_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "profile": profile,
        "symbol": symbol,

        # 공통 자본
        "capital": 50.0,          # 복리용(웹훅5: compounding)
        "initial_capital": 50.0,  # 고정자본용(웹훅6: no compounding)

        # ===== 기존 webhook1~4 호환 필드(유지) =====
        "entry_price": 0.0,
        "position_qty": 0.0,
        "position_side": None,
        "entry_time": "",

        "current_price": 0.0,
        "pnl": 0.0,
        "daily_pnl": 0.0,

        "trade_count": 0,
        "long_count": 0,
        "short_count": 0,

        "leverage": 1,
        "last_reset": now_str,

        # ===== webhook5/6 (Hedge) 전용 필드 =====
        # 거래소 동기화용(진짜 포지션 상태)
        "hedge": {
            "long": {
                "qty": 0.0,         # Binance positionAmt (LONG는 +)
                "entry_price": 0.0, # Binance entryPrice
                "unrealized_pnl": 0.0,
                "update_time": "",  # 마지막 동기화 시각(Asia/Seoul 문자열)
            },
            "short": {
                "qty": 0.0,         # Binance positionAmt (SHORT는 -로 내려오는 경우 많음)
                "entry_price": 0.0,
                "unrealized_pnl": 0.0,
                "update_time": "",
            },
        },

        # 요청 레버리지 정책 확인용(“열려있으면 일치 강제”)
        "hedge_symbol_leverage": 1,

        # 추가진입 카운터(가드 넣을 때 유용)
        "hedge_long_add_count": 0,
        "hedge_short_add_count": 0,
    }


def get_state(symbol: str, profile: str = "default") -> dict:
    key = _make_key(symbol, profile)
    if key not in monitor_states:
        monitor_states[key] = _default_state(symbol, profile)
    return monitor_states[key]


def list_symbols(profile: str) -> list[str]:
    prefix = f"{profile}:"
    return [k.split(":", 1)[1] for k in monitor_states.keys() if k.startswith(prefix)]