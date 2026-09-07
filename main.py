import os, time, threading, telebot, json, websocket
from collections import deque
from flask import Flask

TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN: raise Exception("TELEGRAM_TOKEN алга!")
bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

SYMBOLS = ["BOOM1000","BOOM500","BOOM600","BOOM900","CRASH1000","CRASH500","CRASH900"]
ticks_since = {s:0 for s in SYMBOLS}
alert_sent = {s:False for s in SYMBOLS}
price_hist = {s: deque(maxlen=200) for s in SYMBOLS}
last_prices = {}
subscribers = set()
DRIFT_WINDOW = 50

def is_down(sym):
    h=list(price_hist[sym])
    if len(h)<50: return False
    return (h[-1]-h[0])/h[0]*100 <= -0.3
def is_up(sym):
    h=list(price_hist[sym])
    if len(h)<50: return False
    return (h[-1]-h[0])/h[0]*100 >= 0.3

def on_message(ws,message):
    try:
        data=json.loads(message)
        if 'tick' in data:
            sym=data['tick']['symbol']
            if sym not in SYMBOLS: return
            price=float(data['tick']['quote'])
            price_hist[sym].append(price)
            ticks_since[sym]+=1
            t=ticks_since[sym]
            in_win = False
            if sym in ["BOOM500","BOOM600"]: in_win=400<=t<=480
            elif sym in ["BOOM1000","BOOM900"]: in_win=850<=t<=950
            else: in_win=400<=t<=480
            if in_win and not alert_sent[sym]:
                ok=is_down(sym) if "BOOM" in sym else is_up(sym)
                if ok:
                    msg=f"⚠️ PREDICTIVE MANIAC\n{sym}\nTicks:{t}\nPrice:{price:.2f}\nDrift 0.3% ✅\n1 минутын дараа Spike!"
                    for cid in list(subscribers):
                        try: bot.send_message(cid,msg)
                        except: pass
                    alert_sent[sym]=True
            last_prices[sym]=price
            if ticks_since[sym]>1000:
                ticks_since[sym]=0
                alert_sent[sym]=False
    except: pass

def on_open(ws):
    for s in SYMBOLS: ws.send(json.dumps({"ticks":s,"subscribe":1}))

def run_deriv():
    while True:
        try:
            ws=websocket.WebSocketApp("wss://ws.derivws.com/websockets/v3?app_id=1089",on_message=on_message,on_open=on_open)
            ws.run_forever(ping_interval=30)
        except Exception as e:
            print(f"Deriv error {e}")
            time.sleep(5)

@app.route('/')
def home(): return "MANIAC 7 INDICES LIVE - SELF HEALING"

@bot.message_handler(commands=['start'])
def start_cmd(m):
    subscribers.add(m.chat.id)
    bot.reply_to(m,f"🔥 МАНГАС СЭРЛЭЭ! 🔥\n7 Индекс хянаж байна: {', '.join(SYMBOLS)}\n/test -> шууд тест сигнал\n/status -> одоогийн ticks\nАвтомат сигнал: 400-480/850-950 + 0.3%")

@bot.message_handler(commands=['status'])
def status_cmd(m):
    txt="📊 LIVE STATUS 🟢\n"
    for s in SYMBOLS:
        txt+=f"{s}: {last_prices.get(s,'...')} | tick:{ticks_since[s]} | hist:{len(price_hist[s])}\n"
    bot.reply_to(m,txt)

@bot.message_handler(commands=['test'])
def test_cmd(m):
    subscribers.add(m.chat.id)
    bot.reply_to(m,"⚠️ PREDICTIVE MANIAC\nBOOM1000\nTicks:455\nPrice:1234.56\nDrift 0.3% ✅\nTEST SIGNAL - Bot ажиллаж байна! ✅")

threading.Thread(target=run_deriv,daemon=True).start()

if __name__=='__main__':
    threading.Thread(target=lambda: bot.infinity_polling(skip_pending=True),daemon=True).start()
    app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))
