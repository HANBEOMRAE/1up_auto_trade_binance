# utils/monitor.py

import os
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)
monitor_data = {}

def update_monitor_data(symbol):
    positions = client.futures_position_information(symbol=symbol)
    for p in positions:
        position_amt = float(p['positionAmt'])
        entry_price = float(p['entryPrice'])
        if position_amt != 0:
            mark_price = float(client.futures_mark_price(symbol=symbol)['markPrice'])
            pnl_pct = ((mark_price - entry_price) / entry_price) * 100
            if position_amt < 0:
                pnl_pct = -pnl_pct
            monitor_data[symbol] = {
                "수량": position_amt,
                "진입가": entry_price,
                "시장가": mark_price,
                "수익률": round(pnl_pct, 2)
            }

def get_monitor_data():
    return monitor_data