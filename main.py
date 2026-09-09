import os
import json
import time
import math
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import deque

import websockets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ============================================================
# AI MANIAC V5
# ONLY 7 BOOM / CRASH INDICES
# TELEGRAM SIGNAL ONLY
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable is missing")

# ONLY THESE 7
INDICES = {
    "BOOM1000": {"name": "Boom 1000 Index", "symbol": "BOOM1000", "type": "BOOM"},
    "BOOM500": {"name": "Boom 500 Index", "symbol": "BOOM500", "type": "BOOM"},
    "BOOM600": {"name": "Boom 600 Index", "symbol": "BOOM600", "type": "BOOM"},
    "BOOM900": {"name": "Boom 900 Index", "symbol": "BOOM900", "type": "BOOM"},
    "CRASH1000": {"name": "Crash 1000 Index", "symbol": "CRASH1000", "type": "CRASH"},
    "CRASH500": {"name": "Crash 500 Index", "symbol": "CRASH500", "type": "CRASH"},
    "CRASH900": {"name": "Crash 900 Index", "symbol": "CRASH900", "type": "CRASH"},
}

# ---------------- SETTINGS ----------------
LIVE_HISTORY = 500
WARMUP_HISTORY = 5000

EARLY_MIN_SECONDS = 60
EARLY_MAX_SECONDS = 120
SPIKE_THRESHOLD = 0.10

# Only high-confidence predictions can become Telegram signals.
SIGNAL_CONFIDENCE = 0.40
MIN_SIGNAL_CONFIDENCE = 0.40
MAX_SIGNAL_CONFIDENCE = 0.40

# Avoid repeated signals from the same index.
COOLDOWN_SECONDS = 120

# Online learning
LEARNING_RATE = 0.025
L2 = 0.0003

# The model is allowed to signal only after enough labeled examples.
MIN_TRAINING_SAMPLES = 300

# Do not trust recent win-rate until enough live outcomes exist.
MIN_RECENT_RESULTS = 40
RECENT_RESULTS = 100

MEMORY_FILE = "ai_memory_v5.json"

# 0 = NO SPIKE, 1 = UP SPIKE, 2 = DOWN SPIKE
FEATURE_COUNT = 33
CLASS_COUNT = 3

active = set()
chat_ids = set()

ticks_data = {
    k: deque(maxlen=LIVE_HISTORY) for k in INDICES
}

# Full historical data used only during warm-up.
history_data = {
    k: [] for k in INDICES
}

models = {}
learn_stats = {}
recent_results = {}
pending_predictions = {}
last_signal_time = {}
ws_tasks = {}
history_trained = set()

diagnostics = {}

# Latest model probabilities for diagnostics
last_probabilities = {k: [1.0/CLASS_COUNT] * CLASS_COUNT for k in INDICES}
last_predicted_class = {k: 0 for k in INDICES}

for key in INDICES:
    models[key] = {
        "weights": [[0.0] * FEATURE_COUNT for _ in range(CLASS_COUNT)],
        "bias": [0.0] * CLASS_COUNT,
        "samples": 0,
    }
    learn_stats[key] = {"ok": 0, "fail": 0, "training": 0, "no_spike": 0, "early": 0}
    recent_results[key] = deque(maxlen=RECENT_RESULTS)
    pending_predictions[key] = []
    last_signal_time[key] = 0.0
    diagnostics[key] = {"ticks": 0, "features": 0, "candidates": 0, "signals": 0, "blocked_confidence": 0, "blocked_direction": 0, "blocked_cooldown": 0, "blocked_training": 0, "errors": 0, "last_error": "", "last_tick": 0.0, "last_confidence": 0.0}


# ============================================================
# MEMORY
# ============================================================

def save_memory():
    try:
        data = {
            "models": models,
            "stats": learn_stats,
            "recent": {k: list(v) for k, v in recent_results.items()},
        }
        tmp = MEMORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, MEMORY_FILE)
    except Exception as e:
        logging.error("Memory save error: %s", e)


