# app/routers/webhook.py

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DRY_RUN
from app.services.switching import switch_position
from app.state import monitor_state

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

        # 정상 매매 체결 정보 반영
        now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")

        if action == "BUY":
            info = res.get("buy", {})
            entry = float(info.get("entry", 0))
            qty   = float(info.get("filled", 0))

            monitor_state.update({
                "symbol":        sym,
                "entry_price":   entry,
                "position_qty":  qty,
                "entry_time":    now,
                "first_tp_done":  False,
                "second_tp_done": False,
                "sl_done":        False,
            })

        else:  # SELL
            info = res.get("sell", {})
            entry = float(info.get("entry", 0))

            monitor_state.update({
                "symbol":        sym,
                "entry_price":   entry,
                "position_qty":  0.0,     # 숏은 모니터에서 qty=0 처리
                "entry_time":    now,
                "first_tp_done":  False,
                "second_tp_done": False,
                "sl_done":        False,
            })

    except Exception as e:
        logger.exception(f"Error processing {action} for {sym}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "result": res}