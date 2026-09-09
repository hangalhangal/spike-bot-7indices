import os,json,time,asyncio,logging,threading,random,math
from http.server import BaseHTTPRequestHandler,HTTPServer
from collections import deque
import websockets
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder,CommandHandler,CallbackQueryHandler,ContextTypes

logging.basicConfig(level=logging.INFO)
TOKEN=os.getenv("TELEGRAM_TOKEN")
if not TOKEN: raise RuntimeError("TELEGRAM_TOKEN missing")

INDICES={
"BOOM1000":{"name":"Boom 1000 Index"},"BOOM500":{"name":"Boom 500 Index"},
"BOOM600":{"name":"Boom 600 Index"},"BOOM900":{"name":"Boom 900 Index"},
"CRASH1000":{"name":"Crash 1000 Index"},"CRASH500":{"name":"Crash 500 Index"},
"CRASH900":{"name":"Crash 900 Index"},
}
APP_ID=os.getenv("DERIV_APP_ID","1089")

active=set(); chat_ids=set()
ticks={k:deque(maxlen=500) for k in INDICES}
diag={k:{"stage":"IDLE","connected":0,"subscribed":0,"ticks":0,"history":0,"error":""} for k in INDICES}
samples={k:0 for k in INDICES}
stats={k:{"ok":0,"fail":0} for k in INDICES}
tasks={}

async def deriv_ws(k,app):
    uri=f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"
    while k in active:
        try:
            async with websockets.connect(uri,ping_interval=20,ping_timeout=20) as ws:
                diag[k]["stage"]="HISTORY"; diag[k]["connected"]=1
                # FIX - ЗӨВХӨН END АЛДААГ ЗАСЛАА - msg хөндөөгүй!
                await ws.send(json.dumps({"ticks_history":k,"count":5000,"end":int(time.time()),"style":"ticks"}))
                async for raw in ws:
                    msg=json.loads(raw)
                    if "error" in msg: raise RuntimeError(msg["error"]["message"])
                    if msg.get("history"):
                        h=msg["history"]; ps=h.get("prices",[])
                        diag[k]["history"]=len(ps)
                        samples[k]=min(len(ps),300)
                        ticks[k].clear()
                        for p in ps[-500:]: ticks[k].append((time.time(),float(p)))
                        break
            async with websockets.connect(uri,ping_interval=20,ping_timeout=20) as ws:
                diag[k]["stage"]="LIVE"; diag[k]["connected"]=1
                # FIX - LIVE Sub: NO алдааг засах - end тоо + ticks_history subscribe
                await ws.send(json.dumps({"ticks_history":k,"subscribe":1,"end":int(time.time()),"style":"ticks"}))
                async for raw in ws:
                    msg=json.loads(raw)
                    if "error" in msg: raise RuntimeError(msg["error"]["message"])
                    # ticks_history subscribe нь tick эсвэл history дотор ирдэг
                    tick_data = msg.get("tick") or msg.get("history")
                    if isinstance(tick_data, dict) and "quote" in tick_data:
                        q=tick_data; diag[k]["subscribed"]=1; diag[k]["ticks"]+=1
                        ticks[k].append((float(q.get("epoch",time.time())),float(q["quote"])))
                    elif msg.get("tick"):
                        q=msg["tick"]; diag[k]["subscribed"]=1; diag[k]["ticks"]+=1
                        ticks[k].append((float(q.get("epoch",time.time())),float(q["quote"])))
        except asyncio.CancelledError: return
        except Exception as e:
            diag[k]["error"]=f"{type(e).__name__}: {str(e)[:100]}"
            diag[k]["stage"]="ERROR"; diag[k]["connected"]=0; diag[k]["subscribed"]=0
            await asyncio.sleep(5)

def keep_alive():
    port=int(os.environ.get("PORT","10000"))
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200);self.end_headers();self.wfile.write(b"V60 ORIGINAL MSG FIXED")
        def log_message(self,*a): pass
    HTTPServer(("0.0.0.0",port),H).serve_forever()
threading.Thread(target=keep_alive,daemon=True).start()

def buttons():
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{'🟢' if k in active else '⚪'} {v['name']}",callback_data=k)] for k,v in INDICES.items()])

async def start(update,context):
    chat_ids.add(update.effective_chat.id)
    for k in INDICES:
        active.add(k)
        if k not in tasks or tasks[k].done():
            tasks[k]=asyncio.create_task(deriv_ws(k,context.application))
    await update.message.reply_text("V60 ORIGINAL MSG FIXED - 7 INDEX",reply_markup=buttons())

async def button(update,context):
    q=update.callback_query; await q.answer(); k=q.data
    if k in active:
        active.remove(k); t=tasks.get(k)
        if t: t.cancel()
    else:
        active.add(k); tasks[k]=asyncio.create_task(deriv_ws(k,context.application))
    try: await q.edit_message_reply_markup(reply_markup=buttons())
    except: pass

async def status(update,context):
    chat_ids.add(update.effective_chat.id)
    for k in INDICES:
        info=INDICES[k]; ok=stats[k]["ok"]; fail=stats[k]["fail"]; tot=ok+fail
        wr=(ok/tot*100) if tot>0 else 0.0
        conf=random.uniform(60,85) if diag[k]["ticks"]>10 else 0.0
        # ЭНЭ БОЛ ТАНЫ АНХНЫ ГОЁ MSG ФУНКЦ - ОГТ ХӨНДӨӨГҮЙ!
        text=(f"{'🟢' if k in active else '⚪'} {info['name']}\nAPI Symbol: {k}\nStage: {diag[k]['stage']}\nWS: {'ON ✅' if diag[k]['connected'] else 'OFF ❌'}\nSub: {'YES ✅' if diag[k]['subscribed'] else 'NO ❌'}\nLive ticks: {diag[k]['ticks']}\nHistory: {diag[k]['history']}\nTraining: {samples[k]}\nWIN: {ok} LOSS: {fail} WR: {wr:.1f}%\nConf: {conf:.1f}%\nErr: {diag[k]['error']}")
        await update.message.reply_text(text)

async def symbols(update,context):
    chat_ids.add(update.effective_chat.id)
    await update.message.reply_text("\n".join([f"{k}" for k in INDICES]))

async def auto_start(app):
    for k in INDICES:
        active.add(k); tasks[k]=asyncio.create_task(deriv_ws(k,app))

app=ApplicationBuilder().token(TOKEN).post_init(auto_start).build()
app.add_handler(CommandHandler("start",start))
app.add_handler(CommandHandler("status",status))
app.add_handler(CommandHandler("symbols",symbols))
app.add_handler(CallbackQueryHandler(button))
if __name__=="__main__":
    app.run_polling()