def load_memory():
    if not os.path.exists(MEMORY_FILE):
        logging.info("No previous AI memory. Starting fresh.")
        return

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        for key in INDICES:
            if key in data.get("models", {}):
                m = data["models"][key]
                if (
                    len(m.get("weights", [])) == CLASS_COUNT
                    and all(len(row) == FEATURE_COUNT for row in m["weights"])
                    and len(m.get("bias", [])) == CLASS_COUNT
                ):
                    models[key] = m

            if key in data.get("stats", {}):
                learn_stats[key].update(data["stats"][key])

            if key in data.get("recent", {}):
                recent_results[key] = deque(
                    data["recent"][key], maxlen=RECENT_RESULTS
                )

        logging.info("AI memory loaded.")
    except Exception as e:
        logging.error("Memory load error: %s", e)


# ============================================================
# MATH / MODEL
# ============================================================

def softmax(scores):
    m = max(scores)
    exps = [math.exp(max(-50.0, min(50.0, x - m))) for x in scores]
    total = sum(exps)
    if total <= 0:
        return [1.0 / CLASS_COUNT] * CLASS_COUNT
    return [x / total for x in exps]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def predict(key, features):
    model = models[key]
    scores = [
        dot(model["weights"][c], features) + model["bias"][c]
        for c in range(CLASS_COUNT)
    ]
    probs = softmax(scores)
    best = max(range(CLASS_COUNT), key=lambda i: probs[i])
    return best, probs[best], probs


def train_model(key, features, actual_class):
    model = models[key]

    scores = [
        dot(model["weights"][c], features) + model["bias"][c]
        for c in range(CLASS_COUNT)
    ]
    probs = softmax(scores)

    for c in range(CLASS_COUNT):
        target = 1.0 if c == actual_class else 0.0
        error = target - probs[c]

        for i in range(FEATURE_COUNT):
            gradient = error * features[i] - L2 * model["weights"][c][i]
            model["weights"][c][i] += LEARNING_RATE * gradient

        model["bias"][c] += LEARNING_RATE * error

    model["samples"] += 1
    learn_stats[key]["training"] += 1


# ============================================================
# FEATURE ENGINE
# ============================================================

