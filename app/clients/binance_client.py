# app/clients/binance_client.py

import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import EX_API_KEY, EX_API_SECRET

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 싱글톤으로 Client 인스턴스 관리
_binance_client: Client | None = None


def _ensure_hedge_mode(client: Client) -> None:
    """
    Binance Futures 계정을 Hedge Mode(dualSidePosition=True)로 설정합니다.
    이미 Hedge Mode면 아무 작업도 하지 않습니다.
    """
    try:
        mode = client.futures_get_position_mode()

        if mode.get("dualSidePosition") is True:
            logger.info("Binance account already in Hedge Mode.")
            return

        logger.info("Switching Binance account to Hedge Mode...")
        client.futures_change_position_mode(dualSidePosition=True)

        # 변경 확인
        confirm = client.futures_get_position_mode()
        if confirm.get("dualSidePosition") is not True:
            raise RuntimeError("Failed to enable Hedge Mode.")

        logger.info("Hedge Mode enabled successfully.")

    except BinanceAPIException as e:
        # 이미 Hedge Mode거나 변경 불가능한 상태일 수 있음
        logger.warning("Binance API exception while setting Hedge Mode: %s", e)
    except Exception as e:
        logger.error("Unexpected error while enabling Hedge Mode: %s", e)
        raise


def get_binance_client() -> Client:
    """
    실거래용 Binance Client를 반환합니다.
    EX_API_KEY/EX_API_SECRET 환경변수가 설정되어 있지 않으면 에러를 발생시킵니다.
    최초 생성 시 Hedge Mode를 자동으로 활성화합니다.
    """
    global _binance_client

    if _binance_client is None:
        if not EX_API_KEY or not EX_API_SECRET:
            logger.error("Binance API 키/시크릿이 .env에 설정되지 않았습니다.")
            raise RuntimeError("Missing Binance API credentials.")

        # 실제 거래용 Client 생성
        _binance_client = Client(EX_API_KEY, EX_API_SECRET)
        logger.info("Initialized live Binance Client.")

        # ⭐ 여기서 Hedge Mode 보장
        _ensure_hedge_mode(_binance_client)

    return _binance_client