import os
import time
import httpx
import pandas as pd
import yfinance as yf
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
SYMBOL    = "NQ=F"       # NASDAQ Futures (หรือ ^NDX / QQQ)
INTERVAL  = "5m"         # M5
PERIOD    = "2d"         # ดึงย้อนหลัง 2 วัน
CHECK_EVERY = 60         # เช็คทุก 60 วินาที

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# ─────────────────────────────────────────────
# INDICATOR CALCULATIONS
# ─────────────────────────────────────────────
def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def rsi(series, length=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(length).mean()
    loss  = (-delta.clip(upper=0)).rolling(length).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    macd_line   = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line

def atr(df, length=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(length).mean()

# ─────────────────────────────────────────────
# SESSION FILTER — NY Power Hour (UTC)
# 13:30–14:30 UTC
# ─────────────────────────────────────────────
def in_session(dt):
    h, m = dt.hour, dt.minute
    in_power = (h == 13 and m >= 30) or (h == 14 and m < 30)
    in_ny    = (h > 13 or (h == 13 and m >= 30)) and h < 20
    return in_power, in_ny

# ─────────────────────────────────────────────
# FETCH + SIGNAL
# ─────────────────────────────────────────────
def get_signal():
    df = yf.download(SYMBOL, interval=INTERVAL, period=PERIOD, progress=False)
    if df.empty or len(df) < 210:
        return None

    close = df["Close"]

    # Trend
    e20  = ema(close, 20)
    e50  = ema(close, 50)
    e200 = ema(close, 200)
    bull_trend = (e20.iloc[-1] > e50.iloc[-1]) and (e50.iloc[-1] > e200.iloc[-1])
    bear_trend = (e20.iloc[-1] < e50.iloc[-1]) and (e50.iloc[-1] < e200.iloc[-1])

    # RSI
    rsi_val  = rsi(close).iloc[-1]
    rsi_bull = 50 < rsi_val < 70
    rsi_bear = 30 < rsi_val < 50

    # MACD
    macd_line, signal_line = macd(close)
    macd_bull = (macd_line.iloc[-1] > signal_line.iloc[-1]) and (macd_line.iloc[-1] > 0)
    macd_bear = (macd_line.iloc[-1] < signal_line.iloc[-1]) and (macd_line.iloc[-1] < 0)

    # ATR
    atr_val = atr(df).iloc[-1]

    # Session
    last_time = df.index[-1].to_pydatetime()
    power, ny = in_session(last_time)
    session_ok = power or ny

    # Price
    price = close.iloc[-1]
    tp1   = round(price + atr_val * 2.0, 2)
    tp2   = round(price + atr_val * 3.0, 2)
    sl    = round(price - atr_val * 1.0, 2)

    if bull_trend and rsi_bull and macd_bull and session_ok:
        return {
            "signal": "BUY",
            "price":  round(price, 2),
            "tp1":    tp1,
            "tp2":    tp2,
            "sl":     sl,
            "rsi":    round(rsi_val, 1),
            "session": "⚡ POWER" if power else "🟢 NY",
            "time":   last_time.strftime("%Y-%m-%d %H:%M UTC")
        }
    elif bear_trend and rsi_bear and macd_bear and session_ok:
        sel_tp1 = round(price - atr_val * 2.0, 2)
        sel_tp2 = round(price - atr_val * 3.0, 2)
        sel_sl  = round(price + atr_val * 1.0, 2)
        return {
            "signal": "SELL",
            "price":  round(price, 2),
            "tp1":    sel_tp1,
            "tp2":    sel_tp2,
            "sl":     sel_sl,
            "rsi":    round(rsi_val, 1),
            "session": "⚡ POWER" if power else "🟢 NY",
            "time":   last_time.strftime("%Y-%m-%d %H:%M UTC")
        }
    return None

# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(data):
    arrow  = "🟢" if data["signal"] == "BUY" else "🔴"
    dir_lb = "BUY  ▲" if data["signal"] == "BUY" else "SELL ▼"
    msg = (
        f"⚡ *NASDAQ QUANTUM SIGNAL*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{arrow} *{dir_lb}* — `NQ` (M5)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Price  : `{data['price']}`\n"
        f"🎯 TP1   : `{data['tp1']}`\n"
        f"🎯 TP2   : `{data['tp2']}`\n"
        f"🛑 SL    : `{data['sl']}`\n"
        f"📊 RSI   : `{data['rsi']}`\n"
        f"🕐 Session: {data['session']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 `{data['time']}`"
    )
    with httpx.Client() as client:
        client.post(TELEGRAM_URL, json={
            "chat_id":    CHAT_ID,
            "text":       msg,
            "parse_mode": "Markdown"
        })
    print(f"[{datetime.utcnow()}] Sent {data['signal']} signal")

# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
last_signal = None

print("NASDAQ Quantum Alert — Python Engine started")

while True:
    try:
        result = get_signal()
        if result:
            # ป้องกัน spam — ส่งซ้ำได้ก็ต่อเมื่อ signal เปลี่ยน
            sig_key = f"{result['signal']}_{result['time']}"
            if sig_key != last_signal:
                send_telegram(result)
                last_signal = sig_key
        else:
            print(f"[{datetime.utcnow()}] No signal")
    except Exception as e:
        print(f"[{datetime.utcnow()}] Error: {e}")

    time.sleep(CHECK_EVERY)
