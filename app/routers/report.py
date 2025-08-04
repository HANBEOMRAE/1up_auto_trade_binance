# app/routers/report.py

import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.state import monitor_states, get_state

router = APIRouter()
logger = logging.getLogger("report")


def _compute_period_date(now: datetime) -> str:
    # 09시 이전이면 어제, 이후면 오늘
    if now.hour >= 9:
        return now.strftime("%Y-%m-%d")
    else:
        prev = now - timedelta(days=1)
        return prev.strftime("%Y-%m-%d")


def _build_single_report(sym: str) -> dict:
    state = get_state(sym)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    period_date = _compute_period_date(now)

    return {
        "symbol":           sym,
        "period":           period_date,
        "total_trades":     state.get("trade_count", 0),
        "1차_익절횟수":      state.get("first_tp_count", 0),
        "2차_익절횟수":      state.get("second_tp_count", 0),
        "손절횟수":         state.get("sl_count", 0),
        "총_수익률(%)":     round(state.get("daily_pnl", 0.0), 2),
        "last_reset":       state.get("last_reset", None),
    }


@router.get("/report", response_class=JSONResponse)
async def report(
    symbol: str = Query(None, description="조회할 심볼 (예: ETH/USDT 또는 ETHUSDT)"),
    all: bool = Query(False, description="모든 심볼에 대해 리포트 반환")
):
    """
    리포트 조회 (리셋 없음)
    """
    if all:
        reports = []
        for sym in monitor_states.keys():
            reports.append(_build_single_report(sym))
        logger.info(f"Report all symbols: count={len(reports)}")
        return JSONResponse({"reports": reports})

    # 단일 심볼
    if symbol:
        sym = symbol.upper().replace("/", "")
        if sym not in monitor_states:
            raise HTTPException(status_code=404, detail=f"No data for symbol {sym}")
    else:
        try:
            sym = next(iter(monitor_states))
        except StopIteration:
            raise HTTPException(status_code=404, detail="No symbol data available")

    data = _build_single_report(sym)
    logger.info(f"Report [{sym}]: {data}")
    return JSONResponse(data)


@router.post("/report/reset", response_class=JSONResponse)
async def reset_report(
    symbol: str = Query(..., description="리셋할 심볼 (예: ETH/USDT 또는 ETHUSDT)")
):
    """
    해당 심볼의 집계 상태를 초기화한다. (조회와 분리되어 있어 명시적 호출 필요)
    """
    sym = symbol.upper().replace("/", "")
    if sym not in monitor_states:
        raise HTTPException(status_code=404, detail=f"No data for symbol {sym}")

    state = get_state(sym)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    period_date = _compute_period_date(now)

    # 초기화
    state.update({
        "trade_count":     0,
        "first_tp_count":  0,
        "second_tp_count": 0,
        "sl_count":        0,
        "daily_pnl":       0.0,
        "last_reset":      period_date
    })

    result = {"status": "reset", "symbol": sym, "last_reset": period_date}
    logger.info(f"Reset report state: {result}")
    return JSONResponse(result)