import os
import time
from binance.client import Client
from dotenv import load_dotenv
from utils.logger import Logger

load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)
logger = Logger()

LEVERAGE = 5
INITIAL_CAPITAL = 100.0
capital = INITIAL_CAPITAL
POSITION = None
entry_price = 0.0
tp1_done = False
tp2_done = False
stop_loss_shifted = False

def set_leverage(symbol, leverage):
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        logger.log(f"레버리지 설정 오류: {e}")

def enter_position(symbol, side):
    global POSITION, entry_price, capital, tp1_done, tp2_done, stop_loss_shifted
    if POSITION:
        logger.log("이미 포지션 보유 중")
        return

    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    quantity = round((capital * 0.98 * LEVERAGE) / price, 3)

    try:
        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
        POSITION = side
        entry_price = price
        tp1_done = False
        tp2_done = False
        stop_loss_shifted = False
        logger.log(f"진입: {side}, 가격: {price}, 수량: {quantity}")
    except Exception as e:
        logger.log(f"진입 오류: {e}")

def close_position(symbol):
    global POSITION, capital, entry_price
    if not POSITION:
        logger.log("청산할 포지션 없음")
        return

    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    quantity = get_current_position_quantity(symbol)
    if quantity == 0:
        logger.log("포지션 없음")
        return

    side = 'SELL' if POSITION == 'BUY' else 'BUY'
    try:
        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
            reduceOnly=True
        )
        capital = quantity * price / LEVERAGE
        logger.log(f"청산 완료: {side}, 현재 자본: {capital}")
    except Exception as e:
        logger.log(f"청산 오류: {e}")
    finally:
        reset_state()

def get_current_position_quantity(symbol):
    positions = client.futures_position_information(symbol=symbol)
    for p in positions:
        if float(p['positionAmt']) != 0:
            return abs(float(p['positionAmt']))
    return 0

def reset_state():
    global POSITION, entry_price, tp1_done, tp2_done, stop_loss_shifted
    POSITION = None
    entry_price = 0.0
    tp1_done = False
    tp2_done = False
    stop_loss_shifted = False

def check_exit_conditions(symbol):
    global tp1_done, tp2_done, stop_loss_shifted

    if not POSITION:
        return

    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    change = ((price - entry_price) / entry_price) * 100
    if POSITION == 'SELL':
        change = -change

    if change <= -0.5:
        logger.log(f"손절: {change:.2f}%")
        close_position(symbol)
        return

    if change >= 0.5 and not tp1_done:
        quantity = get_current_position_quantity(symbol)
        qty = round(quantity * 0.3, 3)
        client.futures_create_order(
            symbol=symbol,
            side='SELL' if POSITION == 'BUY' else 'BUY',
            type="MARKET",
            quantity=qty,
            reduceOnly=True
        )
        tp1_done = True
        stop_loss_shifted = True
        logger.log("1차 익절: +0.5%, 30% 청산")
        return

    if tp1_done and stop_loss_shifted and change < 0.1:
        logger.log("익절 후 +0.1% 미만 회귀, 전량 청산")
        close_position(symbol)
        return

    if change >= 1.1 and not tp2_done:
        quantity = get_current_position_quantity(symbol)
        qty = round(quantity * 0.5, 3)
        client.futures_create_order(
            symbol=symbol,
            side='SELL' if POSITION == 'BUY' else 'BUY',
            type="MARKET",
            quantity=qty,
            reduceOnly=True
        )
        tp2_done = True
        logger.log("2차 익절: +1.1%, 50% 청산")
        return

def handle_signal(symbol, side):
    global POSITION
    if POSITION and POSITION != side:
        logger.log("역신호 → 포지션 청산 후 스위칭")
        close_position(symbol)
        time.sleep(30)
        enter_position(symbol, side)
    elif not POSITION:
        enter_position(symbol, side)