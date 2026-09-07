import websocket
import json
import time
import requests
from collections import deque

# ===== ТАНЫ 7 ИНДЕКС - ӨӨРЧЛӨХГҮЙ =====
INDICES = {
    "BOOM1000": "BOOM_1000",
    "BOOM500": "BOOM_500",
    "BOOM600": "BOOM_600",
    "BOOM900": "BOOM_900",
    "CRASH1000": "CRASH_1000",
    "CRASH500": "CRASH_500",
    "CRASH900": "CRASH_900"
}

# ===== TELEGRAM - ЭНД ӨӨРИЙНХӨГ ХИЙНЭ =====
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

# Redline + 1 Min Spike Logic
ticks = {k: deque(maxlen=60) for k in INDICES.keys()}

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except:
        pass

def on_message(ws, message):
    data = json.loads(message)
    if 'tick' in data:
        symbol = data['tick']['symbol']
        price = float(data['tick']['quote'])
        # Find which index
        for name, deriv_symbol in INDICES.items():
            if deriv_symbol == symbol:
                ticks[name].append(price)
                if len(ticks[name]) == 60:
                    # 1 MIN SPIKE DETECTION - Redline Logic
                    first = ticks[name][0]
                    last = ticks[name][-1]
                    change = abs(last - first)
                    percent = (change / first) * 100 if first!= 0 else 0
                    # Spike threshold
                    if percent > 0.3: # Redline spike
                        msg = f"🚨 SPIKE ALERT!\n{name} - 1 MIN\nPrice: {first} -> {last}\nChange: {percent:.3f}%"
                        print(msg)
                        send_telegram(msg)
                break

def on_open(ws):
    print("Connected - 7 Indices Monitoring Started")
    for deriv_symbol in INDICES.values():
        ws.send(json.dumps({"ticks": deriv_symbol}))

# WebSocket
ws = websocket.WebSocketApp("wss://ws.binaryws.com/websockets/v3?app_id=1089",
                            on_message=on_message,
                            on_open=on_open)
ws.run_forever()
