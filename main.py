import os
import websocket
import json
import time
import requests
from collections import deque
from flask import Flask
import threading

# ===== RENDER PORT BINDING - ЭНЭ БАЙХ ЁСТОЙ, УСТГАЖ БОЛОХГҮЙ =====
app = Flask(__name__)
@app.route('/')
def home():
    return "7 Indices Bot Running - Boom Crash Monitoring Active"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()
# ===== PORT BINDING DUUSAV =====

# ===== ТАНЫ 7 ИНДЕКС - ӨӨРЧЛӨӨГҮЙ =====
INDICES = {
    "BOOM1000": "BOOM_1000",
    "BOOM500": "BOOM_500",
    "BOOM600": "BOOM_600",
    "BOOM900": "BOOM_900",
    "CRASH1000": "CRASH_1000",
    "CRASH500": "CRASH_500",
    "CRASH900": "CRASH_900"
}

# ===== TELEGRAM - RENDER ENV-EES УНШИНА =====
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ===== ТАНЫ REDLINE + 1 MIN SPIKE LOGIC - ӨӨРЧЛӨӨГҮЙ =====
ticks = {k: deque(maxlen=60) for k in INDICES.keys()}

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram Token/Chat ID not set")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        print(f"Sent: {msg[:50]}")
    except Exception as e:
        print(f"Telegram Error: {e}")

def on_message(ws, message):
    try:
        data = json.loads(message)
        if 'tick' not in data:
            return

        tick = data['tick']
        symbol = tick['symbol']
        price = float(tick['quote'])

        # Ямар индекс вэ гэдгийг ол
        index_name = None
        for name, sym in INDICES.items():
            if sym == symbol:
                index_name = name
                break

        if not index_name:
            return

        ticks[index_name].append(price)

        # ===== REDLINE + 1 MIN SPIKE ШАЛГАХ =====
        if len(ticks[index_name]) >= 60:
            old_price = ticks[index_name][0]
            new_price = ticks[index_name][-1]
            change_pct = abs(new_price - old_price) / old_price * 100 if old_price!= 0 else 0

            # Хэрэв 1 минутын дотор их өөрчлөлт байвал - SPIKE!
            if change_pct > 0.3: # Таны анхны дүрэм
                msg = f"🚨 *SPIKE ALERT* 🚨\n\n📊 Index: {index_name}\n💰 Price: {price}\n📈 1Min Change: {change_pct:.4f}%\n⏰ Time: {time.strftime('%H:%M:%S')}\n\n🔴 Redline Break!"
                send_telegram(msg)
                ticks[index_name].clear() # Дахин давтагдахаас сэргийлнэ

    except Exception as e:
        print(f"Message Error: {e}")

def on_open(ws):
    print("Connected - 7 Indices Monitoring Started")
    send_telegram("✅ *7 Indices Bot Started*\nMonitoring: BOOM 1000/500/600/900 & CRASH 1000/500/900")
    for symbol in INDICES.values():
        ws.send(json.dumps({"ticks": symbol}))

def on_error(ws, error):
    print(f"WebSocket Error: {error}")

def on_close(ws, close_status_code, close_msg):
    print(f"Closed: {close_status_code} - {close_msg}")
    time.sleep(5)

# ===== MAIN LOOP =====
if __name__ == "__main__":
    while True:
        try:
            ws = websocket.WebSocketApp("wss://ws.binaryws.com/websockets/v3?app_id=1089",
                                      on_message=on_message,
                                      on_open=on_open,
                                      on_error=on_error,
                                      on_close=on_close)
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"Main Loop Error: {e}")
            time.sleep(10)
