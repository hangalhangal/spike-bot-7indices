import os,json,time,math,asyncio,logging,threading
from http.server import BaseHTTPRequestHandler,HTTPServer
from collections import deque
import websockets
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder,CommandHandler,CallbackQueryHandler,ContextTypes

logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s")
TOKEN=os.getenv("TELEGRAM_TOKEN")
if not TOKEN: raise RuntimeError("TELEGRAM_TOKEN missing")

INDICES={
"BOOM1000":{"name":"Boom 1000 Index","type":"BOOM"},
"BOOM500":{"name":"Boom 500 Index","type":"BOOM"},
"BOOM600":{"name":"Boom 600 Index","type":"BOOM"},
"BOOM900":{"name":"Boom 900 Index","type":"BOOM"},
"CRASH1000":{"name":"Crash 1000 Index","type":"CRASH"},
"CRASH500":{"name":"Crash 500 Index","type":"CRASH"},
"CRASH900":{"name":"Crash 900 Index","type":"CRASH"}}
KNOWN_DERIV_SYMBOLS={"BOOM1000":"BOOM1000","BOOM500":"BOOM500","BOOM600":"BOOM600","BOOM900":"BOOM900","CRASH1000":"CRASH1000","CRASH500":"CRASH500","CRASH900":"CRASH900"}
DERIV_SYMBOLS={k:KNOWN_DERIV_SYMBOLS.get(k,k) for k in INDICES}
DERIV_APP_ID=os.getenv("DERIV_APP_ID","1089")
LIVE_HISTORY=500; WARMUP_HISTORY=5000; SPIKE_THRESHOLD=.10; SIGNAL_CONFIDENCE=.40; COOLDOWN_SECONDS=120; LEARNING_RATE=.025; L2=.0003; MIN_TRAINING_SAMPLES=300; FEATURE_COUNT=33; RECENT_RESULTS=100; MEMORY_FILE="ai_memory_v5_fixed.json"
active=set(); chat_ids=set()
ticks_data={k:deque(maxlen=LIVE_HISTORY) for k in INDICES}
history_data={k:[] for k in INDICES}
models={}; stats={}; recent={}; pending={}; last_signal={}; tasks={}; trained=set()
last_probs={k:[1/3]*3 for k in INDICES}; last_class={k:0 for k in INDICES}
diag={}
for k in INDICES:
    models[k]={"weights":[[0.0]*FEATURE_COUNT for _ in range(3)],"bias":[0.0]*3,"samples":0}
    stats[k]={"ok":0,"fail":0,"training":0,"no_spike":0}
    recent[k]=deque(maxlen=RECENT_RESULTS); pending[k]=[]; last_signal[k]=0.0
    diag[k]={"ticks":0,"features":0,"candidates":0,"signals":0,"blocked_confidence":0,"blocked_cooldown":0,"blocked_training":0,"errors":0,"last_error":"","last_tick":0.0,"last_confidence":0.0,"connected":0,"history":0,"subscribed":0,"last_msg_type":"","stage":"IDLE"}

def save_memory():
    try:
        with open(MEMORY_FILE+".tmp","w",encoding="utf-8") as f: json.dump({"models":models,"stats":stats,"recent":{k:list(v) for k,v in recent.items()}},f)
        os.replace(MEMORY_FILE+".tmp",MEMORY_FILE)
    except Exception as e: logging.error("save: %s",e)
def load_memory():
    if not os.path.exists(MEMORY_FILE): return
    try:
        with open(MEMORY_FILE,encoding="utf-8") as f: d=json.load(f)
        for k in INDICES:
            m=d.get("models",{}).get(k)
            if m and len(m.get("weights",[]))==3 and all(len(x)==FEATURE_COUNT for x in m["weights"]): models[k]=m
            if k in d.get("stats",{}): stats[k].update(d["stats"][k])
            if k in d.get("recent",{}): recent[k]=deque(d["recent"][k],maxlen=RECENT_RESULTS)
    except Exception as e: logging.error("load: %s",e)
def softmax(s):
    m=max(s); ex=[math.exp(max(-50,min(50,x-m))) for x in s]; z=sum(ex)
    return [x/z for x in ex] if z else [1/3]*3
def dot(a,b): return sum(x*y for x,y in zip(a,b))
def predict(k,f):
    m=models[k]; p=softmax([dot(m["weights"][c],f)+m["bias"][c] for c in range(3)])
    c=max(range(3),key=lambda i:p[i]); return c,p[c],p
