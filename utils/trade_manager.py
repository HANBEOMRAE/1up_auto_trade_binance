import time
import datetime
import os
from binance.client import Client
from dotenv import load_dotenv
from utils.logger import Logger
from utils.monitor import update_monitor_data
import math

# .env 파일에서 API 키 로드
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# Binance API 클라이언트 및 로그 모듈 초기화
client = Client(API_KEY, API_SECRET)
logger = Logger()

# 설정값
LEVERAGE = 5
INITIAL_CAPITAL = 100.0

capital_map = {}
position_states = {}
last_switch_time = {}

def get_stepsize(symbol):
    info = client.futures_exchange_info()
    for s in info['symbols']:
        if s['symbol'] == symbol:
            for f in s['filters']:
                if f['filterType'] == 'LOT_SIZE':
                    return float(f['stepSize'])
    return 0.001

def adjust_quantity(symbol, quantity):
    step = get_stepsize(symbol)
    return math.floor(quantity / step) * step

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

def reset_state(symbol):
    position_states[symbol] = {
        "side": None,
        "entry_price": 0.0,
        "tp1_done": False,
        "tp2_done": False,
        "stop_loss_shifted": False
    }

def set_leverage(symbol, leverage):
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logger.log(f"{symbol} 레버리지 {leverage}배 설정 완료")
    except Exception as e:
        logger.log(f"레버리지 설정 오류: {e}")

def get_current_position_quantity(symbol):
    try:
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            if float(p['positionAmt']) != 0:
                return abs(float(p['positionAmt']))
    except Exception as e:
        logger.log(f"포지션 수량 조회 오류: {e}")
    return 0

def enter_position(symbol, side):
    state = get_position_state(symbol)
    if state["side"]:
        logger.log(f"{symbol} 포지션 이미 존재")
        return

    set_leverage(symbol, LEVERAGE)

    if symbol not in capital_map:
        capital_map[symbol] = INITIAL_CAPITAL

    amount_to_use = capital_map[symbol] * 0.98
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    raw_quantity = (amount_to_use * LEVERAGE) / price
    quantity = adjust_quantity(symbol, raw_quantity)

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

def switch_position(symbol, new_side):
    quantity = get_current_position_quantity(symbol)
    if quantity > 0:
        # 1. 기존 포지션 청산
        try:
            client.futures_create_order(
                symbol=symbol,
                side='SELL' if new_side == 'BUY' else 'BUY',
                type='MARKET',
                quantity=adjust_quantity(symbol, quantity),
                reduceOnly=True
            )
            logger.log(f"{symbol} 스위칭: 기존 포지션 청산 완료")
        except Exception as e:
            logger.log(f"{symbol} 스위칭 청산 실패: {e}")
            return

    time.sleep(30)
    logger.log(f"{symbol} 스위칭 30초 대기 후 재진입 시도")
    reset_state(symbol)
    enter_position(symbol, new_side)

def handle_signal(symbol, side):
    side = side.upper()
    now = datetime.datetime.utcnow()
    state = get_position_state(symbol)

    if state["side"] and state["side"] != side:
        logger.log(f"{symbol} 역신호 감지 → 스위칭 처리 시작")
        last_switch_time[symbol] = now
        switch_position(symbol, side)
        return

    if symbol in last_switch_time:
        diff = (now - last_switch_time[symbol]).total_seconds()
        if diff < 60:
            logger.log(f"{symbol} 1분 유예 대기 중... ({int(60-diff)}초 남음)")
            return

    if not state["side"]:
        enter_position(symbol, side)