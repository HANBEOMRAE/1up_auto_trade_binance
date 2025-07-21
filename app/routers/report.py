# app/routers/report.py

import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from datetime import datetime
from zoneinfo import ZoneInfo
from app.state import monitor_states, get_state

router = APIRouter()
logger = logging.getLogger("report")

@router.get("/report", response_class=JSONResponse)
async def report(symbol: str = Query(None, description="조회할 심볼 (예: ETH/USDT 또는 ETHUSDT)")):
    """
    일일 정산 리포트:
    - symbol: 조회할 심볼 (쿼리 파라미터 or 기본 첫 번째)
    - period: 보고 대상 날짜 (09시 기준 어제 날짜)
    - total_trades, tp1_count, tp2_count, sl_count, total_pnl
    """
    # 1) symbol 파라미터 처리
    if symbol:
        sym = symbol.upper().replace("/", "")
        if sym not in monitor_states:
            raise HTTPException(status_code=404, detail=f"No data for symbol {sym}")
    else:
        try:
            sym = next(iter(monitor_states))
        except StopIteration:
            raise HTTPException(status_code=404, detail="No symbol data available")

    # 2) 해당 심볼 상태 가져오기
    state = get_state(sym)

    # 3) 리포트 날짜 결정 (09시 이전 → 어제, 이후 → 오늘)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    period_date = (now if now.hour >= 9 else
                   now.replace(day=now.day-1)).strftime("%Y-%m-%d")

    # 4) 집계 정보 구성
    data = {
        "symbol":           sym,
        "period":           period_date,
        "total_trades":     state.get("trade_count", 0),
        "1차_익절횟수":      state.get("first_tp_count", 0),
        "2차_익절횟수":      state.get("second_tp_count", 0),
        "손절횟수":         state.get("sl_count", 0),
        "총_수익률(%)":     round(state.get("daily_pnl", 0.0), 2),
    }

    # 5) 로그 남기기
    logger.info(f"Daily Report [{sym}][{period_date}]: {data}")

    # 6) 리셋 (해당 심볼 카운터 초기화)
    state.update({
        "trade_count":     0,
        "first_tp_count":  0,
        "second_tp_count": 0,
        "sl_count":        0,
        "daily_pnl":       0.0,
        "last_reset":      period_date
    })

    return JSONResponse(data)