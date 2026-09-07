import os, websocket, json, time, requests
from collections import defaultdict, deque
from flask import Flask
import threading
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
@app.route('/')
def home():
    return "Bot Live 0.3% FINAL"

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
THRESHOLD = 0.3
INDICES = ["BOOM_1000","BOOM_500","BOOM_600","BOOM_900","CRASH_1000","CRASH_500","CRASH_900"]
ticks = defaultdict(lambda: deque(maxlen=10))
last_alert = {}
alert_count = 0

def tg(msg, cid=None):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": cid or CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def ub_time():
    return (datetime.now(timezone.utc)+timedelta(hours=8)).strftime("%H:%M:%S UB")

def on_message(ws, message):
    global alert_count
    try:
        d=json.loads(message)
        if 'tick' not in d: return
        sym=d['tick']['symbol']; price=float(d['tick']['quote'])
        if not ticks[sym]: ticks[sym].append(price); return
        old=ticks[sym][-1]; ticks[sym].append(price)
        change=abs((price-old)/old*100) if old!=0 else 0
        if change>=THRESHOLD:
            now=time.time()
            if sym in last_alert and now-last_alert[sym]<60: return
            last_alert[sym]=now; alert_count+=1
            tg(f"🚨 *SPIKE {THRESHOLD}%* {sym}\nPrice:{price}\nChange:{change:.4f}%\nTime:{ub_time()} #{alert_count}")
    except: pass

def on_open(ws):
    tg(f"✅ *Bot Started FINAL 0.3%*\nTime:{ub_time()}\n7 indices LIVE")
    for s in INDICES: ws.send(json.dumps({"ticks": s}))

def run_bot():
    while True:
        try:
            ws=websocket.WebSocketApp("wss://ws.binaryws.com/websockets/v3?app_id=1089",on_message=on_message,on_open=on_open)
            ws.run_forever(ping_interval=30)
        except: time.sleep(10)

def poller():
    offset=0
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=10",timeout=15).json()
            if r.get("ok"):
                for u in r.get("result",[]):
                    offset=u["update_id"]+1
                    cid=u.get("message",{}).get("chat",{}).get("id")
                    txt=u.get("message",{}).get("text","")
                    if txt=="/start":
                        tg(f"✅ LIVE {ub_time()}\nThr:{THRESHOLD}% Alerts:{alert_count}\nIndices: BOOM 1000/500/600/900 CRASH 1000/500/900",cid)
                    elif txt=="/status":
                        tg(f"📊 STATUS {ub_time()}\nThr:{THRESHOLD}% Count:{alert_count}\nMonitoring 7 indices OK",cid)
                    elif txt=="/help":
                        tg("Commands:\n/start - live\n/status - stats\n/help - help",cid)
        except: time.sleep(3)

threading.Thread(target=run_bot,daemon=True).start()
threading.Thread(target=poller,daemon=True).start()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
