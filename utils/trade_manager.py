import time
import datetime
import os
import math
from binance.client import Client
from dotenv import load_dotenv
from utils.logger import Logger
from utils.monitor import update_monitor_data

# 환경 변수 로딩
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# Binance 클라이언트 및 로거 설정
client = Client(API_KEY, API_SECRET)
logger = Logger()

# 기본 설정값
LEVERAGE = 5
INITIAL_CAPITAL = 100.0

# 상태 저장용 딕셔너리
capital_map = {}
position_states = {}
last_switch_time = {}
symbol_precision_map = {}

# 심볼의 최소 주문 수량 단위 및 가격 소수점 단위 반환
def get_symbol_precision(symbol):
    info = client.futures_exchange_info()
    for s in info['symbols']:
        if s['symbol'] == symbol:
            qty_step = 0.001
            price_tick = 0.01
            for f in s['filters']:
                if f['filterType'] == 'LOT_SIZE':
                    qty_step = float(f['stepSize'])
                elif f['filterType'] == 'PRICE_FILTER':
                    price_tick = float(f['tickSize'])
            symbol_precision_map[symbol] = (qty_step, price_tick)
            return qty_step, price_tick
    return 0.001, 0.01

def adjust_quantity(symbol, quantity):
    qty_step, _ = symbol_precision_map.get(symbol, get_symbol_precision(symbol))
    return math.floor(quantity / qty_step) * qty_step

def round_price(symbol, price):
    _, tick_size = symbol_precision_map.get(symbol, get_symbol_precision(symbol))
    precision = abs(int(round(-math.log10(tick_size), 0)))
    return round(price, precision)

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

def cancel_all_open_orders(symbol):
    try:
        client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception as e:
        logger.log(f"{symbol} 오픈 주문 취소 실패: {e}")

def place_tp_sl_orders(symbol, entry_price, quantity, side):
    opposite = 'SELL' if side == 'BUY' else 'BUY'
    qty_step, _ = get_symbol_precision(symbol)
    tp1_price = round_price(symbol, entry_price * 1.005)
    tp2_price = round_price(symbol, entry_price * 1.011)
    sl_price = round_price(symbol, entry_price * 0.995)

    tp1_qty = adjust_quantity(symbol, quantity * 0.3)
    tp2_qty = adjust_quantity(symbol, quantity * 0.35)

    try:
        client.futures_create_order(
            symbol=symbol,
            side=opposite,
            type='TAKE_PROFIT_MARKET',
            stopPrice=tp1_price,
            quantity=tp1_qty,
            reduceOnly=True,
            timeInForce='GTC',
            newClientOrderId=f"{symbol}_TP1"
        )
        client.futures_create_order(
            symbol=symbol,
            side=opposite,
            type='TAKE_PROFIT_MARKET',
            stopPrice=tp2_price,
            quantity=tp2_qty,
            reduceOnly=True,
            timeInForce='GTC',
            newClientOrderId=f"{symbol}_TP2"
        )
        client.futures_create_order(
            symbol=symbol,
            side=opposite,
            type='STOP_MARKET',
            stopPrice=sl_price,
            quantity=quantity,
            reduceOnly=True,
            timeInForce='GTC',
            newClientOrderId=f"{symbol}_SL"
        )
        logger.log(f"{symbol} TP/SL 주문 설정 완료")
    except Exception as e:
        logger.log(f"{symbol} TP/SL 주문 설정 실패: {e}")

def monitor_tp1_fill(symbol, entry_price, side):
    opposite = 'SELL' if side == 'BUY' else 'BUY'
    sl_new_price = round_price(symbol, entry_price * 1.001)
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        tp1_filled = all(o.get("clientOrderId") != f"{symbol}_TP1" for o in orders)
        if tp1_filled:
            client.futures_cancel_order(symbol=symbol, origClientOrderId=f"{symbol}_SL")
            logger.log(f"{symbol} TP1 체결 감지 → 기존 SL 취소 후 상향 SL 설정")
            current_qty = get_current_position_quantity(symbol)
            client.futures_create_order(
                symbol=symbol,
                side=opposite,
                type='STOP_MARKET',
                stopPrice=sl_new_price,
                quantity=adjust_quantity(symbol, current_qty),
                reduceOnly=True,
                timeInForce='GTC',
                newClientOrderId=f"{symbol}_SL_ADJ"
            )
    except Exception as e:
        logger.log(f"{symbol} TP1 SL 수정 실패: {e}")

def enter_position(symbol, side):
    state = get_position_state(symbol)
    if state["side"]:
        logger.log(f"{symbol} 포지션 이미 존재")
        return

    set_leverage(symbol, LEVERAGE)
    cancel_all_open_orders(symbol)

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
        place_tp_sl_orders(symbol, price, quantity, side)
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
        cancel_all_open_orders(symbol)
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
        try:
            cancel_all_open_orders(symbol)
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
    else:
        monitor_tp1_fill(symbol, state["entry_price"], side)