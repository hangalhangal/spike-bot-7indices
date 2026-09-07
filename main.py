import os
import websocket
import json
import time
import requests
from collections import deque, defaultdict
from flask import Flask
import threading

# ===== RENDER PORT BINDING - БАЙХ ЁСТОЙ =====
app = Flask(__name__)

@app.route('/')
def home():
    return "7 Indices Bot Running - Boom Crash Monitoring Live"

# ===== TELEGRAM =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(msg):
    try:
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

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

ticks = defaultdict(lambda: deque(maxlen=10))

def on_message(ws, message):
    try:
        data = json.loads(message)
        if 'tick' in data:
            tick = data['tick']
            symbol = tick['symbol']
            price = float(tick['quote'])
            index_name = symbol
            
            old_price = ticks[index_name][-1] if ticks[index_name] else price
            ticks[index_name].append(price)
            
            if len(ticks[index_name]) < 2:
                return

            new_price = ticks[index_name][-1]
            change_pct = abs((new_price - old_price) / old_price * 100 if old_price != 0 else 0)

            # ТАНЫ АНХНЫ ДҮРЭМ - 0.3% SPIKE
            if change_pct > 0.3:
                msg = f"🚨 *SPIKE ALERT* 🚨\n\n📊 Index: {index_name}\n💰 Price: {price}\n📈 Change: {change_pct:.3f}%"
                print(msg)
                send_telegram(msg)
                ticks[index_name].clear()
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

def run_bot_forever():
    print("🤖 7 Indices Bot Thread Starting...")
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

# ===== ЗӨВХӨН ЭНЭ 2 МӨР Л ЗАСВАР - БУСАД БҮГД ХЭВЭЭРЭЭ =====
threading.Thread(target=run_bot_forever, daemon=True).start()
print("✅ Bot Thread Started - Fixed!")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
