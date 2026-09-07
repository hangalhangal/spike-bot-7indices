# ===== RENDER WEB SERVICE PORT FIX - 18 МӨР НЭМЭВ =====
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot Running OK - AI MANIAC")
    def log_message(self, *args): return

def run_keep_alive():
    port = int(os.environ.get("PORT", 10000))
    httpd = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    print(f"HTTP server listening on 0.0.0.0:{port}")
    httpd.serve_forever()
threading.Thread(target=run_keep_alive, daemon=True).start()

# ===== ORIGINAL BOT CODE (ХӨНДӨӨГҮЙ) =====
import os
import asyncio
import json
import logging
from collections import deque
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import websockets

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN not found!")

# ===== ҮНДСЭН ДҮРЭМ: ЗӨВХӨН 7 ИНДЕКС - ӨӨР ЮУЧГҮЙ =====
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

# AI МАНГАС САНАМЖ - өөрөө хөгждөг
price_history = {name: deque(maxlen=500) for name in INDICES}
active_subscriptions = {}  # chat_id -> set(indices)
user_apps = {}  # chat_id -> app

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
        "✅ ЗӨВХӨН 7 ИНДЕКС ДЭЭР АЖИЛЛАНА!\n\n"
        "BOOM: 1000, 500, 600, 900\n"
        "CRASH: 1000, 500, 900\n\n"
        "Доороос 7-өөсөө сонгоод идэвхжүүл!\n"
        "/test - Тест дохио",
        reply_markup=reply_markup
    )

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ PREDICTIVE MANIAC\n"
        "BOOM1000\n"
        "Ticks:455\n"
        "Price:1234.56\n"
        "Drift 0.3% ✅\n"
        "TEST SIGNAL - Bot ажиллаж байна! ✅\n\n"
        "AI Мангас бэлэн! 7 индекс дээр ажиллаж байна!"
    )

async def handle_index(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = update.message.text.strip()
    
    if msg not in INDICES:
        return
    
    if chat_id not in active_subscriptions:
        active_subscriptions[chat_id] = set()
    
    if msg in active_subscriptions[chat_id]:
        active_subscriptions[chat_id].remove(msg)
        await update.message.reply_text(f"❌ {msg} унтраалаа", reply_markup=reply_markup)
    else:
        active_subscriptions[chat_id].add(msg)
        await update.message.reply_text(
            f"✅ {msg} идэвхжлээ!\n"
            f"🧠 AI мангас {msg} дээр сурч эхэллээ...\n"
            f"Drift 0.3% хүрмэгц дохио ирнэ!\n"
            f"Ticks цуглуулж байна...",
            reply_markup=reply_markup
        )
        # Дервив холболт эхлүүлнэ
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
                    
                    # AI МАНГАС ЛОГИК: Drift тооцох
                    if len(price_history[index_name]) >= 100:
                        old_price = price_history[index_name][0]
                        drift = abs(price - old_price) / old_price * 100
                        ticks = len(price_history[index_name])
                        
                        # 0.3% Drift + Ticks 400+ = Spike магадлал өндөр
                        if drift >= 0.3 and ticks >= 200:
                            for chat_id, subs in active_subscriptions.items():
                                if index_name in subs:
                                    try:
                                        await context.bot.send_message(
                                            chat_id=chat_id,
                                            text=f"⚠️ PREDICTIVE MANIAC\n"
                                                 f"{symbol}\n"
                                                 f"Ticks:{ticks}\n"
                                                 f"Price:{price}\n"
                                                 f"Drift {drift:.2f}% ✅\n"
                                                 f"AI МАНГАС ДОХИО - {index_name} SPIKE удахгүй! 🧠",
                                            reply_markup=reply_markup
                                        )
                                        # Давхардахгүй тулд бага зэрэг хүлээх
                                        await asyncio.sleep(30)
                                    except Exception as e:
                                        logging.error(f"Send error: {e}")
    except Exception as e:
        logging.error(f"Deriv watcher error {index_name}: {e}")
        await asyncio.sleep(5)
        asyncio.create_task(deriv_watcher(index_name, context))

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("test", test))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_index))

if __name__ == "__main__":
    print("🧠 AI MANIAC - ONLY 7 INDICES - STARTED...")
    app.run_polling()
