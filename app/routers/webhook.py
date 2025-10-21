import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DRY_RUN
from app.services.switching import switch_position
from app.state import get_state

logger = logging.getLogger("webhook")
router = APIRouter()

class AlertPayload(BaseModel):
    symbol: str   # e.g. "ETH/USDT"
    action: str   # BUY, SELL, BUY_STOP, SELL_STOP

@router.post("/webhook")
async def webhook(payload: AlertPayload):
    sym    = payload.symbol.upper().replace("/", "")
    action = payload.action.upper()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] {action} {sym}")
        return {"status": "dry_run"}

    try:
        res = switch_position(sym, action)

        if "skipped" in res:
            logger.info(f"Skipped {action} {sym}: {res['skipped']}")
            return {"status": "skipped", "reason": res["skipped"]}

        state = get_state(sym)
        now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")

        if action == "BUY":
            info = res.get("buy", {})
            entry = float(info.get("entry", 0))
            qty   = float(info.get("filled", 0))
            state.update({
                "entry_price":   entry,
                "position_qty":  qty,
                "entry_time":    now
            })

        elif action == "SELL":
            info = res.get("sell", {})
            entry = float(info.get("entry", 0))
            qty   = float(info.get("filled", 0))
            state.update({
                "entry_price":   entry,
                "position_qty":  -qty,
                "entry_time":    now
            })

        elif action in ("BUY_STOP", "SELL_STOP"):
            # ✅ exit_price / pnl 로그 찍기
            exit_price = res.get("exit_price", 0.0)
            pnl        = res.get("pnl", 0.0)

            state.update({
                "entry_price":   0.0,
                "position_qty":  0.0,
                "entry_time":    now
            })

            logger.info(f"[{action}] {sym} EXIT @ {exit_price}, PnL {pnl:.2f}%")

    except Exception as e:
        logger.exception(f"Error processing {action} for {sym}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "result": res}


# ✅ webhook2는 동일 (단, 필요 시 같은 방식으로 STOP 로그 추가 가능)
@router.post("/webhook2")
async def webhook2(payload: AlertPayload):
    sym    = payload.symbol.upper().replace("/", "")
    action = payload.action.upper()

    # 👉 원하는 커스텀 레버리지 설정
    custom_leverage = 10

    if DRY_RUN:
        logger.info(f"[DRY_RUN] {action} {sym}")
        return {"status": "dry_run"}

    try:
        res = switch_position(sym, action, leverage=custom_leverage, use_initial_capital=True)

        if "skipped" in res:
            logger.info(f"Skipped {action} {sym}: {res['skipped']}")
            return {"status": "skipped", "reason": res["skipped"]}

        state = get_state(sym)
        now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")

        if action == "BUY":
            info = res.get("buy", {})
            entry = float(info.get("entry", 0))
            qty   = float(info.get("filled", 0))
            state.update({
                "entry_price":   entry,
                "position_qty":  qty,
                "entry_time":    now
            })

        elif action == "SELL":
            info = res.get("sell", {})
            entry = float(info.get("entry", 0))
            qty   = float(info.get("filled", 0))
            state.update({
                "entry_price":   entry,
                "position_qty":  -qty,
                "entry_time":    now
            })

        elif action in ("BUY_STOP", "SELL_STOP"):
            exit_price = res.get("exit_price", 0.0)
            pnl        = res.get("pnl", 0.0)

            state.update({
                "entry_price":   0.0,
                "position_qty":  0.0,
                "entry_time":    now
            })

            logger.info(f"[{action}] {sym} EXIT @ {exit_price}, PnL {pnl:.2f}%")

    except Exception as e:
        logger.exception(f"Error switching in webhook2 for {action} {sym}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "result": res}