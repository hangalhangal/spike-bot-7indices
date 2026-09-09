import os,json,time,asyncio,logging,threading
from http.server import BaseHTTPRequestHandler,HTTPServer
from collections import deque
import websockets
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder,CommandHandler,CallbackQueryHandler,ContextTypes

logging.basicConfig(level=logging.INFO)
TOKEN=os.getenv("TELEGRAM_TOKEN")
if not TOKEN: raise RuntimeError("TELEGRAM_TOKEN missing")

INDICES={
"BOOM1000":{"name":"Boom 1000"},
"BOOM500":{"name":"Boom 500"},
"BOOM600":{"name":"Boom 600"},
"BOOM900":{"name":"Boom 900"},
"CRASH1000":{"name":"Crash 1000"},
"CRASH500":{"name":"Crash 500"},
"CRASH900":{"name":"Crash 900"},
}
APP_ID=os.getenv("DERIV_APP_ID","1089")

active=set(); chat_ids=set()
ticks={k:deque(maxlen=500) for k in INDICES}
diag={k:{"ticks":0,"history":0,"connected":0,"subscribed":0,"stage":"IDLE","error":""} for k in INDICES}
tasks={}

async def deriv_ws(k,app):
    uri=f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"
    while k in active:
        try:
            async with websockets.connect(uri,ping_interval=20,ping_timeout=20) as ws:
                diag[k]["stage"]="HISTORY"
                diag[k]["connected"]=1
                # FIX V58.2 - end тоогоор заавал өгөх ёстой!
                await ws.send(json.dumps({"ticks_history":k,"count":1000,"end":int(time.time()),"style":"ticks"}))
                async for raw in ws:
                    msg=json.loads(raw)
                    if "error" in msg: raise RuntimeError(msg["error"]["message"])
                    if msg.get("history"):
                        h=msg["history"]
                        ps=h.get("prices",[]); ts=h.get("times",[])
                        diag[k]["history"]=len(ps)
                        ticks[k].clear()
                        for i in range(max(0,len(ps)-500),len(ps)):
                            ticks[k].append((float(ts[i]),float(ps[i])))
                        break
            async with websockets.connect(uri,ping_interval=20,ping_timeout=20) as ws:
                diag[k]["stage"]="LIVE"
                diag[k]["connected"]=1
                await ws.send(json.dumps({"ticks":k,"subscribe":1}))
                async for raw in ws:
                    msg=json.loads(raw)
                    if "error" in msg: raise RuntimeError(msg["error"]["message"])
                    t=msg.get("tick")
                    if t:
                        diag[k]["subscribed"]=1
                        diag[k]["ticks"]+=1
                        ticks[k].append((float(t.get("epoch",time.time())),float(t["quote"])))
        except asyncio.CancelledError:
            return
        except Exception as e:
            diag[k]["error"]=str(e)[:100]
            diag[k]["stage"]="ERROR"
            diag[k]["connected"]=0
            diag[k]["subscribed"]=0
            await asyncio.sleep(5)

def keep_alive():
    port=int(os.environ.get("PORT","10000"))
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200);self.end_headers();self.wfile.write(b"V58.2 FIXED END INT")
        def log_message(self,*a): pass
    HTTPServer(("0.0.0.0",port),H).serve_forever()
threading.Thread(target=keep_alive,daemon=True).start()

def buttons():
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{'✅' if k in active else '❌'} {v['name']}",callback_data=k)] for k,v in INDICES.items()])

async def start(update,context):
    chat_ids.add(update.effective_chat.id)
    for k in INDICES:
        active.add(k)
        if k not in tasks or tasks[k].done():
            tasks[k]=asyncio.create_task(deriv_ws(k,context.application))
    await update.message.reply_text("V58.2 FIXED END INT - 7 INDEX",reply_markup=buttons())

async def button(update,context):
    q=update.callback_query;await q.answer();k=q.data
    if k in active:
        active.remove(k)
        t=tasks.get(k)
        if t: t.cancel()
    else:
        active.add(k)
        tasks[k]=asyncio.create_task(deriv_ws(k,context.application))
    try: await q.edit_message_reply_markup(reply_markup=buttons())
    except: pass

async def status(update,context):
    chat_ids.add(update.effective_chat.id)
    await update.message.reply_text(f"ACTIVE {len(active)}/7 V58.2 END INT")
    for k in INDICES:
        d=diag[k]
        await update.message.reply_text(f"{k} Stage {d['stage']} WS {'ON' if d['connected'] else 'OFF'} Sub {'YES' if d['subscribed'] else 'NO'} Live {d['ticks']} Hist {d['history']} Err {d['error']}")

async def symbols(update,context):
    chat_ids.add(update.effective_chat.id)
    await update.message.reply_text("\n".join([k for k in INDICES]))

async def auto_start(app):
    for k in INDICES:
        active.add(k)
        tasks[k]=asyncio.create_task(deriv_ws(k,app))

app=ApplicationBuilder().token(TOKEN).post_init(auto_start).build()
app.add_handler(CommandHandler("start",start))
app.add_handler(CommandHandler("status",status))
app.add_handler(CommandHandler("symbols",symbols))
app.add_handler(CallbackQueryHandler(button))
if __name__=="__main__":
    app.run_polling()
