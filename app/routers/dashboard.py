from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from typing import Optional
from app.state import monitor_states, get_state

router = APIRouter()

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(symbol: Optional[str] = None):
    # 보여줄 심볼을 쿼리 파라미터로 받거나, 저장된 첫 번째 심볼을 기본으로 사용
    if symbol:
        sym = symbol.upper().replace('/', '')
    else:
        try:
            sym = next(iter(monitor_states))
        except StopIteration:
            raise HTTPException(status_code=404, detail="No symbol data available")

    state = get_state(sym)
    data        = state
    entry_price = data.get("entry_price", 0.0)
    entry_time  = data.get("entry_time", "-")
    qty         = data.get("position_qty", 0.0)
    pnl         = data.get("pnl", 0.0)

    first_done  = data.get("first_tp_done", False)
    tp1_price   = data.get("first_tp_price", 0.0)
    tp1_qty     = data.get("first_tp_qty", 0.0)
    tp1_time    = data.get("first_tp_time", "-")
    tp1_pnl     = data.get("first_tp_pnl", 0.0)

    second_done = data.get("second_tp_done", False)
    tp2_price   = data.get("second_tp_price", 0.0)
    tp2_qty     = data.get("second_tp_qty", 0.0)
    tp2_time    = data.get("second_tp_time", "-")
    tp2_pnl     = data.get("second_tp_pnl", 0.0)

    sl_done     = data.get("sl_done", False)
    sl_price    = data.get("sl_price", 0.0)
    sl_qty      = data.get("sl_qty", 0.0)
    sl_time     = data.get("sl_time", "-")
    sl_pnl      = data.get("sl_pnl", 0.0)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>자동매매 대시보드 - {sym}</title>
  <meta http-equiv="refresh" content="3" />
  <style>
    body {{ background:#f0f2f5; font-family: Arial; padding:20px; }}
    h1 {{ text-align:center; margin-bottom:20px; }}
    .card {{ background:#fff; border-radius:8px; padding:16px; margin:10px 0; box-shadow:0 2px 4px rgba(0,0,0,0.1); }}
    h2 {{ margin:0 0 10px; }}
    p {{ margin:4px 0; }}
    .done {{ color:green; }}
    .pending {{ color:orange; }}
  </style>
</head>
<body>
  <h1>자동매매 상태 대시보드: {sym}</h1>

  <div class="card">
    <h2>진입 정보 <span class="{ 'done' if qty>0 else 'pending' }">({ '진행 중' if qty>0 else '미진행'})</span></h2>
    <p><strong>시간:</strong> {entry_time}</p>
    <p><strong>진입가:</strong> {entry_price:.2f} USDT</p>
    <p><strong>수량:</strong> {qty:.4f}</p>
    <p><strong>현재 PnL:</strong> {pnl:.2f}%</p>
  </div>

  <div class="card">
    <h2>1차 익절 <span class="{ 'done' if first_done else 'pending' }">({ '완료' if first_done else '미완료'})</span></h2>
    <p><strong>시간:</strong> {tp1_time}</p>
    <p><strong>체결가:</strong> {tp1_price:.2f} USDT</p>
    <p><strong>수량:</strong> {tp1_qty:.4f}</p>
    <p><strong>수익률:</strong> {tp1_pnl:.2f}%</p>
  </div>

  <div class="card">
    <h2>2차 익절 <span class="{ 'done' if second_done else 'pending' }">({ '완료' if second_done else '미완료'})</span></h2>
    <p><strong>시간:</strong> {tp2_time}</p>
    <p><strong>체결가:</strong> {tp2_price:.2f} USDT</p>
    <p><strong>수량:</strong> {tp2_qty:.4f}</p>
    <p><strong>수익률:</strong> {tp2_pnl:.2f}%</p>
  </div>

  <div class="card">
    <h2>손절 <span class="{ 'done' if sl_done else 'pending' }">({ '완료' if sl_done else '미완료'})</span></h2>
    <p><strong>시간:</strong> {sl_time}</p>
    <p><strong>체결가:</strong> {sl_price:.2f} USDT</p>
    <p><strong>수량:</strong> {sl_qty:.4f}</p>
    <p><strong>손익률:</strong> {sl_pnl:.2f}%</p>
  </div>
</body>
</html>"""
    return HTMLResponse(html)