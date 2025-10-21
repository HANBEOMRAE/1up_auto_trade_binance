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
    if now.hour >= 9:
        return now.strftime("%Y-%m-%d")
    else:
        prev = now - timedelta(days=1)
        return prev.strftime("%Y-%m-%d")


def _calculate_cumulative_return(current_capital: float, initial_capital: float) -> float:
    if initial_capital == 0:
        return 0.0
    return round(((current_capital / initial_capital) - 1.0) * 100, 2)


def _build_single_report(sym: str) -> dict:
    state = get_state(sym)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    period_date = _compute_period_date(now)

    capital = state.get("capital", 0.0)
    initial = state.get("initial_capital", 1.0)

    return {
        "symbol":           sym,
        "period":           period_date,
        "total_trades":     state.get("trade_count", 0),
        "long_entries":     state.get("long_count", 0),
        "short_entries":    state.get("short_count", 0),
        "현재_자본($)":     round(capital, 2),
        "복리_수익률(%)":    _calculate_cumulative_return(capital, initial),
        "last_reset":       state.get("last_reset", None),
    }


@router.get("/report", response_class=JSONResponse)
async def report(
    symbol: str = Query(None, description="조회할 심볼 (예: ETH/USDT 또는 ETHUSDT)"),
    all: bool = Query(False, description="모든 심볼에 대해 리포트 반환")
):
    if all:
        reports = [_build_single_report(sym) for sym in monitor_states]
        logger.info(f"Report all symbols: count={len(reports)}")
        return JSONResponse({"reports": reports})

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
    sym = symbol.upper().replace("/", "")
    if sym not in monitor_states:
        raise HTTPException(status_code=404, detail=f"No data for symbol {sym}")

    state = get_state(sym)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    period_date = _compute_period_date(now)

    # 리셋 시 현재 자본을 기준으로 초기 자본 갱신
    capital_now = state.get("capital", 30.0)
    state.update({
        "trade_count":     0,
        "long_count":      0,
        "short_count":     0,
        "daily_pnl":       0.0,
        "initial_capital": capital_now,
        "last_reset":      period_date
    })

    result = {"status": "reset", "symbol": sym, "last_reset": period_date}
    logger.info(f"Reset report state: {result}")
    return JSONResponse(result)