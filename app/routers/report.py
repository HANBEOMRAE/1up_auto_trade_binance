# app/routers/report.py

import logging
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from datetime import datetime
from zoneinfo import ZoneInfo
from app.state import monitor_state

router = APIRouter()
logger = logging.getLogger("report")

@router.get("/report", response_class=JSONResponse)
async def report():
    """
    일일 정산 리포트:
    - period: 보고 대상 날짜 (09시 기준 어제 날짜)
    - total_trades, tp1_count, tp2_count, sl_count, total_pnl
    """
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    # 09시 이전이면 어제, 이후면 오늘 기준으로 리포트 대상일 설정
    period_date = (now if now.hour >= 9 else now.replace(day=now.day-1))\
                    .strftime("%Y-%m-%d")

    # 현재 집계값 읽기
    data = {
        "period":        period_date,
        "total_trades":  monitor_state.get("trade_count", 0),
        "1차_익절횟수":   monitor_state.get("first_tp_count", 0),
        "2차_익절횟수":   monitor_state.get("second_tp_count", 0),
        "손절횟수":      monitor_state.get("sl_count", 0),
        "총_수익률(%)":  round(monitor_state.get("daily_pnl", 0.0), 2),
    }

    # 로그에도 남기고
    logger.info(f"Daily Report [{period_date}]: {data}")

    # 카운터 리셋
    monitor_state.update({
        "trade_count":      0,
        "first_tp_count":   0,
        "second_tp_count":  0,
        "sl_count":         0,
        "daily_pnl":        0.0,
        "last_reset":       period_date
    })

    return JSONResponse(data)