def train_model(k,f,y):
    m=models[k]; p=softmax([dot(m["weights"][c],f)+m["bias"][c] for c in range(3)])
    for c in range(3):
        err=(1.0 if c==y else 0.0)-p[c]
        for i,x in enumerate(f): m["weights"][c][i]+=LEARNING_RATE*(err*x-L2*m["weights"][c][i])
        m["bias"][c]+=LEARNING_RATE*err
    m["samples"]+=1; stats[k]["training"]+=1
def features(prices):
    if len(prices)<210:return None
    p=[float(x) for x in prices]; cur=p[-1]
    if not cur:return None
    def ret(n): o=p[-1-n]; return (cur-o)/o*100 if o else 0
    r1,r3,r5,r10,r20,r50,r100,r200=[ret(n) for n in (1,3,5,10,20,50,100,200)]
    mom=.30*r5+.25*r10+.20*r20+.15*r50+.10*r100; acc=r5-r20
    q=p[-50:]; ch=[abs((q[i]-q[i-1])/q[i-1]*100) for i in range(1,len(q)) if q[i-1]]
    vol=sum(ch)/len(ch) if ch else 0
    up=sum(p[-i]>p[-i-1] for i in range(1,30)); down=sum(p[-i]<p[-i-1] for i in range(1,30))
    pressure=(up-down)/30
    gains=[];losses=[]
    for i in range(max(1,len(p)-15),len(p)):
        d=p[i]-p[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/len(gains) if gains else 0; al=sum(losses)/len(losses) if losses else 0
    rsi=100 if ag and not al else (50 if not al else 100-100/(1+ag/al)); rsi_n=(rsi-50)/50
    mean=lambda n:sum(p[-n:])/min(n,len(p))
    e12,e26=mean(12),mean(26); macd=(e12-e26)/cur*100; macds=(e12-mean(9))/cur*100
    tr=[abs(p[i]-p[i-1]) for i in range(max(1,len(p)-30),len(p))]
    atr=sum(tr)/len(tr) if tr else 0; atrp=atr/cur*100; atrm=(p[-1]-p[-2])/atr if atr else 0
    w=p[-20:]; mm=sum(w)/len(w); sd=(sum((x-mm)**2 for x in w)/len(w))**.5; bb=(cur-mm)/(2*sd) if sd else 0
    s20,s50,s100=mean(20),mean(50),mean(100)
    trend=.5*((s20-s50)/s50*100 if s50 else 0)+.3*((s50-s100)/s100*100 if s100 else 0)+.2*((cur-s20)/s20*100 if s20 else 0)
    plus=minus=0
    for i in range(max(1,len(p)-20),len(p)):
        d=p[i]-p[i-1]
        if d>0:plus+=d
        elif d<0:minus-=d
    dmi=(plus-minus)/(plus+minus) if plus+minus else 0; adx=abs(dmi)
    a=p[-20:]; b=p[-40:-20]; rh,rl=max(a),min(a); ph,pl=max(b),min(b)
    structure=1 if rh>ph and rl>pl else (-1 if rh<ph and rl<pl else 0)
    bos=1 if cur>ph else (-1 if cur<pl else 0)
    ps=1 if p[-20]>p[-40] else (-1 if p[-20]<p[-40] else 0)
    cs=1 if cur>p[-20] else (-1 if cur<p[-20] else 0); choch=cs if cs!=ps else 0
    look=p[-31:-1]; hi=max(look);lo=min(look); liq=1 if cur>hi else (-1 if cur<lo else 0)
    fvg=1 if p[-5]<p[-3]<p[-1] else (-1 if p[-5]>p[-3]>p[-1] else 0)
    dif=[p[i]-p[i-1] for i in range(max(1,len(p)-15),len(p))]
    mx=max((abs(x) for x in dif),default=0); ob=max(dif,key=abs)/mx if mx else 0
    old=[abs(p[i]-p[i-1]) for i in range(max(1,len(p)-60),max(1,len(p)-30))]
    new=[abs(p[i]-p[i-1]) for i in range(max(1,len(p)-30),len(p))]
    oa=sum(old)/len(old) if old else 0; na=sum(new)/len(new) if new else 0; amd=0
    if oa:
        z=na/oa
        if z>1.3:amd=1 if mom>0 else -1
        elif z<.75:amd=.5 if mom>0 else -.5
    large=0; since=300
    for i in range(max(1,len(p)-300),len(p)):
        if abs((p[i]-p[i-1])/p[i-1]*100)>=.10:large+=1;since=0
        else:since=min(since+1,300)
    dist=since/300; freq=min(large/10,1)
    r10rng=max(p[-10:])-min(p[-10:]); r50rng=max(p[-50:])-min(p[-50:])
    comp=1-min(r10rng/r50rng,1) if r50rng else 0
    f=[r1,r3,r5,r10,r20,r50,r100,r200,mom,acc,vol,pressure,rsi_n,macd,macds,atrp,atrm,bb,trend,adx,dmi,structure,bos,choch,liq,fvg,ob,amd,dist,freq,comp,r1-r3,pressure*.6+dmi*.4]
    return [max(-5,min(5,float(x))) for x in f]
def label_at(prices,times,i):
    if i<210 or i>=len(prices)-1 or not prices[i]:return None
    t0=times[i]; start=prices[i]
    for j in range(i+1,len(prices)):
        dt=times[j]-t0
        if dt<60:continue
        if dt>120:break
        move=(prices[j]-start)/start*100
        if abs(move)>=.10:return 1 if move>0 else 2
    return 0 if times[-1]-t0>=120 else None
def warmup(k):
    if k in trained:return
    d=history_data[k]
    if len(d)<500:return
    ts=[x[0] for x in d]; ps=[x[1] for x in d]
    pools=[[],[],[]]
    step=max(1,(len(ps)-211)//1200)
    for i in range(210,len(ps)-1,step):
        y=label_at(ps,ts,i)
        if y is None:continue
        f=features(ps[:i+1])
        if f is not None:pools[y].append(f)
    if not any(pools):return
    pairs=[]; n=max(len(x) for x in pools)
    for i in range(n):
        for y in range(3):
            if pools[y]: pairs.append((y,pools[y][i%len(pools[y])]))
            if len(pairs)>=300:break
        if len(pairs)>=300:break
    for y,f in pairs:train_model(k,f,y)
    trained.add(k);save_memory()
def eval_one(pred,data,now):
    t0=pred["time"];start=pred["price"];found=None
    for ts,price in data:
        dt=ts-t0
        if dt<60:continue
        if dt>120:break
        move=(price-start)/start*100
        if abs(move)>=.10:found=1 if move>0 else 2;break
    if found is None:return None if now-t0<120 else 0
    wanted=1 if pred["direction"]=="BUY" else 2
    return 1 if found==wanted else -1
async def evaluate(k,now):
    if not pending[k]:return
    data=list(ticks_data[k]); rem=[];changed=False
    for p in pending[k]:
        r=eval_one(p,data,now)
        if r is None:rem.append(p);continue
        changed=True
        if r==1:stats[k]["ok"]+=1;recent[k].append(1);actual=1 if p["direction"]=="BUY" else 2
        elif r==-1:stats[k]["fail"]+=1;recent[k].append(0);actual=2 if p["direction"]=="BUY" else 1
        else:stats[k]["fail"]+=1;recent[k].append(0);actual=0
        train_model(k,p["features"],actual)
    pending[k]=rem
    if changed:save_memory()
async def send_signal(app,k,direction,conf,probs):
    info=INDICES[k]
    text=(f"MANIAC V5.6 FIXED\n{info['name']}\n{'BUY 🟢' if direction=='BUY' else 'SELL 🔴'} {conf*100:.1f}%\nUP {probs[1]*100:.1f}% DOWN {probs[2]*100:.1f}%")
    for cid in list(chat_ids):
        try: await app.bot.send_message(chat_id=cid,text=text)
        except: pass
async def deriv_ws(k,app):
    uri=f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    while k in active:
        try:
            async with websockets.connect(uri,ping_interval=20,ping_timeout=20,close_timeout=10) as ws:
                diag[k]["connected"]=1; diag[k]["stage"]="HISTORY"
                await ws.send(json.dumps({"ticks_history":DERIV_SYMBOLS[k],"count":WARMUP_HISTORY,"style":"ticks"}))
                while k in active:
                    raw=await asyncio.wait_for(ws.recv(), timeout=20)
                    msg=json.loads(raw)
                    if "error" in msg: raise RuntimeError(msg["error"].get("message","history error"))
                    if msg.get("msg_type")=="history" and msg.get("history"):
                        h=msg["history"]; ps=h.get("prices",[]); ts=h.get("times",[])
                        history_data[k]=[(float(ts[i]),float(x)) for i,x in enumerate(ps) if i<len(ts)]
                        diag[k]["history"]=len(history_data[k])
                        ticks_data[k].clear()
                        for item in history_data[k][-LIVE_HISTORY:]: ticks_data[k].append(item)
                        await asyncio.to_thread(warmup,k)
                        break
            async with websockets.connect(uri,ping_interval=20,ping_timeout=20,close_timeout=10) as ws:
                diag[k]["connected"]=1; diag[k]["stage"]="LIVE"
                await ws.send(json.dumps({"ticks":DERIV_SYMBOLS[k],"subscribe":1}))
                while k in active:
                    raw=await ws.recv()
                    msg=json.loads(raw)
                    if "error" in msg: raise RuntimeError(msg["error"].get("message","live error"))
                    tick=msg.get("tick")
                    if tick:
                        price=float(tick["quote"]); ts=float(tick.get("epoch",time.time()))
                        diag[k]["subscribed"]=1; diag[k]["stage"]="LIVE"; diag[k]["ticks"]+=1; diag[k]["last_tick"]=ts
                        ticks_data[k].append((ts,price))
                        ps=[x[1] for x in ticks_data[k]]
                        if len(ps)<210: continue
                        await evaluate(k,ts)
                        f=features(ps)
                        if not f: continue
                        c,conf,probs=predict(k,f)
                        last_probs[k]=probs; diag[k]["last_confidence"]=conf
                        if models[k]["samples"]<300: continue
                        if c==0: continue
                        if conf<0.40: continue
                        if time.time()-last_signal[k]<120: continue
                        direction="BUY" if c==1 else "SELL"
                        pending[k].append({"time":ts,"price":price,"direction":direction,"features":f,"confidence":conf})
                        last_signal[k]=time.time()
                        await send_signal(app,k,direction,conf,probs)
        except asyncio.CancelledError: return
        except Exception as e:
            diag[k]["connected"]=0; diag[k]["subscribed"]=0; diag[k]["last_error"]=str(e); diag[k]["stage"]="ERROR"
            await asyncio.sleep(5)
def keep_alive():
    port=int(os.environ.get("PORT","10000"))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b"V5.6 LIVE")
        def log_message(self,*a):pass
    HTTPServer(("0.0.0.0",port),H).serve_forever()
threading.Thread(target=keep_alive,daemon=True).start()
def buttons():
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{'✅' if k in active else '❌'} {v['name']}",callback_data=k)] for k,v in INDICES.items()])
async def start(update,context):
    chat_ids.add(update.effective_chat.id)
    for k in INDICES:
        active.add(k)
        if k not in tasks or tasks[k].done():tasks[k]=asyncio.create_task(start_ws(k,context.application))
    await update.message.reply_text("MANIAC V5.6 FULL FIXED - 7 INDEX LIVE",reply_markup=buttons())
async def button(update,context):
    q=update.callback_query;await q.answer();k=q.data;chat_ids.add(q.message.chat.id)
    if k in active:
        active.remove(k);t=tasks.get(k)
        if t and not t.done():t.cancel()
    else:
        active.add(k)
        if k not in tasks or tasks[k].done():tasks[k]=asyncio.create_task(start_ws(k,context.application))
    try:await q.edit_message_reply_markup(reply_markup=buttons())
    except:pass
async def status(update,context):
    chat_ids.add(update.effective_chat.id)
    await update.message.reply_text(f"ACTIVE {len(active)}/7 V5.6")
    for k,info in INDICES.items():
        text=f"{info['name']}\nStage {diag[k]['stage']}\nWS {'ON ✅' if diag[k]['connected'] else 'OFF ❌'}\nSub {'YES ✅' if diag[k]['subscribed'] else 'NO ❌'}\nLive {diag[k]['ticks']}\nHist {diag[k]['history']}\nErr {diag[k]['last_error'] or '-'}"
        await update.message.reply_text(text)
async def symbols(update,context):
    chat_ids.add(update.effective_chat.id)
    await update.message.reply_text("\n".join([f"{v['name']}: {DERIV_SYMBOLS[k]}" for k,v in INDICES.items()]))
async def ai(update,context):
    chat_ids.add(update.effective_chat.id)
    await update.message.reply_text("\n".join([f"{v['name']} {models[k]['samples']}" for k,v in INDICES.items()]))
async def test(update,context):
    chat_ids.add(update.effective_chat.id)
    await update.message.reply_text("TEST V5.6 OK")
load_memory()
async def auto_start(application):
    for k in INDICES:
        active.add(k)
        if k not in tasks or tasks[k].done(): tasks[k]=asyncio.create_task(start_ws(k,application))
app=ApplicationBuilder().token(TOKEN).post_init(auto_start).build()
app.add_handler(CommandHandler("start",start))
app.add_handler(CommandHandler("status",status))
app.add_handler(CommandHandler("symbols",symbols))
app.add_handler(CommandHandler("ai",ai))
app.add_handler(CommandHandler("test",test))
app.add_handler(CallbackQueryHandler(button))
if __name__=="__main__":
    app.run_polling()
