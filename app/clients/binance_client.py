# app/clients/binance_client.py

import logging
from binance.client import Client
from app.config import EX_API_KEY, EX_API_SECRET

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 싱글톤으로 Client 인스턴스 관리
_binance_client: Client | None = None

def get_binance_client() -> Client:
    """
    실거래용 Binance Client를 반환합니다.
    EX_API_KEY/EX_API_SECRET 환경변수가 설정되어 있지 않으면 에러를 발생시킵니다.
    """
    global _binance_client

    if _binance_client is None:
        if not EX_API_KEY or not EX_API_SECRET:
            logger.error("Binance API 키/시크릿이 .env에 설정되지 않았습니다.")
            raise RuntimeError("Missing Binance API credentials.")
        # 실제 거래용 Client 생성
        _binance_client = Client(EX_API_KEY, EX_API_SECRET)
        logger.info("Initialized live Binance Client.")

    return _binance_client