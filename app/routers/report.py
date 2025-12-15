# app/routers/report.py

import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.state import monitor_states, get_state, list_symbols

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


def _build_single_report(profile: str, sym: str) -> dict:
    state = get_state(sym, profile)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    period_date = _compute_period_date(now)

    capital = state.get("capital", 0.0)
    initial = state.get("initial_capital", 1.0)

    return {
        "profile": profile,
        "symbol":           sym,
        "period":           period_date,
        "total_trades":     state.get("trade_count", 0),
        "long_entries":     state.get("long_count", 0),
        "short_entries":    state.get("short_count", 0),
        "현재_자본($)":     round(capital, 2),
        "복리_수익률(%)":    _calculate_cumulative_return(capital, initial),
        "daily_pnl(%)":     round(state.get("daily_pnl", 0.0), 2),
        "initial_capital":  round(initial, 2),
        "last_reset":       state.get("last_reset", None),
    }

async def _report_internal(
    profile: str,
    symbol: str | None,
    all: bool,
):
    symbols = list_symbols(profile)

    if all:
        reports = [_build_single_report(profile, sym) for sym in symbols]
        logger.info(f"Report all symbols for {profile}: count={len(reports)}")
        return JSONResponse({"profile": profile, "reports": reports})

    if symbol:
        sym = symbol.upper().replace("/", "")
        if sym not in symbols:
            raise HTTPException(status_code=404, detail=f"No data for {profile}:{sym}")
    else:
        try:
            sym = symbols[0]
        except IndexError:
            raise HTTPException(status_code=404, detail=f"No symbol data available for {profile}")

    data = _build_single_report(profile, sym)
    logger.info(f"Report [{profile}:{sym}]: {data}")
    return JSONResponse(data)


@router.get("/report", response_class=JSONResponse)
async def report(
    symbol: str | None = Query(None, description="조회할 심볼 (예: ETH/USDT 또는 ETHUSDT)"),
    all: bool = Query(False, description="해당 profile의 모든 심볼 리포트"),
):
    # 기본: webhook1용
    return await _report_internal("webhook1", symbol, all)

@router.get("/report2", response_class=JSONResponse)
async def report2(
    symbol: str | None = Query(None, description="조회할 심볼 (예: ETH/USDT 또는 ETHUSDT)"),
    all: bool = Query(False, description="해당 profile의 모든 심볼 리포트"),
):
    return await _report_internal("webhook2", symbol, all)


@router.get("/report3", response_class=JSONResponse)
async def report3(
    symbol: str | None = Query(None, description="조회할 심볼 (예: ETH/USDT 또는 ETHUSDT)"),
    all: bool = Query(False, description="해당 profile의 모든 심볼 리포트"),
):
    return await _report_internal("webhook3", symbol, all)

@router.get("/report4", response_class=JSONResponse)
async def report4(
    symbol: str | None = Query(None, description="조회할 심볼 (예: ETH/USDT 또는 ETHUSDT)"),
    all: bool = Query(False, description="해당 profile의 모든 심볼 리포트"),
):
    return await _report_internal("webhook4", symbol, all)


# ── reset 로직 ─────────────────────────────────────────
def _reset_internal(profile: str, symbol: str) -> dict:
    sym = symbol.upper().replace("/", "")
    state = get_state(sym, profile)  # 없으면 생성됨

    now = datetime.now(ZoneInfo("Asia/Seoul"))
    period_date = _compute_period_date(now)

    # 현재 자본을 새로운 기준 자본으로 사용
    capital_now = float(state.get("capital", 50.0))

    state.update(
        {
            # 카운터 초기화
            "trade_count": 0,
            "long_count": 0,
            "short_count": 0,
            "daily_pnl": 0.0,

            # 자본 기준 재설정
            "capital": capital_now,
            "initial_capital": capital_now,

            # 포지션/가격 정보 초기화
            "entry_price": 0.0,
            "position_qty": 0.0,
            "position_side": None,
            "current_price": 0.0,
            "pnl": 0.0,

            "last_reset": period_date,
        }
    )

    result = {
        "status": "reset",
        "profile": profile,
        "symbol": sym,
        "last_reset": period_date,
        "capital": capital_now,
        "initial_capital": capital_now,
    }
    logger.info(f"Reset report state: {result}")
    return result


@router.post("/report/reset", response_class=JSONResponse)
async def reset_report(
    symbol: str = Query(..., description="리셋할 심볼 (예: ETH/USDT 또는 ETHUSDT)"),
):
    result = _reset_internal("webhook1", symbol)
    return JSONResponse(result)


@router.post("/report2/reset", response_class=JSONResponse)
async def reset_report2(
    symbol: str = Query(..., description="리셋할 심볼 (예: ETH/USDT 또는 ETHUSDT)"),
):
    result = _reset_internal("webhook2", symbol)
    return JSONResponse(result)


@router.post("/report3/reset", response_class=JSONResponse)
async def reset_report3(
    symbol: str = Query(..., description="리셋할 심볼 (예: ETH/USDT 또는 ETHUSDT)"),
):
    result = _reset_internal("webhook3", symbol)
    return JSONResponse(result)

@router.post("/report4/reset", response_class=JSONResponse)
async def reset_report4(
    symbol: str = Query(..., description="리셋할 심볼 (예: ETH/USDT 또는 ETHUSDT)"),
):
    result = _reset_internal("webhook4", symbol)
    return JSONResponse(result)