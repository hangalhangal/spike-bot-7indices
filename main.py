import os, time, threading, telebot, json, websocket
from collections import deque
from flask import Flask

TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

SYMBOLS = ["BOOM1000","BOOM500","BOOM600","BOOM900","CRASH1000","CRASH500","CRASH900"]
ticks_since = {s:0 for s in SYMBOLS}
alert_sent = {s:False for s in SYMBOLS}
price_hist = {s: deque(maxlen=200) for s in SYMBOLS}
last_prices = {}
subscribers = set()
DRIFT_WINDOW = 50
BOOM_500_RANGE = (400,480)
BOOM_900_RANGE = (850,950)
CRASH_RANGE = (400,480)

def is_spike(cur,prev,sym):
    if prev==0: return False
    d=cur-prev
    return d>(prev*0.15) if "BOOM" in sym else d<-(prev*0.15)
def is_down(sym):
    h=list(price_hist[sym])[-DRIFT_WINDOW:]
    if len(h)<DRIFT_WINDOW: return False
    return (h[-1]-h[0])/h[0]*100 <= -0.3
def is_up(sym):
    h=list(price_hist[sym])[-DRIFT_WINDOW:]
    if len(h)<DRIFT_WINDOW: return False
    return (h[-1]-h[0])/h[0]*100 >= 0.3

def on_message(ws,message):
    data=json.loads(message)
    if 'tick' in data:
        sym=data['tick']['symbol']; price=float(data['tick']['quote'])
        prev=last_prices.get(sym,0)
        price_hist[sym].append(price)
        if is_spike(price,prev,sym):
            ticks_since[sym]=0; alert_sent[sym]=False; last_prices[sym]=price; return
        ticks_since[sym]+=1; t=ticks_since[sym]
        in_win=False
        if sym in ["BOOM500","BOOM600"]: in_win=BOOM_500_RANGE[0]<=t<=BOOM_500_RANGE[1]
        elif sym in ["BOOM1000","BOOM900"]: in_win=BOOM_900_RANGE[0]<=t<=BOOM_900_RANGE[1]
        else: in_win=CRASH_RANGE[0]<=t<=CRASH_RANGE[1]
        if in_win and not alert_sent[sym]:
            ok=is_down(sym) if "BOOM" in sym else is_up(sym)
            if ok:
                msg=f"⚠️ PREDICTIVE MANIAC\n{sym}\nTicks:{t}\nPrice:{price:.2f}\nDrift 0.3% ✅\nSpike in ~1min!"
                for cid in list(subscribers):
                    try: bot.send_message(cid,msg)
                    except: pass
                alert_sent[sym]=True
        last_prices[sym]=price

def on_open(ws):
    for s in SYMBOLS: ws.send(json.dumps({"ticks":s,"subscribe":1}))

def run_deriv():
    while True:
        try:
            ws=websocket.WebSocketApp("wss://ws.derivws.com/websockets/v3?app_id=1089",on_message=on_message,on_open=on_open)
            ws.run_forever(ping_interval=30)
        except: time.sleep(5)

@app.route('/')
def home(): return "MANIAC LIVE - 7 INDICES SELF HEALING"

@bot.message_handler(commands=['start'])
def start_cmd(m):
    subscribers.add(m.chat.id)
    bot.reply_to(m,f"🔥 МАНГАС СЭРЛЭЭ! 🔥\n7 Индекс: {','.join(SYMBOLS)}\n400-480/850-950 + 0.3% + 1мин ӨМНӨ\n/self-healing ON - унтуулахгүй!")

@bot.message_handler(commands=['status'])
def status_cmd(m):
    txt="📊 LIVE 🟢 SELF-HEALING ON\n"
    for s in SYMBOLS: txt+=f"{s}: {last_prices.get(s,'...')} | {ticks_since[s]}\n"
    bot.reply_to(m,txt)

@bot.message_handler(commands=['help'])
def help_cmd(m): bot.reply_to(m,"/start /status /help")

threading.Thread(target=run_deriv,daemon=True).start()
threading.Thread(target=lambda: bot.infinity_polling(),daemon=True).start()

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))
