# app/routers/webhook.py

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DRY_RUN
from app.services.switching import switch_position, switch_position2
from app.state import get_state
from app.services.simple_buy import execute_simple_buy
from app.services.simple_sell import execute_simple_sell

logger = logging.getLogger("webhook")
router = APIRouter()

class AlertPayload(BaseModel):
    symbol: str   # e.g. "ETH/USDT"
    action: str   # "BUY" or "SELL"

@router.post("/webhook")
async def webhook(payload: AlertPayload):
    sym    = payload.symbol.upper().replace("/", "")
    action = payload.action.upper()

    # Dry-run 모드면 리턴
    if DRY_RUN:
        logger.info(f"[DRY_RUN] {action} {sym}")
        return {"status": "dry_run"}

    try:
        # 포지션 스위칭 (청산 + 새 진입)
        res = switch_position(sym, action)

        # 이미 같은 방향 포지션이 있으면 스킵
        if "skipped" in res:
            logger.info(f"Skipped {action} {sym}: {res['skipped']}")
            return {"status": "skipped", "reason": res["skipped"]}

        # 상태 객체 가져오기 (심볼별)
        state = get_state(sym)
        now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")

        # 정상 매매 체결 정보 반영
        if action == "BUY":
            info = res.get("buy", {})
            entry = float(info.get("entry", 0))
            qty   = float(info.get("filled", 0))
            state.update({
                "entry_price":   entry,
                "position_qty":  qty,
                "entry_time":    now,
                "first_tp_done":  False,
                "second_tp_done": False,
                "sl_done":        False,
                # reset TP/SL details
                "first_tp_price":    0.0,
                "first_tp_qty":      0.0,
                "first_tp_time":     "",
                "first_tp_pnl":      0.0,
                "second_tp_price":   0.0,
                "second_tp_qty":     0.0,
                "second_tp_time":    "",
                "second_tp_pnl":     0.0,
                "sl_price":          0.0,
                "sl_qty":            0.0,
                "sl_time":           "",
                "sl_pnl":            0.0
            })
        else:  # SELL
            info = res.get("sell", {})
            entry = float(info.get("entry", 0))
            state.update({
                "entry_price":   entry,
                "position_qty":  0.0,
                "entry_time":    now,
                "first_tp_done":  False,
                "second_tp_done": False,
                "sl_done":        False,
                # reset TP/SL details
                "first_tp_price":    0.0,
                "first_tp_qty":      0.0,
                "first_tp_time":     "",
                "first_tp_pnl":      0.0,
                "second_tp_price":   0.0,
                "second_tp_qty":     0.0,
                "second_tp_time":    "",
                "second_tp_pnl":     0.0,
                "sl_price":          0.0,
                "sl_qty":            0.0,
                "sl_time":           "",
                "sl_pnl":            0.0
            })

    except Exception as e:
        logger.exception(f"Error processing {action} for {sym}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "result": res}

@router.post("/webhook2")
async def webhook2(payload: AlertPayload):
    sym    = payload.symbol.upper().replace("/", "")
    action = payload.action.upper()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] {action} {sym}")
        return {"status": "dry_run"}

    try:
        # ✅ 스위칭 수행 (기존 포지션 청산 후 진입)
        res = switch_position2(sym, action)

        if "skipped" in res:
            logger.info(f"Skipped {action} {sym}: {res['skipped']}")
            return {"status": "skipped", "reason": res["skipped"]}

    except Exception as e:
        logger.exception(f"Error switching in webhook2 for {action} {sym}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "result": res}