import os
import time
from binance.client import Client
from dotenv import load_dotenv
from utils.logger import Logger

# 환경 변수 로드
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# Binance 클라이언트 초기화
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
    """레버리지 설정"""
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logger.log(f"{symbol} 레버리지 {leverage}배 설정 완료")
    except Exception as e:
        logger.log(f"레버리지 설정 오류: {e}")

def enter_position(symbol, side):
    """포지션 진입"""
    global POSITION, entry_price, tp1_done, tp2_done, stop_loss_shifted
    if POSITION:
        logger.log("이미 포지션 보유 중")
        return

    set_leverage(symbol, LEVERAGE)

    # 초기 증거금의 98%만 사용
    amount_to_use = capital * 0.98

    # USDT 기준 금액으로 주문 → 수량 계산
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    quote_qty = round(amount_to_use * LEVERAGE, 2)  # 레버리지 포함 전체 주문 금액

    try:
        # quoteOrderQty 방식으로 주문 (시장가)
        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quoteOrderQty=quote_qty
        )
        POSITION = side
        entry_price = price
        tp1_done = False
        tp2_done = False
        stop_loss_shifted = False
        logger.log(f"진입: {side}, 진입금액: {quote_qty} USDT, 레버리지: {LEVERAGE}배")
    except Exception as e:
        logger.log(f"진입 오류: {e}")
        
def close_position(symbol):
    """포지션 전량 청산"""
    global POSITION, capital
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
        # 복리 방식으로 자본 업데이트
        capital = (quantity * price) / LEVERAGE
        logger.log(f"청산 완료: {side}, 현재 자본: {capital}")
    except Exception as e:
        logger.log(f"청산 오류: {e}")
    finally:
        reset_state()

def get_current_position_quantity(symbol):
    """현재 포지션 수량 반환"""
    positions = client.futures_position_information(symbol=symbol)
    for p in positions:
        if float(p['positionAmt']) != 0:
            return abs(float(p['positionAmt']))
    return 0

def reset_state():
    """포지션 상태 초기화"""
    global POSITION, entry_price, tp1_done, tp2_done, stop_loss_shifted
    POSITION = None
    entry_price = 0.0
    tp1_done = False
    tp2_done = False
    stop_loss_shifted = False

def check_exit_conditions(symbol):
    """손절/익절 조건 확인"""
    global tp1_done, tp2_done, stop_loss_shifted

    if not POSITION:
        return

    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    change = ((price - entry_price) / entry_price) * 100
    if POSITION == 'SELL':
        change = -change

    # 손절 -0.5%
    if change <= -0.5:
        logger.log(f"손절: {change:.2f}%")
        close_position(symbol)
        return

    # 1차 익절 +0.5%, 30% 청산
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

    # 익절 후 +0.1% 미만 회귀 시 전량 청산
    if tp1_done and stop_loss_shifted and change < 0.1:
        logger.log("익절 후 +0.1% 미만 회귀, 전량 청산")
        close_position(symbol)
        return

    # 2차 익절 +1.1%, 50% 청산
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
    """외부 신호 처리 → 포지션 진입/스위칭"""
    global POSITION
    if POSITION and POSITION != side:
        logger.log("역신호 → 포지션 청산 후 스위칭")
        close_position(symbol)
        time.sleep(30)
        enter_position(symbol, side)
    elif not POSITION:
        enter_position(symbol, side)