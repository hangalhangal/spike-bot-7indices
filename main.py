import os, websocket, json, time, requests
from collections import deque, defaultdict
from flask import Flask
import threading
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
@app.route('/')
def home():
    return "7 Indices Bot - TEST MODE 0.05%"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ===== ТУРШИЛТЫН ДҮРЭМ =====
TEST_MODE = True # True бол 0.05%, False бол 0.3%
THRESHOLD = 0.05 if TEST_MODE else 0.3

def send_telegram(msg):
    try:
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

# UB цаг гаргах
def ub_time():
    utc_now = datetime.now(timezone.utc)
    ub_now = utc_now + timedelta(hours=8)
    return ub_now.strftime("%Y-%m-%d %H:%M:%S UB")

INDICES = ["BOOM_1000","BOOM_500","BOOM_600","BOOM_900","CRASH_1000","CRASH_500","CRASH_900"]
ticks = defaultdict(lambda: deque(maxlen=10))
last_spike_time = {}
alert_count = 0

def on_message(ws, message):
    global alert_count
    try:
        data = json.loads(message)
        if 'tick' in data:
            tick = data['tick']
            symbol = tick['symbol']
            price = float(tick['quote'])
            old_price = ticks[symbol][-1] if ticks[symbol] else price
            ticks[symbol].append(price)
            if len(ticks[symbol]) < 2: return
            new_price = ticks[symbol][-1]
            change_pct = abs((new_price - old_price) / old_price * 100 if old_price!= 0 else 0)

            if change_pct > THRESHOLD:
                # Давхардагдахаас сэргийлэх - 1 минутад 1 удаа
                now = time.time()
                if symbol in last_spike_time and now - last_spike_time[symbol] < 60:
                    return
                last_spike_time[symbol] = now
                alert_count += 1

                mode_text = "🧪 TEST 0.05%" if TEST_MODE else "🚨 LIVE 0.3%"
                msg = f"{mode_text}\n\n📊 Index: {symbol}\n💰 Price: {price}\n📈 Change: {change_pct:.4f}%\n🕐 Time: {ub_time()}\n🔢 Count: {alert_count}"
                print(msg)
                send_telegram(msg)
                ticks[symbol].clear()
    except Exception as e:
        print(f"Msg Error: {e}")

def on_open(ws):
    print(f"Connected - Threshold {THRESHOLD}% - {ub_time()}")
    send_telegram(f"✅ *Bot Restarted - TEST MODE*\nThreshold: {THRESHOLD}%\nTime: {ub_time()}\nMonitoring 7 indices - Spike alerts will come every few minutes!")
    for symbol in INDICES:
        ws.send(json.dumps({"ticks": symbol}))

def on_error(ws, error): print(f"WS Error: {error}")
def on_close(ws, c, m): print(f"Closed {c}"); time.sleep(5)

def run_bot_forever():
    while True:
        try:
            ws = websocket.WebSocketApp("wss://ws.binaryws.com/websockets/v3?app_id=1089",
                on_message=on_message, on_open=on_open, on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"Loop Error: {e}"); time.sleep(10)

# ===== TELEGRAM COMMAND HANDLER =====
def telegram_poller():
    offset = 0
    while True:
        try:
            if not TELEGRAM_TOKEN: time.sleep(10); continue
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=30"
            r = requests.get(url, timeout=35).json()
            if r.get("ok"):
                for upd in r.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message", {})
                    text = msg.get("text", "")
                    if text == "/start":
                        send_telegram(f"🤖 *Hangal Forex AI Bot*\n\n✅ Bot Live: {ub_time()}\nThreshold: {THRESHOLD}%\nMode: {'TEST' if TEST_MODE else 'LIVE'}\nAlerts: {alert_count}\n\nCommands:\n/status - status\n/help - help")
                    elif text == "/help":
                        send_telegram("📖 *Help*\n\nBot monitors BOOM 1000/500/600/900 & CRASH 1000/500/900\nSends alert when price jumps > threshold\n\n/status - see live status\n/start - start message")
                    elif text == "/status":
                        send_telegram(f"📊 *Status*\n\nTime: {ub_time()}\nThreshold: {THRESHOLD}%\nAlerts sent: {alert_count}\nMonitoring: 7 indices\nLast ticks: {len(ticks)} markets tracked")
        except Exception as e:
            print(f"Poller Error: {e}")
            time.sleep(5)

threading.Thread(target=run_bot_forever, daemon=True).start()
threading.Thread(target=telegram_poller, daemon=True).start()
print("✅ Both Threads Started!")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
