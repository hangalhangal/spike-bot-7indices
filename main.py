import os, asyncio, json, logging, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import deque
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import websockets

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_TOKEN")

# === PORT FIX - Хамгийн эхэнд шууд нээх ===
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"AI MANIAC 7 LIVE")
    def do_HEAD(self):
        self.send_response(200); self.end_headers()
    def log_message(self,*a): return

def keep():
    try:
        port=int(os.getenv("PORT",10000))
        HTTPServer(("0.0.0.0",port),H).serve_forever()
    except Exception as e: logging.error(e)

threading.Thread(target=keep,daemon=True).start()

# === ЗӨВХӨН 7 МАНГАС - ДҮРЭМ ===
INDICES = ["Boom 1000 Index","Boom 500 Index","Boom 600 Index","Boom 900 Index","Crash 1000 Index","Crash 500 Index","Crash 900 Index"]
MAP = {"Boom 1000 Index":"BOOM1000","Boom 500 Index":"BOOM500","Boom 600 Index":"BOOM600","Boom 900 Index":"BOOM900","Crash 1000 Index":"CRASH1000","Crash 500 Index":"CRASH500","Crash 900 Index":"CRASH900"}

# === AI МАНГАС 500 TICKS ===
history = {n: deque(maxlen=500) for n in INDICES}
subs = {}

kb = [["Boom 1000 Index","Crash 1000 Index"],["Boom 500 Index","Crash 500 Index"],["Boom 600 Index","Crash 900 Index"],["Boom 900 Index"]]
mk = ReplyKeyboardMarkup(kb,resize_keyboard=True)

async def start(u,c): await u.message.reply_text("🧠 AI МАНГАС - ЗӨВХӨН 7 ИНДЕКС\n500 ticks санах ойтой\nДоороос сонго: /test",reply_markup=mk)
async def test(u,c): await u.message.reply_text("⚠️ PREDICTIVE MANIAC\nBOOM1000\nTicks:455\nDrift 0.3% ✅\nAI МАНГАС TEST OK!")
async def handle(u,c):
    txt=u.message.text.strip()
    if txt not in INDICES: return
    chat=u.effective_chat.id; subs.setdefault(chat,set())
    if txt in subs[chat]:
        subs[chat].remove(txt); await u.message.reply_text(f"❌ {txt} унтраалаа",reply_markup=mk)
    else:
        subs[chat].add(txt); await u.message.reply_text(f"✅ {txt} идэвхжлээ!\n🧠 AI сурч эхэллээ Drift 0.3%",reply_markup=mk)
        asyncio.create_task(watch(txt,c))

async def watch(name,c):
    uri="wss://ws.derivws.com/websockets/v3?app_id=1089"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"ticks":MAP[name]}))
                while True:
                    d=json.loads(await ws.recv())
                    if "tick" in d:
                        p=d["tick"]["quote"]; history[name].append(p)
                        if len(history[name])>=200:
                            drift=abs(p-history[name][0])/history[name][0]*100
                            ticks=len(history[name])
                            if drift>=0.3:
                                for cid,s in subs.items():
                                    if name in s:
                                        try: await c.bot.send_message(cid,f"⚠️ PREDICTIVE MANIAC\n{MAP[name]}\nTicks:{ticks}\nPrice:{p}\nDrift {drift:.2f}% ✅\n🧠 AI МАНГАС!",reply_markup=mk); await asyncio.sleep(30)
                                        except: pass
        except Exception as e: logging.error(f"{name} {e}"); await asyncio.sleep(5)

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start",start))
app.add_handler(CommandHandler("test",test))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle))

if __name__=="__main__":
    print("🧠 AI MANIAC 7 ONLY - STARTED")
    # БУРУУ БАЙСАН: close_loop=False => ЭНЭ УСТГАСАН!
    app.run_polling(stop_signals=None)
