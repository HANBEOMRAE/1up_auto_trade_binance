import time
import datetime
import os
from binance.client import Client
from dotenv import load_dotenv
from utils.logger import Logger
from utils.monitor import update_monitor_data

# 환경 변수에서 API 키 로드
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# Binance 클라이언트 및 로거 초기화
client = Client(API_KEY, API_SECRET)
logger = Logger()

# 기본 설정값
LEVERAGE = 5
INITIAL_CAPITAL = 100.0

# 상태 저장 딕셔너리
capital_map = {}  # 심볼별 자본 상태
position_states = {}  # 심볼별 포지션 상태
last_switch_time = {}  # 심볼별 마지막 스위칭 시간

# 포지션 상태 초기화 함수
def get_position_state(symbol):
    if symbol not in position_states:
        position_states[symbol] = {
            "side": None,
            "entry_price": 0.0,
            "tp1_done": False,
            "tp2_done": False,
            "stop_loss_shifted": False
        }
    return position_states[symbol]

# 포지션 상태 리셋 함수
def reset_state(symbol):
    position_states[symbol] = {
        "side": None,
        "entry_price": 0.0,
        "tp1_done": False,
        "tp2_done": False,
        "stop_loss_shifted": False
    }

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
    state = get_position_state(symbol)
    if state["side"]:
        logger.log(f"{symbol} 포지션 이미 존재")
        return

    # 레버리지 설정
    set_leverage(symbol, LEVERAGE)

    # 자본 초기화
    if symbol not in capital_map:
        capital_map[symbol] = INITIAL_CAPITAL

    amount_to_use = capital_map[symbol] * 0.98
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    quantity = round((amount_to_use * LEVERAGE) / price, 3)

    try:
        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
        state["side"] = side
        state["entry_price"] = price
        logger.log(f"{symbol} 진입: {side}, 가격: {price}, 수량: {quantity}, 자금: {amount_to_use}")
    except Exception as e:
        logger.log(f"{symbol} 진입 실패: {e}")

# 포지션 청산 함수
def close_position(symbol):
    state = get_position_state(symbol)
    quantity = get_current_position_quantity(symbol)
    if quantity == 0:
        logger.log(f"{symbol} 청산할 포지션 없음")
        reset_state(symbol)
        return

    side = 'SELL' if state["side"] == 'BUY' else 'BUY'
    try:
        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
            reduceOnly=True
        )
        price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        capital_map[symbol] = (quantity * price) / LEVERAGE
        logger.log(f"{symbol} 청산 완료: {side}, 현재 자본: {capital_map[symbol]:.2f}")
    except Exception as e:
        logger.log(f"{symbol} 청산 실패: {e}")
    finally:
        reset_state(symbol)

# 익절/손절 조건 확인 함수
def check_exit_conditions(symbol):
    state = get_position_state(symbol)
    if not state["side"]:
        return

    update_monitor_data(symbol)
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    entry = state["entry_price"]
    change = ((price - entry) / entry) * 100
    if state["side"] == 'SELL':
        change = -change

    try:
        # 손절: -0.5%
        if change <= -0.5:
            logger.log(f"{symbol} 손절 발생: {change:.2f}%")
            close_position(symbol)
            return

        # 1차 익절: +0.5%에 30% 청산
        if change >= 0.5 and not state["tp1_done"]:
            qty = round(get_current_position_quantity(symbol) * 0.3, 3)
            client.futures_create_order(
                symbol=symbol,
                side='SELL' if state["side"] == 'BUY' else 'BUY',
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            state["tp1_done"] = True
            state["stop_loss_shifted"] = True
            logger.log(f"{symbol} 1차 익절: +0.5%")
            return

        # 익절 후 회귀 시 전체 청산
        if state["tp1_done"] and state["stop_loss_shifted"] and change < 0.1:
            logger.log(f"{symbol} 익절 후 회귀 청산")
            close_position(symbol)
            return

        # 2차 익절: +1.1%에 50% 청산
        if change >= 1.1 and not state["tp2_done"]:
            qty = round(get_current_position_quantity(symbol) * 0.5, 3)
            client.futures_create_order(
                symbol=symbol,
                side='SELL' if state["side"] == 'BUY' else 'BUY',
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            state["tp2_done"] = True
            logger.log(f"{symbol} 2차 익절: +1.1%")
    except Exception as e:
        logger.log(f"{symbol} 익절/손절 조건 실행 오류: {e}")

# 트레이딩 신호 처리 함수
def handle_signal(symbol, side):
    now = datetime.datetime.utcnow()
    state = get_position_state(symbol)

    # 역신호 시 포지션 청산 후 1분 유예 후 진입
    if state["side"] and state["side"] != side:
        logger.log(f"{symbol} 역신호 감지 → 전체 청산 후 1분봉 대기 진입")
        close_position(symbol)
        last_switch_time[symbol] = now
        return

    # 스위칭 유예 시간 체크
    if symbol in last_switch_time:
        diff = (now - last_switch_time[symbol]).total_seconds()
        if diff < 60:
            logger.log(f"{symbol} 1분 유예 대기 중... ({int(60-diff)}초 남음)")
            return

    # 포지션 미보유 시 진입
    if not state["side"]:
        enter_position(symbol, side)
