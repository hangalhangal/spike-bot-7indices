import os, websocket, json, time, requests
from collections import defaultdict, deque
from flask import Flask
import threading
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
@app.route('/')
def home():
    return "Bot Live - 0.3% 7 Indices"

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
THRESHOLD = 0.3

def tg(msg):
    try:
        if not TOKEN or not CHAT_ID:
            print("No token/chat_id")
            return
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        r = requests.post(url, json=data, timeout=10)
        print(f"TG sent: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"TG error: {e}")

def ub_time():
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    return now.strftime("%H:%M:%S UB")

INDICES = ["BOOM_1000","BOOM_500","BOOM_600","BOOM_900","CRASH_1000","CRASH_500","CRASH_900"]
ticks = defaultdict(lambda: deque(maxlen=10))
last_alert = {}
alert_count = 0

def on_message(ws, message):
    global alert_count
    try:
        data = json.loads(message)
        if 'tick' not in data: return
        sym = data['tick']['symbol']
        price = float(data['tick']['quote'])
        if not ticks[sym]:
            ticks[sym].append(price)
            return
        old = ticks[sym][-1]
        ticks[sym].append(price)
        change = abs((price - old) / old * 100) if old!= 0 else 0
        if change >= THRESHOLD:
            now = time.time()
            if sym in last_alert and now - last_alert[sym] < 60:
                return
            last_alert[sym] = now
            alert_count += 1
            msg = f"🚨 *SPIKE ALERT {THRESHOLD}%*\nIndex: {sym}\nPrice: {price}\nChange: {change:.4f}%\nTime: {ub_time()}\nCount: #{alert_count}"
            tg(msg)
    except Exception as e:
        print(f"on_message error: {e}")

def on_open(ws):
    print("WS Connected")
    tg(f"✅ *Bot Restarted - LIVE MODE*\nThreshold: {THRESHOLD}%\nTime: {ub_time()}\n7 markets monitoring!")
    for s in INDICES:
        ws.send(json.dumps({"ticks": s}))

def on_error(ws, err): print(f"WS Error: {err}")
def on_close(ws, a, b):
    print("WS Closed")
    time.sleep(5)

def run_bot():
    while True:
        try:
            ws = websocket.WebSocketApp(
                "wss://ws.binaryws.com/websockets/v3?app_id=1089",
                on_message=on_message, on_open=on_open,
                on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"run_bot error: {e}")
            time.sleep(10)

def poller():
    offset = 0
    while True:
        try:
            if not TOKEN:
                time.sleep(5)
                continue
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=30"
            r = requests.get(url, timeout=35).json()
            if r.get("ok"):
                for upd in r.get("result", []):
                    offset = upd["update_id"]+1
                    txt = upd.get("message", {}).get("text", "")
                    if txt == "/start":
                        tg(f"Bot Live {ub_time()} | Thr {THRESHOLD}% | Alerts {alert_count} | 7 indices")
                    if txt == "/status":
                        tg(f"📊 Status {ub_time()}\nThr {THRESHOLD}%\nAlerts today {alert_count}\nIndices: {', '.join(INDICES)}")
                    if txt == "/help":
                        tg("Commands:\n/start - check live\n/status - stats\n/help - this help")
        except Exception as e:
            print(f"poller error: {e}")
            time.sleep(5)

# Start threads immediately - IMPORTANT for Gunicorn
print("Starting threads...")
threading.Thread(target=run_bot, daemon=True).start()
threading.Thread(target=poller, daemon=True).start()
print("Threads started")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
