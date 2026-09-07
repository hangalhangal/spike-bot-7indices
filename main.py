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

# ДҮРЭМ: ЗӨВХӨН 7 ИНДЕКС
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
chat_ids = set()

# Keep Alive - Render Port
def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"AI MANIAC LIVE")
        def log_message(self, *args):
            return
    httpd = HTTPServer(("0.0.0.0", port), Handler)
    httpd.serve_forever()

threading.Thread(target=keep_alive, daemon=True).start()

async def deriv_ws(symbol, key, app):
    uri = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"ticks": symbol}))
        while True:
            if key not in active:
                return
            msg = json.loads(await ws.recv())
            if "tick" in msg:
                price = float(msg["tick"]["quote"])
                ticks_data[key].append(price)
                if len(ticks_data[key]) > 500:
                    ticks_data[key].pop(0)

                # AI МАНГАС 500 ticks + Drift 0.3%
                if len(ticks_data[key]) >= 200:
                    old = ticks_data[key][-200]
                    drift = ((price - old) / old * 100) if old!= 0 else 0

                    if abs(drift) >= 0.3:
                        info = INDICES[key]
                        # BUY / SELL тодорхой болголоо
                        if info["type"] == "BOOM":
                            action = "BUY NOW 🟢\nДээшээ ХАДАХ гэж байна! 📈"
                        else:
                            action = "SELL NOW 🔴\nДоошоо УНАХ гэж байна! 📉"

                        text = (
                            f"⚠️ PREDICTIVE MANIAC\n"
                            f"{info['symbol']} - {action}\n\n"
                            f"Ticks: {len(ticks_data[key])}\n"
                            f"Price: {price}\n"
                            f"Drift {drift:.2f}% ✅\n"
                            f"🧠 AI МАНГАС!"
                        )
                        for cid in list(chat_ids):
                            try:
                                await app.bot.send_message(chat_id=cid, text=text)
                            except:
                                pass
                        # Давхардлаас зайлсхийх
                        await asyncio.sleep(60)

async def start_ws(symbol, key, app):
    while key in active:
        try:
            await deriv_ws(symbol, key, app)
        except Exception as e:
            logging.error(f"{key} ws error {e}")
            await asyncio.sleep(5)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    buttons = []
    for k, v in INDICES.items():
        status = "✅" if k in active else "❌"
        buttons.append([InlineKeyboardButton(f"{status} {v['name']}", callback_data=k)])
    await update.message.reply_text(
        "🧠 AI МАНГАС - ЗӨВХӨН 7 ИНДЕКС\nDrift 0.3% - 500 ticks санах ой",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data
    chat_ids.add(query.message.chat.id)

    if key in active:
        active.remove(key)
        await query.message.reply_text(f"❌ {INDICES[key]['name']} унтраалаа!")
    else:
        active.add(key)
        await query.message.reply_text(f"✅ {INDICES[key]['name']} идэвхжлээ!\n🧠 AI сурч эхэллээ Drift 0.3%")
        asyncio.create_task(start_ws(INDICES[key]['symbol'], key, context.application))

    buttons = []
    for k, v in INDICES.items():
        status = "✅" if k in active else "❌"
        buttons.append([InlineKeyboardButton(f"{status} {v['name']}", callback_data=k)])
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
    except:
        pass

async def test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ PREDICTIVE MANIAC\n"
        "BOOM1000 - BUY NOW 🟢\n"
        "Ticks:455\n"
        "Drift 0.3% ✅\n"
        "AI МАНГАС TEST OK!"
    )

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("test", test_cmd))
app.add_handler(CallbackQueryHandler(button))

if __name__ == "__main__":
    app.run_polling()