def calculate_features(prices):
    if len(prices) < 210:
        return None
    p=[float(x) for x in prices]
    cur=p[-1]
    if cur==0: return None
    def ret(n):
        old=p[-1-n]
        return ((cur-old)/old*100.0) if old else 0.0
    r1,r3,r5,r10,r20,r50,r100,r200=[ret(n) for n in (1,3,5,10,20,50,100,200)]
    momentum=r5*.30+r10*.25+r20*.20+r50*.15+r100*.10
    acceleration=r5-r20
    recent=p[-50:]
    ch=[abs((recent[i]-recent[i-1])/recent[i-1]*100) for i in range(1,len(recent)) if recent[i-1]!=0]
    volatility=sum(ch)/len(ch) if ch else 0.0
    up=down=0
    for i in range(1,min(30,len(p)-1)):
        if p[-i]>p[-i-1]: up+=1
        elif p[-i]<p[-i-1]: down+=1
    pressure=(up-down)/30.0
    gains=[]; losses=[]
    for i in range(max(1,len(p)-15),len(p)):
        d=p[i]-p[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/len(gains) if gains else 0; al=sum(losses)/len(losses) if losses else 0
    rsi=100 if ag>0 and al==0 else (50 if al==0 else 100-(100/(1+ag/al)))
    rsi_norm=(rsi-50)/50
    def mean(n): return sum(p[-n:])/min(n,len(p))
    ema12,ema26=mean(12),mean(26); macd=(ema12-ema26)/cur*100
    signal=mean(9); macd_signal=(ema12-signal)/cur*100
    tr=[abs(p[i]-p[i-1]) for i in range(max(1,len(p)-30),len(p))]
    atr=sum(tr)/len(tr) if tr else 0; atr_pct=atr/cur*100 if cur else 0; atr_move=(p[-1]-p[-2])/atr if atr else 0
    w=p[-20:]; m=sum(w)/len(w); sd=(sum((x-m)**2 for x in w)/len(w))**.5
    bb=(cur-m)/(2*sd) if sd else 0
    sma20,sma50,sma100=mean(20),mean(50),mean(100)
    trend=((sma20-sma50)/sma50*100 if sma50 else 0)*.5+((sma50-sma100)/sma100*100 if sma100 else 0)*.3+((cur-sma20)/sma20*100 if sma20 else 0)*.2
    plus=minus=0
    for i in range(max(1,len(p)-20),len(p)):
        d=p[i]-p[i-1]
        if d>0: plus+=d
        elif d<0: minus-=d
    dmi=(plus-minus)/(plus+minus) if plus+minus else 0; adx=abs(dmi)
    a=p[-20:]; b=p[-40:-20]
    rh,rl=max(a),min(a); ph,pl=max(b),min(b)
    structure=1 if rh>ph and rl>pl else (-1 if rh<ph and rl<pl else 0)
    bos=1 if cur>ph else (-1 if cur<pl else 0)
    prev_struct=1 if p[-20]>p[-40] else (-1 if p[-20]<p[-40] else 0)
    curr_struct=1 if cur>p[-20] else (-1 if cur<p[-20] else 0)
    choch=curr_struct if curr_struct!=prev_struct else 0
    look=p[-31:-1]; hi=max(look); lo=min(look)
    liquidity=1 if cur>hi else (-1 if cur<lo else 0)
    fvg=1 if p[-5]<p[-3]<p[-1] else (-1 if p[-5]>p[-3]>p[-1] else 0)
    dif=[p[i]-p[i-1] for i in range(max(1,len(p)-15),len(p))]
    mx=max((abs(x) for x in dif),default=0); ob=(max(dif,key=abs)/mx if mx else 0)
    old=[abs(p[i]-p[i-1]) for i in range(max(1,len(p)-60),max(1,len(p)-30))]
    new=[abs(p[i]-p[i-1]) for i in range(max(1,len(p)-30),len(p))]
    oa=sum(old)/len(old) if old else 0; na=sum(new)/len(new) if new else 0
    amd=0
    if oa:
        exp=na/oa
        if exp>1.3: amd=1 if momentum>0 else -1
        elif exp<.75: amd=.5 if momentum>0 else -.5
    large=0; since=300
    for i in range(max(1,len(p)-300),len(p)):
        if abs((p[i]-p[i-1])/p[i-1]*100) >= SPIKE_THRESHOLD: large+=1; since=0
        else: since=min(since+1,300)
    spike_distance=since/300; spike_frequency=min(large/10,1)
    range10=max(p[-10:])-min(p[-10:]); range50=max(p[-50:])-min(p[-50:])
    compression=1-min(range10/range50,1) if range50 else 0
    tick_accel=r1-r3
    directional_pressure=pressure*.6+dmi*.4
    f=[r1,r3,r5,r10,r20,r50,r100,r200,momentum,acceleration,volatility,pressure,rsi_norm,macd,macd_signal,atr_pct,atr_move,bb,trend,adx,dmi,structure,bos,choch,liquidity,fvg,ob,amd,spike_distance,spike_frequency,compression,tick_accel,directional_pressure]
    return [max(-5,min(5,float(x))) for x in f] if len(f)==FEATURE_COUNT else None


def historical_label(prices,timestamps,i):
    if i<210 or i>=len(prices)-1 or prices[i]==0: return None
    t0=timestamps[i]; start=prices[i]; j=i+1
    while j<len(prices):
        dt=timestamps[j]-t0
        if dt<EARLY_MIN_SECONDS: j+=1; continue
        if dt>EARLY_MAX_SECONDS: break
        move=(prices[j]-start)/start*100
        if abs(move)>=SPIKE_THRESHOLD: return 1 if move>0 else 2
        j+=1
    return 0 if j<len(prices) or timestamps[-1]-t0>=EARLY_MAX_SECONDS else None


def warmup_model(key):
    if key in history_trained: return
    data=history_data[key]
    if len(data)<500: return
    ts=[x[0] for x in data]; prices=[x[1] for x in data]
    target=max(0,MIN_TRAINING_SAMPLES-models[key]["samples"])
    if target<=0: history_trained.add(key); return
    start=210; end=len(prices)-1; step=max(1,(end-start)//max(1,min(target,1200)))
    trained=0; i=start
    while i<end and trained<target:
        try: f=calculate_features(prices[:i+1])
        except Exception as e:
            diagnostics[key]["errors"]+=1; diagnostics[key]["last_error"]=str(e); f=None
        if f is not None:
            label=historical_label(prices,ts,i)
            if label is not None: train_model(key,f,label); trained+=1
        i+=step
    history_trained.add(key); save_memory()
    logging.info("%s V5 warm-up +%d samples, total=%d",key,trained,models[key]["samples"])

# ============================================================
# HISTORICAL WARM-UP
# ============================================================
# LIVE RESULT EVALUATION
# ============================================================

def evaluate_prediction_window(pred,data,now):
    t0=pred["time"]; start=pred["price"]
    found=None
    for ts,price in data:
        dt=ts-t0
        if dt<EARLY_MIN_SECONDS: continue
        if dt>EARLY_MAX_SECONDS: break
        if start:
            move=(price-start)/start*100
            if abs(move)>=SPIKE_THRESHOLD:
                found=1 if move>0 else 2; break
    if found is None:
        return None if now-t0<EARLY_MAX_SECONDS else 0
    wanted=1 if pred["direction"]=="BUY" else 2
    return 1 if found==wanted else -1


async def evaluate_predictions(key,current_time,current_price=None):
    pending=pending_predictions[key]
    if not pending: return
    data=list(ticks_data[key]); remaining=[]; changed=False
    for pred in pending:
        result=evaluate_prediction_window(pred,data,current_time)
        if result is None:
            remaining.append(pred); continue
        changed=True
        if result==1:
            learn_stats[key]["ok"]+=1; recent_results[key].append(1)
            actual=1 if pred["direction"]=="BUY" else 2; text="WIN"
        elif result==-1:
            learn_stats[key]["fail"]+=1; recent_results[key].append(0)
            actual=2 if pred["direction"]=="BUY" else 1; text="LOSS"
        else:
            learn_stats[key]["fail"]+=1; learn_stats[key]["no_spike"]+=1; recent_results[key].append(0)
            actual=0; text="NO SPIKE"
        train_model(key,pred["features"],actual)
        logging.info("%s result=%s direction=%s confidence=%.3f",key,text,pred["direction"],pred.get("confidence",0))
    pending_predictions[key]=remaining
    if changed: save_memory()


# ============================================================
# CONFIDENCE / FILTERS
# ============================================================

def recent_winrate(key):
    data=list(recent_results[key])
    return None if len(data)<MIN_RECENT_RESULTS else sum(data)/len(data)


def adaptive_confidence(key):
    # V5 diagnostic mode: 40% threshold.
    return 0.40


def signal_allowed(key,predicted_class,confidence,features):
    # Diagnostic mode: disable V4 direction and recent-win-rate gates.
    # This isolates whether the confidence threshold was too strict.
    if models[key]["samples"] < MIN_TRAINING_SAMPLES:
        diagnostics[key]["blocked_training"] += 1
        return False
    if predicted_class == 0:
        return False
    if confidence < adaptive_confidence(key):
        diagnostics[key]["blocked_confidence"] += 1
        return False
    if time.time() - last_signal_time[key] < COOLDOWN_SECONDS:
        diagnostics[key]["blocked_cooldown"] += 1
        return False
    return True


# ============================================================
# TELEGRAM SIGNAL
# ============================================================

async def send_signal(app, key, direction, confidence, probabilities):
    info = INDICES[key]
    wr = recent_winrate(key)
    wr_text = "not enough data" if wr is None else f"{wr * 100:.1f}%"

    if direction == "BUY":
        action = "BUY NOW 🟢"
        move = "UP SPIKE 📈"
    else:
        action = "SELL NOW 🔴"
        move = "DOWN SPIKE 📉"

    text = (
        "🧠🔥 AI MANIAC V5\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊 {info['name']}\n"
        f"⚡ {action}\n"
        f"🎯 {move}\n"
        "⏱️ Expected: 60–120 sec after signal\n"
        f"🧠 Model confidence: {confidence * 100:.1f}%\n"
        f"📚 Training samples: {models[key]['samples']}\n"
        f"📈 Recent win rate: {wr_text}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"UP: {probabilities[1] * 100:.1f}%\n"
        f"DOWN: {probabilities[2] * 100:.1f}%\n"
        f"NO SPIKE: {probabilities[0] * 100:.1f}%\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Prediction only — guaranteed profit биш."
    )

    for cid in list(chat_ids):
        try:
            await app.bot.send_message(chat_id=cid, text=text)
        except Exception as e:
            logging.error("Telegram send error: %s", e)


# ============================================================
# DERIV WEBSOCKET
# ============================================================

async def deriv_ws(symbol, key, app):
    uri = "wss://ws.binaryws.com/websockets/v3?app_id=1089"

    async with websockets.connect(
        uri,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=10,
    ) as ws:

        # 5000 ticks for historical warm-up.
        await ws.send(json.dumps({
            "ticks_history": symbol,
            "count": WARMUP_HISTORY,
            "end": "latest",
            "style": "ticks",
        }))

        # Subscribe to live ticks.
        await ws.send(json.dumps({
            "ticks": symbol,
            "subscribe": 1,
        }))

        logging.info("%s CONNECTED", key)

        while key in active:
            raw = await ws.recv()
            msg = json.loads(raw)

            # ---------------- HISTORY ----------------
            if "history" in msg:
                prices = msg["history"].get("prices", [])
                times = msg["history"].get("times", [])

                history_data[key] = []

                for i, price in enumerate(prices):
                    try:
                        ts = float(times[i]) if i < len(times) else 0.0
                        history_data[key].append((ts, float(price)))
                    except Exception:
                        pass

                if history_data[key]:
                    # Seed live buffer with the latest 500.
                    for item in history_data[key][-LIVE_HISTORY:]:
                        ticks_data[key].append(item)

                # Warm-up is CPU work; run it in a thread so Telegram
                # remains responsive.
                await asyncio.to_thread(warmup_model, key)

                logging.info(
                    "%s history loaded: %d ticks",
                    key, len(history_data[key])
                )

            # ---------------- LIVE TICK ----------------
            if "tick" in msg:
                tick = msg["tick"]

                try:
                    price = float(tick["quote"])
                    timestamp = float(tick.get("epoch", time.time()))
                except Exception:
                    continue

                ticks_data[key].append((timestamp, price))

                prices = [x[1] for x in ticks_data[key]]

                if len(prices) < 210:
                    continue

                await evaluate_predictions(
                    key,
                    timestamp,
                    price,
                )

                try:
                    features = calculate_features(prices)
                except Exception as e:
                    diagnostics[key]["errors"] += 1
                    diagnostics[key]["last_error"] = str(e)
                    logging.error("%s feature error: %s", key, e)
                    continue

                if features is None:
                    continue

                diagnostics[key]["features"] += 1

                predicted_class, confidence, probabilities = predict(
                    key,
                    features
                )
                last_probabilities[key] = probabilities
                last_predicted_class[key] = predicted_class
                diagnostics[key]["last_confidence"] = confidence
                if predicted_class != 0:
                    diagnostics[key]["candidates"] += 1

                if not signal_allowed(
                    key,
                    predicted_class,
                    confidence,
                    features,
                ):
                    continue

                direction = "BUY" if predicted_class == 1 else "SELL"

                # Save the prediction so the model can learn from
                # the actual result after 60 seconds.
                pending_predictions[key].append({
                    "time": timestamp,
                    "price": price,
                    "direction": direction,
                    "features": features,
                    "confidence": confidence,
                })

                last_signal_time[key] = time.time()
                diagnostics[key]["signals"] += 1

                await send_signal(
                    app,
                    key,
                    direction,
                    confidence,
                    probabilities,
                )


async def start_ws(symbol, key, app):
    while key in active:
        try:
            await deriv_ws(symbol, key, app)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logging.error("%s websocket error: %s", key, e)
            if key in active:
                await asyncio.sleep(5)


# ============================================================
# KEEP ALIVE
# ============================================================

def keep_alive():
    port = int(os.environ.get("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"AI MANIAC V5 LIVE")

        def log_message(self, *args):
            return

    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


threading.Thread(target=keep_alive, daemon=True).start()


# ============================================================
# TELEGRAM UI
# ============================================================

def index_buttons():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{'✅' if k in active else '❌'} {v['name']}",
                callback_data=k,
            )
        ]
        for k, v in INDICES.items()
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)

    # /start automatically activates all 7.
    for key in INDICES:
        active.add(key)

        if key not in ws_tasks or ws_tasks[key].done():
            ws_tasks[key] = asyncio.create_task(
                start_ws(
                    INDICES[key]["symbol"],
                    key,
                    context.application,
                )
            )

    await update.message.reply_text(
        "🧠🔥 AI MANIAC V5\n\n"
        "ЗӨВХӨН 7 BOOM / CRASH INDEX\n\n"
        "✅ 7 индекс бүгд идэвхжлээ.\n"
        "📡 Live tick data авч байна.\n"
        "🧠 AI historical data-аар warm-up хийнэ.\n"
        "🔄 Шинэ prediction бүрийн дараа өөрийгөө шинэчилнэ.\n"
        "💾 Сурсан жинг memory-д хадгална.\n"
        "🎯 Diagnostic signal threshold: 40%.\n\n"
        "⚠️ Эхлээд суралцана. Шууд ашиг амлахгүй.",
        reply_markup=index_buttons(),
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    key = q.data
    chat_ids.add(q.message.chat.id)

    if key in active:
        active.remove(key)

        task = ws_tasks.get(key)
        if task and not task.done():
            task.cancel()

        await q.message.reply_text(
            f"❌ {INDICES[key]['name']} унтарлаа."
        )
    else:
        active.add(key)

        if key not in ws_tasks or ws_tasks[key].done():
            ws_tasks[key] = asyncio.create_task(
                start_ws(
                    INDICES[key]["symbol"],
                    key,
                    context.application,
                )
            )

        await q.message.reply_text(
            f"✅ {INDICES[key]['name']} идэвхжлээ!\n"
            f"🧠 Training: {models[key]['samples']}\n"
            f"🎯 Signal threshold: {adaptive_confidence(key) * 100:.0f}%"
        )

    try:
        await q.edit_message_reply_markup(
            reply_markup=index_buttons()
        )
    except Exception:
        pass


# ============================================================
# STATUS
# ============================================================

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    now = time.time()

    msg = "🧠🔥 AI MANIAC V5 STATUS\n━━━━━━━━━━━━━━━━━━\n\n"

    for key, info in INDICES.items():
        m = models[key]
        ok = learn_stats[key]["ok"]
        fail = learn_stats[key]["fail"]
        total = ok + fail

        wr = (ok / total * 100.0) if total else 0.0

        msg += (
            f"{'🟢' if key in active else '⚪'} {info['name']}\n"
            f"Ticks: {len(ticks_data[key])}/{LIVE_HISTORY}\n"
            f"AI Training: {m['samples']}\n"
            f"WIN: {ok} | LOSS: {fail}\n"
            f"WinRate: {wr:.1f}%\n"
            f"Pending: {len(pending_predictions[key])} | Candidates: {diagnostics[key]['candidates']}\n"
            f"Prob UP: {last_probabilities[key][1]*100:.1f}% | DOWN: {last_probabilities[key][2]*100:.1f}% | NO SPIKE: {last_probabilities[key][0]*100:.1f}%\n"
            f"Signals: {diagnostics[key]['signals']} | Errors: {diagnostics[key]['errors']}\n\n"
        )

    msg += (
        "━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Active: {len(active)}/7\n"
        "🧠 Online learning: ON\n"
        "💾 Persistent memory: ON\n"
        "🎯 Diagnostic confidence: 40%\n"
        "⚠️ WinRate нь бодит үр дүнгээр тооцогдоно."
    )

    await update.message.reply_text(msg)


# ============================================================
# AI STATUS
# ============================================================

async def ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)

    msg = "🧠🔥 AI BRAIN\n━━━━━━━━━━━━━━━━━━\n"

    for key, info in INDICES.items():
        msg += (
            f"\n{info['name']}\n"
            f"Learned: {models[key]['samples']}\n"
            f"Live WIN: {learn_stats[key]['ok']}\n"
            f"Live LOSS: {learn_stats[key]['fail']}\n"
        )

    msg += (
        "\n━━━━━━━━━━━━━━━━━━\n"
        "7 индекс тус бүр өөрийн model-той."
    )

    await update.message.reply_text(msg)


# ============================================================
# TEST
# ============================================================

async def test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)

    await update.message.reply_text(
        "🧪 AI MANIAC V5 TEST\n\n"
        "Telegram: OK ✅\n"
        "7 Index engine: OK ✅\n"
        "Historical warm-up: ON ✅\n"
        "Online learning: ON ✅\n"
        "Persistent memory: ON ✅\n"
        "Diagnostic 40% signal filter: ON ✅\n"
        "Auto /start activation: ON ✅\n\n"
        "⚠️ TEST MESSAGE ONLY"
    )


# ============================================================
# MAIN
# ============================================================

load_memory()

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("ai", ai_cmd))
app.add_handler(CommandHandler("test", test_cmd))
app.add_handler(CallbackQueryHandler(button))

if __name__ == "__main__":
    logging.info("🔥 AI MANIAC V5 STARTING")
    logging.info("ONLY 7 BOOM/CRASH INDICES")
    app.run_polling()
