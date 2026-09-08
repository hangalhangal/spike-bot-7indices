import os
import json
import asyncio
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
import websockets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

INDICES = {
    "BOOM1000": {"name": "Boom 1000 Index", "symbol": "BOOM1000", "type": "BOOM"},
    "CRASH1000": {"name": "Crash 1000 Index", "symbol": "CRASH1000", "type": "CRASH"},
    "BOOM500": {"name": "Boom 500 Index", "symbol": "BOOM500", "type": "BOOM"},
    "CRASH500": {"name": "Crash 500 Index", "symbol": "CRASH500", "type": "CRASH"},
    "BOOM600": {"name": "Boom 600 Index", "symbol": "BOOM600", "type": "BOOM"},
    "CRASH900": {"name": "Crash 900 Index", "symbol": "CRASH900", "type": "CRASH"},
    "BOOM900": {"name": "Boom 900 Index", "symbol": "BOOM900", "type": "BOOM"},
}

active = set()
ticks_data = {k: [] for k in INDICES}
drift_data = {k: 0.0 for k in INDICES}
drift_threshold = {k: 0.3 for k in INDICES}
learn_stats = {k: {"ok":0, "fail":0} for k in INDICES}
chat_ids = set()

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"AI MANIAC LIVE")
        def log_message(self, *args): return
    httpd = HTTPServer(("0.0.0.0", port), Handler)
    httpd.serve_forever()
threading.Thread(target=keep_alive, daemon=True).start()

async def deriv_ws(symbol, key, app):
    uri = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"ticks_history": symbol, "count": 500, "end": "latest", "style": "ticks"}))
        await ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))
        while True:
            if key not in active: return
            msg = json.loads(await ws.recv())
            price = None
            if "history" in msg:
                try:
                    ticks_data[key] = [float(p) for p in msg["history"]["prices"]]
                    if ticks_data[key]:
                        price = ticks_data[key][-1]
                        if len(ticks_data[key]) >= 200:
                            old = ticks_data[key][-200]
                            drift_data[key] = ((price-old)/old*100) if old!=0 else 0
                except: pass
            if "tick" in msg:
                price = float(msg["tick"]["quote"])
                ticks_data[key].append(price)
                if len(ticks_data[key]) > 500: ticks_data[key].pop(0)

            if price is not None and len(ticks_data[key]) >= 200:
                old = ticks_data[key][-200]
                drift = ((price - old) / old * 100) if old!=0 else 0
                drift_data[key] = drift
                if abs(drift) >= drift_threshold[key]:
                    info = INDICES[key]
                    action = "BUY NOW 🟢\nДээшээ ХАДАХ гэж байна! 📈" if info["type"]=="BOOM" else "SELL NOW 🔴\nДоошоо УНАХ гэж байна! 📉"
                    text = f"⚠️ PREDICTIVE MANIAC\n{info['symbol']} - {action}\nTicks:{len(ticks_data[key])} Price:{price}\nDrift {drift:.3f}% Bosgo {drift_threshold[key]:.2f}% ✅\n🧠 AI МАНГАС!"
                    for cid in list(chat_ids):
                        try: await app.bot.send_message(chat_id=cid, text=text)
                        except: pass

                    # >>> ЖИНХЭНЭ AI ӨӨРӨӨ СУРАЛЦАХ ХЭСЭГ - ТАНЫ ХҮССЭН <<<
                    pred_price = price
                    await asyncio.sleep(30)
                    if key in ticks_data and len(ticks_data[key])>0:
                        after_price = ticks_data[key][-1]
                        real_move = abs((after_price - pred_price)/pred_price*100)
                        if real_move < 0.10: # Спайк болоогүй - Алдаа
                            learn_stats[key]["fail"]+=1
                            drift_threshold[key] = min(0.8, drift_threshold[key]+0.05)
                            for cid in list(chat_ids):
                                try: await app.bot.send_message(chat_id=cid, text=f"🧠 AI СУРЛАА {info['symbol']} FAIL! Bosgo {drift_threshold[key]:.2f}% болгож чангаллаа!")
                                except: pass
                        else: # Спайк болсон - Зөв
                            learn_stats[key]["ok"]+=1
                            drift_threshold[key] = max(0.20, drift_threshold[key]-0.02)
                    else:
                        await asyncio.sleep(30)

async def start_ws(symbol, key, app):
    while key in active:
        try: await deriv_ws(symbol, key, app)
        except Exception as e: logging.error(f"{key} {e}"); await asyncio.sleep(5)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    buttons = [[InlineKeyboardButton(f"{'✅' if k in active else '❌'} {v['name']}", callback_data=k)] for k,v in INDICES.items()]
    await update.message.reply_text("🧠 AI МАНГАС - ЗӨВХӨН 7 ИНДЕКС\nDrift 0.3% - 500 ticks - ӨӨРӨӨ СУРАЛЦАХ AI", reply_markup=InlineKeyboardMarkup(buttons))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); key = q.data; chat_ids.add(q.message.chat.id)
    if key in active: active.remove(key); await q.message.reply_text(f"❌ {INDICES[key]['name']} унтраалаа!")
    else: active.add(key); await q.message.reply_text(f"✅ {INDICES[key]['name']} идэвхжлээ!\n🧠 AI сурч эхэллээ Drift 0.3%"); asyncio.create_task(start_ws(INDICES[key]['symbol'], key, context.application))
    buttons = [[InlineKeyboardButton(f"{'✅' if k in active else '❌'} {v['name']}", callback_data=k)] for k,v in INDICES.items()]
    try: await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
    except: pass

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    msg="📊 BOT STATUS - LIVE AI LEARNING\n\n"
    for k,v in INDICES.items():
        s=learn_stats[k]; msg+=f"{'✅' if k in active else '❌'} {v['name']}: {len(ticks_data.get(k,[]))}/500 | Drift {drift_data.get(k,0.0):.3f}% | Bosgo {drift_threshold[k]:.2f}% | OK:{s['ok']} FAIL:{s['fail']}\n"
    msg+=f"\n🟢 Active: {len(active)}/7\n🧠 AI МАНГАС өөрөө хөгжиж байна!"
    await update.message.reply_text(msg)

async def test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    await update.message.reply_text("⚠️ PREDICTIVE MANIAC\nBOOM1000 - BUY NOW 🟢\nTicks:500 Drift 0.3% ✅\nAI МАНГАС TEST OK!")

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("test", test_cmd))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CallbackQueryHandler(button))
if __name__ == "__main__": app.run_polling()
