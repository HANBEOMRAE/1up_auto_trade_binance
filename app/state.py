# app/state.py

from datetime import datetime
from zoneinfo import ZoneInfo

monitor_state = {
    "symbol": "ETHUSDT",

    # 진입 정보
    "entry_price": 0.0,
    "position_qty": 0.0,
    "entry_time": "",

    # 1차 익절 정보
    "first_tp_done": False,
    "first_tp_price": 0.0,
    "first_tp_qty": 0.0,
    "first_tp_time": "",
    "first_tp_pnl": 0.0,

    # 2차 익절 정보
    "second_tp_done": False,
    "second_tp_price": 0.0,
    "second_tp_qty": 0.0,
    "second_tp_time": "",
    "second_tp_pnl": 0.0,

    # 손절 정보
    "sl_done": False,
    "sl_price": 0.0,
    "sl_qty": 0.0,
    "sl_time": "",
    "sl_pnl": 0.0,

    # 현재가 & PnL
    "current_price": 0.0,
    "pnl": 0.0,
    
    # —— 아래가 새로 추가된 일일 정산용 카운터들 ——  
    "trade_count": 0,       # 신호 받을 때마다 +1  
    "first_tp_count": 0,    # 1차 익절 시 +1  
    "second_tp_count": 0,   # 2차 익절 시 +1  
    "sl_count": 0,          # 손절 시 +1  
    "daily_pnl": 0.0,       # 모든 익절/손절 PnL 합산(%)  
    "last_reset": ""        # 마지막 리셋 일자(YYYY-MM-DD)  
}