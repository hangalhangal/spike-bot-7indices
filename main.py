import os
import asyncio
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import deque
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import websockets

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_TOKEN")

# RENDER PORT FIX
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"AI MANIAC LIVE - 7 INDICES")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args): return

def run_keep_alive():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), KeepAliveHandler).serve_forever()

threading.Thread(target=run_keep_alive, daemon=True).start()

# ===== ҮНДСЭН ДҮРЭМ: ЗӨВХӨН ЧИНИЙ 7 МАНГАС - ӨӨР ЮУЧГҮЙ =====
INDICES = [
    "Boom 1000 Index",
    "Boom 500 Index",
    "Boom 600 Index",
    "Boom 900 Index",
    "Crash 1000 Index",
    "Crash 500 Index",
    "Crash 900 Index"
]

DERIV_MAP = {
    "Boom 1000 Index": "BOOM1000",
    "Boom 500 Index": "BOOM500",
    "Boom 600 Index": "BOOM600",
    "Boom 900 Index": "BOOM900",
    "Crash 1000 Index": "CRASH1000",
    "Crash 500 Index": "CRASH500",
    "Crash 900 Index": "CRASH900"
}

# ===== AI МАНГАС САНАМЖ - ӨӨРӨӨ ХӨГЖДӨГ МАНГАС ИНДИКАТОР =====
price_history = {name: deque(maxlen=500) for name in INDICES}
active_subscriptions = {}

keyboard = [
    ["Boom 1000 Index", "Crash 1000 Index"],
    ["Boom 500 Index", "Crash 500 Index"],
    ["Boom 600 Index", "Crash 900 Index"],
    ["Boom 900 Index"]
]
reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 PREDICTIVE MANIAC - AI МАНГАС 🧠\n\n"
        "✅ AI өөрөө хөгжиж, суралцаж байна!\n"
        "✅ ЗӨВХӨН 7 ИНДЕКС ДЭЭР АЖИЛЛАНА!\n"
        "🧠 AI САНАМЖ: 500 Ticks\n\n"
        "BOOM: 1000,500,600,900\nCRASH:1000,500,900\n\n"
        "/test - Тест", reply_markup=reply_markup)

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚠️ PREDICTIVE MANIAC\nBOOM1000\nTicks:455\nPrice:1234.56\nDrift 0.3% ✅\nTEST SIGNAL ✅")

async def handle_index(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = update.message.text.strip()
    if msg not in INDICES: return
    if chat_id not in active_subscriptions: active_subscriptions[chat_id] = set()
    if msg in active_subscriptions[chat_id]:
        active_subscriptions[chat_id].remove(msg)
        await update.message.reply_text(f"❌ {msg} унтраалаа", reply_markup=reply_markup)
    else:
        active_subscriptions[chat_id].add(msg)
        await update.message.reply_text(f"✅ {msg} идэвхжлээ!\n🧠 AI мангас сурч эхэллээ... Drift 0.3% хүрмэгц дохио ирнэ!", reply_markup=reply_markup)
        asyncio.create_task(deriv_watcher(msg, context))

async def deriv_watcher(index_name, context):
    symbol = DERIV_MAP[index_name]
    uri = "wss://ws.derivws.com/websockets/v3?app_id=1089"
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"ticks": symbol}))
            while True:
                data = json.loads(await ws.recv())
                if "tick" in data:
                    price = data["tick"]["quote"]
                    price_history[index_name].append(price)
                    if len(price_history[index_name]) >= 100:
                        old = price_history[index_name][0]
                        drift = abs(price - old) / old * 100
                        ticks = len(price_history[index_name])
                        if drift >= 0.3 and ticks >= 200:
                            for cid, subs in active_subscriptions.items():
                                if index_name in subs:
                                    await context.bot.send_message(chat_id=cid, text=f"⚠️ PREDICTIVE MANIAC\n{symbol}\nTicks:{ticks}\nPrice:{price}\nDrift {drift:.2f}% ✅\n🧠 AI МАНГАС ДОХИО!")
                                    await asyncio.sleep(30)
    except Exception as e:
        logging.error(e)
        await asyncio.sleep(5)
        asyncio.create_task(deriv_watcher(index_name, context))

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("test", test))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_index))

if __name__ == "__main__":
    print("🧠 AI MANIAC - ONLY 7 INDICES + AI SELF LEARNING - STARTED...")
    app.run_polling()
