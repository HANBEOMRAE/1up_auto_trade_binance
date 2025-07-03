# trade_manager.py - 리팩토링 버전 (설명 포함 주석 추가)

import time
from binance.client import Client
from dotenv import load_dotenv
import os
from utils.logger import Logger
from utils.monitor import update_monitor_data

# .env 파일에서 API 키 불러오기
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# Binance API 클라이언트 및 로거 초기화
client = Client(API_KEY, API_SECRET)
logger = Logger()

# 설정 값
LEVERAGE = 5  # 레버리지 배율
INITIAL_CAPITAL = 100.0  # 시작 자본 (USDT)
capital = INITIAL_CAPITAL  # 거래 후 갱신되는 자본

# 포지션 상태 추적용 딕셔너리
position_state = {
    "side": None,  # BUY 또는 SELL
    "entry_price": 0.0,  # 진입 가격
    "tp1_done": False,  # 1차 익절 여부
    "tp2_done": False,  # 2차 익절 여부
    "stop_loss_shifted": False  # 익절 후 손절 조건 조정 여부
}

# 포지션 상태 초기화 함수
def reset_state():
    position_state.update({
        "side": None,
        "entry_price": 0.0,
        "tp1_done": False,
        "tp2_done": False,
        "stop_loss_shifted": False
    })

# 레버리지 설정 함수
def set_leverage(symbol, leverage):
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logger.log(f"{symbol} 레버리지 {leverage}배 설정 완료")
    except Exception as e:
        logger.log(f"레버리지 설정 오류: {e}")

# 현재 포지션 수량 조회 함수
def get_current_position_quantity(symbol):
    try:
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            if float(p['positionAmt']) != 0:
                return abs(float(p['positionAmt']))
    except Exception as e:
        logger.log(f"포지션 수량 조회 오류: {e}")
    return 0

# 포지션 진입 함수
def enter_position(symbol, side):
    if position_state["side"]:
        logger.log("포지션 이미 존재")
        return

    set_leverage(symbol, LEVERAGE)

    # 자본의 98% 사용하여 진입 수량 계산
    amount_to_use = capital * 0.98
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    quantity = round((amount_to_use * LEVERAGE) / price, 3)

    try:
        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
        # 상태 업데이트
        position_state["side"] = side
        position_state["entry_price"] = price
        logger.log(f"진입: {side}, 가격: {price}, 수량: {quantity}, 자금: {amount_to_use}")
    except Exception as e:
        logger.log(f"진입 실패: {e}")

# 포지션 청산 함수
def close_position(symbol):
    global capital

    quantity = get_current_position_quantity(symbol)
    if quantity == 0:
        logger.log("청산할 포지션 없음")
        reset_state()
        return

    side = 'SELL' if position_state["side"] == 'BUY' else 'BUY'
    try:
        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
            reduceOnly=True
        )
        price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        capital = (quantity * price) / LEVERAGE  # 복리 자본 갱신
        logger.log(f"청산 완료: {side}, 현재 자본: {capital:.2f}")
    except Exception as e:
        logger.log(f"청산 실패: {e}")
    finally:
        reset_state()

# 익절/손절 조건 확인 함수
def check_exit_conditions(symbol):
    if not position_state["side"]:
        return

    update_monitor_data(symbol)
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    entry = position_state["entry_price"]
    change = ((price - entry) / entry) * 100
    if position_state["side"] == 'SELL':
        change = -change

    try:
        # 손절 조건: -0.5% 하락
        if change <= -0.5:
            logger.log(f"손절 발생: {change:.2f}%")
            close_position(symbol)
            return

        # 1차 익절 조건: +0.5%
        if change >= 0.5 and not position_state["tp1_done"]:
            qty = round(get_current_position_quantity(symbol) * 0.3, 3)
            client.futures_create_order(
                symbol=symbol,
                side='SELL' if position_state["side"] == 'BUY' else 'BUY',
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            position_state["tp1_done"] = True
            position_state["stop_loss_shifted"] = True
            logger.log("1차 익절: +0.5%")
            return

        # 익절 후 회귀 조건: +0.1% 미만으로 떨어지면 전량 청산
        if position_state["tp1_done"] and position_state["stop_loss_shifted"] and change < 0.1:
            logger.log("익절 후 회귀 청산")
            close_position(symbol)
            return

        # 2차 익절 조건: +1.1%
        if change >= 1.1 and not position_state["tp2_done"]:
            qty = round(get_current_position_quantity(symbol) * 0.5, 3)
            client.futures_create_order(
                symbol=symbol,
                side='SELL' if position_state["side"] == 'BUY' else 'BUY',
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            position_state["tp2_done"] = True
            logger.log("2차 익절: +1.1%")
    except Exception as e:
        logger.log(f"익절/손절 조건 실행 오류: {e}")

# 외부 신호 처리 함수 (TradingView 등에서 호출됨)
def handle_signal(symbol, side):
    if position_state["side"] and position_state["side"] != side:
        logger.log("역신호 감지 → 포지션 종료 후 스위칭")
        close_position(symbol)
        time.sleep(30)  # 청산 지연 후 재진입
        enter_position(symbol, side)
    elif not position_state["side"]:
        enter_position(symbol, side)
