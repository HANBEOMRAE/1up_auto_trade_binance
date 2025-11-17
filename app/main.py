# app/main.py

from fastapi import FastAPI
from app.routers.webhook import router as webhook_router
#from app.routers.dashboard import router as dashboard_router
from app.routers.report import router as report_router, report
import threading
import logging
#from app.services.monitor import start_monitor

# APScheduler imports
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from zoneinfo import ZoneInfo

app = FastAPI()

@app.on_event("startup")
def on_startup():
    """
    앱 기동 시:
    1) 모니터 스레드 안전 실행
    2) 매일 KST 09:00에 일일 리포트 실행 스케줄러 등록
    """

    # 1) 일일 리포트 스케줄러 (Asia/Seoul 09:00)
    sched = BackgroundScheduler(timezone="Asia/Seoul")
    # 매일 오전 09:00에 report() 호출
    sched.add_job(lambda: report(), 'cron', hour=9, minute=0)
    sched.start()


# 라우터 등록
app.include_router(webhook_router)
#app.include_router(dashboard_router)
app.include_router(report_router)


@app.get("/health")
def health():
    return {"status": "alive"}