import os,json,time,math,asyncio,logging,threading
from http.server import BaseHTTPRequestHandler,HTTPServer
from collections import deque
import websockets
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder,CommandHandler,CallbackQueryHandler,ContextTypes

logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s")
TOKEN=os.getenv("TELEGRAM_TOKEN")
if not TOKEN: raise RuntimeError("TELEGRAM_TOKEN environment variable is missing")

# ЗӨВХӨН ЭДГЭЭР 7
INDICES={
"BOOM1000":{"name":"Boom 1000 Index","type":"BOOM"},
"BOOM500":{"name":"Boom 500 Index","type":"BOOM"},
"BOOM600":{"name":"Boom 600 Index","type":"BOOM"},
"BOOM900":{"name":"Boom 900 Index","type":"BOOM"},
"CRASH1000":{"name":"Crash 1000 Index","type":"CRASH"},
"CRASH500":{"name":"Crash 500 Index","type":"CRASH"},
"CRASH900":{"name":"Crash 900 Index","type":"CRASH"}}

DERIV_SYMBOLS={k:None for k in INDICES}

LIVE_HISTORY=500
WARMUP_HISTORY=5000
EARLY_MIN_SECONDS=60
EARLY_MAX_SECONDS=120
SPIKE_THRESHOLD=.10
SIGNAL_CONFIDENCE=.40
COOLDOWN_SECONDS=120
LEARNING_RATE=.025
L2=.0003
MIN_TRAINING_SAMPLES=300
FEATURE_COUNT=33
CLASS_COUNT=3
RECENT_RESULTS=100
MEMORY_FILE="ai_memory_v5_fixed.json"

active=set()
chat_ids=set()

ticks_data={
    k:deque(maxlen=LIVE_HISTORY)
    for k in INDICES
}

history_data={
    k:[]
    for k in INDICES
}

models={}
stats={}
recent={}
pending={}
last_signal={}
tasks={}
trained=set()

last_probs={
    k:[1/3,1/3,1/3]
    for k in INDICES
}

last_class={
    k:0
    for k in INDICES
}

diag={}

for k in INDICES:

    models[k]={
        "weights":[
            [0.0]*FEATURE_COUNT
            for _ in range(3)
        ],
        "bias":[0.0,0.0,0.0],
        "samples":0
    }

    stats[k]={
        "ok":0,
        "fail":0,
        "training":0,
        "no_spike":0
    }

    recent[k]=deque(
        maxlen=RECENT_RESULTS
    )

    pending[k]=[]

    last_signal[k]=0.0

    diag[k]={
        "ticks":0,
        "features":0,
        "candidates":0,
        "signals":0,
        "blocked_confidence":0,
        "blocked_cooldown":0,
        "blocked_training":0,
        "errors":0,
        "last_error":"",
        "last_tick":0.0,
        "last_confidence":0.0,
        "connected":0,
        "history":0,
        "subscribed":0,
        "last_msg_type":"",
        "last_block_reason":"",
        "telegram_sent":0,
        "telegram_errors":0,
        "last_telegram_error":""
    }


# ============================================================
# MEMORY SAVE
# ============================================================

def save_memory():

    try:

        with open(
            MEMORY_FILE+".tmp",
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                {
                    "models":models,
                    "stats":stats,
                    "recent":{
                        k:list(v)
                        for k,v in recent.items()
                    }
                },
                f
            )

        os.replace(
            MEMORY_FILE+".tmp",
            MEMORY_FILE
        )

    except Exception as e:

        logging.error(
            "memory save: %s",
            e
        )


# ============================================================
# MEMORY LOAD
# ============================================================

def load_memory():

    if not os.path.exists(
        MEMORY_FILE
    ):
        return

    try:

        with open(
            MEMORY_FILE,
            encoding="utf-8"
        ) as f:

            data=json.load(f)

        for k in INDICES:

            model=(
                data
                .get("models",{})
                .get(k)
            )

            if (
                model
                and len(
                    model.get(
                        "weights",
                        []
                    )
                )==3
                and all(
                    len(row)==FEATURE_COUNT
                    for row in model["weights"]
                )
            ):

                models[k]=model

            if k in data.get(
                "stats",
                {}
            ):

                stats[k].update(
                    data["stats"][k]
                )

            if k in data.get(
                "recent",
                {}
            ):

                recent[k]=deque(
                    data["recent"][k],
                    maxlen=RECENT_RESULTS
                )

        logging.info(
            "AI memory loaded"
        )

    except Exception as e:

        logging.error(
            "memory load: %s",
            e
        )


# ============================================================
# SOFTMAX
# ============================================================

def softmax(scores):

    m=max(scores)

    ex=[
        math.exp(
            max(
                -50,
                min(
                    50,
                    x-m
                )
            )
        )
        for x in scores
    ]

    z=sum(ex)

    if z:

        return [
            x/z
            for x in ex
        ]

    return [
        1/3,
        1/3,
        1/3
    ]


# ============================================================
# DOT
# ============================================================

def dot(a,b):

    return sum(
        x*y
        for x,y in zip(a,b)
    )


# ============================================================
# PREDICTION
# ============================================================

def predict(k,f):

    model=models[k]

    scores=[
        dot(
            model["weights"][c],
            f
        )
        +
        model["bias"][c]
        for c in range(3)
    ]

    probabilities=softmax(
        scores
    )

    cls=max(
        range(3),
        key=lambda i:probabilities[i]
    )

    return (
        cls,
        probabilities[cls],
        probabilities
    )


# ============================================================
# ONLINE TRAINING
# ============================================================

def train_model(k,f,y):

    model=models[k]

    probabilities=softmax(
        [
            dot(
                model["weights"][c],
                f
            )
            +
            model["bias"][c]
            for c in range(3)
        ]
    )

    for c in range(3):

        error=(
            (1.0 if c==y else 0.0)
            -
            probabilities[c]
        )

        for i,x in enumerate(f):

            model["weights"][c][i]+=(
                LEARNING_RATE*
                (
                    error*x
                    -
                    L2*
                    model["weights"][c][i]
                )
            )

        model["bias"][c]+=(
            LEARNING_RATE*error
        )

    model["samples"]+=1
    stats[k]["training"]+=1


# ============================================================
# FEATURES
# ============================================================

def features(prices):

    if len(prices)<210:
        return None

    p=[
        float(x)
        for x in prices
    ]

    current=p[-1]

    if not current:
        return None

    def ret(n):

        old=p[-1-n]

        if old:

            return (
                (current-old)
                /
                old
                *
                100
            )

        return 0

    r1=ret(1)
    r3=ret(3)
    r5=ret(5)
    r10=ret(10)
    r20=ret(20)
    r50=ret(50)
    r100=ret(100)
    r200=ret(200)

    momentum=(
        .30*r5
        +
        .25*r10
        +
        .20*r20
        +
        .15*r50
        +
        .10*r100
    )

    acceleration=r5-r20

    q=p[-50:]

    changes=[
        abs(
            (q[i]-q[i-1])
            /
            q[i-1]
            *
            100
        )
        for i in range(
            1,
            len(q)
        )
        if q[i-1]
    ]

    volatility=(
        sum(changes)/len(changes)
        if changes
        else 0
    )

    up=sum(
        p[-i]>p[-i-1]
        for i in range(1,30)
    )

    down=sum(
        p[-i]<p[-i-1]
        for i in range(1,30)
    )

    pressure=(
        up-down
    )/30

    gains=[]
    losses=[]

    for i in range(
        max(1,len(p)-15),
        len(p)
    ):

        d=p[i]-p[i-1]

        gains.append(
            max(d,0)
        )

        losses.append(
            max(-d,0)
        )

    average_gain=(
        sum(gains)/len(gains)
        if gains
        else 0
    )

    average_loss=(
        sum(losses)/len(losses)
        if losses
        else 0
    )

    if average_gain and not average_loss:

        rsi=100

    elif not average_loss:

        rsi=50

    else:

        rsi=(
            100
            -
            100/
            (
                1
                +
                average_gain/
                average_loss
            )
        )

    rsi_normalized=(
        rsi-50
    )/50

    mean=lambda n:(
        sum(p[-n:])
        /
        min(n,len(p))
    )

    e12=mean(12)
    e26=mean(26)

    macd=(
        e12-e26
    )/current*100

    macd_signal=(
        e12-mean(9)
    )/current*100

    true_ranges=[
        abs(
            p[i]-p[i-1]
        )
        for i in range(
            max(1,len(p)-30),
            len(p)
        )
    ]

    atr=(
        sum(true_ranges)/len(true_ranges)
        if true_ranges
        else 0
    )

    atr_percent=(
        atr/current*100
    )

    atr_momentum=(
        (p[-1]-p[-2])/atr
        if atr
        else 0
    )

    window20=p[-20:]

    average20=(
        sum(window20)
        /
        len(window20)
    )

    sd=(
        sum(
            (x-average20)**2
            for x in window20
        )
        /
        len(window20)
    )**.5

    bollinger=(
        (current-average20)
        /
        (2*sd)
        if sd
        else 0
    )

    sma20=mean(20)
    sma50=mean(50)
    sma100=mean(100)

    trend=(
        .5*
        (
            (sma20-sma50)
            /
            sma50
            *
            100
            if sma50
            else 0
        )
        +
        .3*
        (
            (sma50-sma100)
            /
            sma100
            *
            100
            if sma100
            else 0
        )
        +
        .2*
        (
            (current-sma20)
            /
            sma20
            *
            100
            if sma20
            else 0
        )
    )

    plus=0
    minus=0

    for i in range(
        max(1,len(p)-20),
        len(p)
    ):

        d=p[i]-p[i-1]

        if d>0:
            plus+=d

        elif d<0:
            minus-=d

    dmi=(
        (plus-minus)
        /
        (plus+minus)
        if plus+minus
        else 0
    )

    adx=abs(dmi)

    a=p[-20:]
    b=p[-40:-20]

    recent_high=max(a)
    recent_low=min(a)

    previous_high=max(b)
    previous_low=min(b)

    structure=(
        1
        if (
            recent_high>previous_high
            and
            recent_low>previous_low
        )
        else (
            -1
            if (
                recent_high<previous_high
                and
                recent_low<previous_low
            )
            else 0
        )
    )

    bos=(
        1
        if current>previous_high
        else (
            -1
            if current<previous_low
            else 0
        )
    )

    previous_structure=(
        1
        if p[-20]>p[-40]
        else (
            -1
            if p[-20]<p[-40]
            else 0
        )
    )

    current_structure=(
        1
        if current>p[-20]
        else (
            -1
            if current<p[-20]
            else 0
        )
    )

    choch=(
        current_structure
        if current_structure!=previous_structure
        else 0
    )

    lookback=p[-31:-1]

    highest=max(lookback)
    lowest=min(lookback)

    liquidity=(
        1
        if current>highest
        else (
            -1
            if current<lowest
            else 0
        )
    )

    fvg=(
        1
        if p[-5]<p[-3]<p[-1]
        else (
            -1
            if p[-5]>p[-3]>p[-1]
            else 0
        )
    )

    differences=[
        p[i]-p[i-1]
        for i in range(
            max(1,len(p)-15),
            len(p)
        )
    ]

    maximum=max(
        (
            abs(x)
            for x in differences
        ),
        default=0
    )

    order_block=(
        max(
            differences,
            key=abs
        )/maximum
        if maximum
        else 0
    )

    old_moves=[
        abs(
            p[i]-p[i-1]
        )
        for i in range(
            max(1,len(p)-60),
            max(1,len(p)-30)
        )
    ]

    new_moves=[
        abs(
            p[i]-p[i-1]
        )
        for i in range(
            max(1,len(p)-30),
            len(p)
        )
    ]

    old_average=(
        sum(old_moves)/len(old_moves)
        if old_moves
        else 0
    )

    new_average=(
        sum(new_moves)/len(new_moves)
        if new_moves
        else 0
    )

    amd=0

    if old_average:

        ratio=(
            new_average/
            old_average
        )

        if ratio>1.3:

            amd=(
                1
                if momentum>0
                else -1
            )

        elif ratio<.75:

            amd=(
                .5
                if momentum>0
                else -.5
            )

    large_moves=0
    since_spike=300

    for i in range(
        max(1,len(p)-300),
        len(p)
    ):

        move=(
            (p[i]-p[i-1])
            /
            p[i-1]
            *
            100
        )

        if abs(move)>=SPIKE_THRESHOLD:

            large_moves+=1
            since_spike=0

        else:

            since_spike=min(
                since_spike+1,
                300
            )

    distance=(
        since_spike/300
    )

    frequency=min(
        large_moves/10,
        1
    )

    range10=(
        max(p[-10:])
        -
        min(p[-10:])
    )

    range50=(
        max(p[-50:])
        -
        min(p[-50:])
    )

    compression=(
        1-
        min(
            range10/range50,
            1
        )
        if range50
        else 0
    )

    f=[
        r1,
        r3,
        r5,
        r10,
        r20,
        r50,
        r100,
        r200,
        momentum,
        acceleration,
        volatility,
        pressure,
        rsi_normalized,
        macd,
        macd_signal,
        atr_percent,
        atr_momentum,
        bollinger,
        trend,
        adx,
        dmi,
        structure,
        bos,
        choch,
        liquidity,
        fvg,
        order_block,
        amd,
        distance,
        frequency,
        compression,
        r1-r3,
        pressure*.6+dmi*.4
    ]

    return[
        max(
            -5,
            min(
                5,
                float(x)
            )
        )
        for x in f
    ]


# ============================================================
# LABEL
# ============================================================

def label_at(
    prices,
    times,
    i
):

    if (
        i<210
        or
        i>=len(prices)-1
        or
        not prices[i]
    ):
        return None

    start_time=times[i]
    start_price=prices[i]

    for j in range(
        i+1,
        len(prices)
    ):

        dt=(
            times[j]-
            start_time
        )

        if dt<EARLY_MIN_SECONDS:
            continue

        if dt>EARLY_MAX_SECONDS:
            break

        move=(
            (prices[j]-start_price)
            /
            start_price
            *
            100
        )

        if abs(move)>=SPIKE_THRESHOLD:

            return(
                1
                if move>0
                else 2
            )

    if(
        times[-1]-start_time
        >=EARLY_MAX_SECONDS
    ):

        return 0

    return None


# ============================================================
# WARM-UP
# ============================================================

def warmup(k):

    if k in trained:
        return

    data=history_data[k]

    if len(data)<500:
        return

    times=[
        x[0]
        for x in data
    ]

    prices=[
        x[1]
        for x in data
    ]

    pools=[
        [],
        [],
        []
    ]

    step=max(
        1,
        (len(prices)-211)//1200
    )

    for i in range(
        210,
        len(prices)-1,
        step
    ):

        y=label_at(
            prices,
            times,
            i
        )

        if y is None:
            continue

        try:

            f=features(
                prices[:i+1]
            )

        except Exception as e:

            diag[k]["errors"]+=1
            diag[k]["last_error"]=str(e)
            f=None

        if f is not None:

            pools[y].append(f)

    if not any(pools):
        return

    pairs=[]

    maximum=max(
        len(x)
        for x in pools
    )

    for i in range(maximum):

        for y in range(3):

            if pools[y]:

                pairs.append(
                    (
                        y,
                        pools[y][
                            i%len(pools[y])
                        ]
                    )
                )

            if len(pairs)>=MIN_TRAINING_SAMPLES:
                break

        if len(pairs)>=MIN_TRAINING_SAMPLES:
            break

    if len(pairs)<MIN_TRAINING_SAMPLES:

        for y,pool in enumerate(pools):

            for f in pool:

                pairs.append(
                    (
                        y,
                        f
                    )
                )

                if len(pairs)>=MIN_TRAINING_SAMPLES:
                    break

            if len(pairs)>=MIN_TRAINING_SAMPLES:
                break

    for y,f in pairs:

        train_model(
            k,
            f,
            y
        )

    trained.add(k)

    save_memory()

    logging.info(
        "%s warm-up labels NO=%d UP=%d DOWN=%d trained=%d",
        k,
        len(pools[0]),
        len(pools[1]),
        len(pools[2]),
        models[k]["samples"]
    )


# ============================================================
# RESULT EVALUATION
# ============================================================

def eval_one(
    prediction,
    data,
    now
):

    start_time=prediction["time"]
    start_price=prediction["price"]

    found=None

    for timestamp,price in data:

        dt=(
            timestamp-
            start_time
        )

        if dt<60:
            continue

        if dt>120:
            break

        move=(
            (price-start_price)
            /
            start_price
            *
            100
        )

        if abs(move)>=SPIKE_THRESHOLD:

            found=(
                1
                if move>0
                else 2
            )

            break

    if found is None:

        return(
            None
            if now-start_time<120
            else 0
        )

    wanted=(
        1
        if prediction["direction"]=="BUY"
        else 2
    )

    return(
        1
        if found==wanted
        else -1
    )


# ============================================================
# ONLINE EVALUATION
# ============================================================

async def evaluate(k,now):

    if not pending[k]:
        return

    data=list(
        ticks_data[k]
    )

    remaining=[]
    changed=False

    for prediction in pending[k]:

        result=eval_one(
            prediction,
            data,
            now
        )

        if result is None:

            remaining.append(
                prediction
            )

            continue

        changed=True

        if result==1:

            stats[k]["ok"]+=1
            recent[k].append(1)

            actual=(
                1
                if prediction["direction"]=="BUY"
                else 2
            )

        elif result==-1:

            stats[k]["fail"]+=1
            recent[k].append(0)

            actual=(
                2
                if prediction["direction"]=="BUY"
                else 1
            )

        else:

            stats[k]["fail"]+=1
            stats[k]["no_spike"]+=1
            recent[k].append(0)

            actual=0

        train_model(
            k,
            prediction["features"],
            actual
        )

    pending[k]=remaining

    if changed:
        save_memory()


# ============================================================
# TELEGRAM SIGNAL
# ============================================================

async def send_signal(
    app,
    k,
    direction,
    confidence,
    probabilities
):

    info=INDICES[k]

    if direction=="BUY":

        action="BUY NOW 🟢"
        spike="UP SPIKE 📈"

    else:

        action="SELL NOW 🔴"
        spike="DOWN SPIKE 📉"

    text=(
        "🧠🔥 AI MANIAC V5 FIX\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊 {info['name']}\n"
        f"⚡ {action}\n"
        f"🎯 {spike}\n"
        "⏱️ Expected movement: 60–120 sec\n"
        f"🧠 Confidence: {confidence*100:.1f}%\n"
        f"📚 Training: {models[k]['samples']}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📈 UP: {probabilities[1]*100:.1f}%\n"
        f"📉 DOWN: {probabilities[2]*100:.1f}%\n"
        f"⚪ NO SPIKE: {probabilities[0]*100:.1f}%\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Prediction only — guaranteed profit биш."
    )

    for chat_id in list(chat_ids):

        try:

            await app.bot.send_message(
                chat_id=chat_id,
                text=text
            )

            diag[k]["telegram_sent"]+=1

        except Exception as e:

            diag[k]["telegram_errors"]+=1
            diag[k]["last_telegram_error"]=str(e)

            logging.error(
                "Telegram send error: %s",
                e
            )


# ============================================================
# SYMBOL NORMALIZATION
# ============================================================

def normalize_symbol(value):

    return "".join(
        ch
        for ch in str(value).upper()
        if ch.isalnum()
    )


# ============================================================
# SYMBOL MATCH
# ============================================================

def symbol_matches(
    key,
    info,
    item
):

    symbol=(
        item.get("underlying_symbol")
        or
        item.get("symbol")
        or
        ""
    )

    name=(
        item.get("underlying_symbol_name")
        or
        item.get("display_name")
        or
        ""
    )

    target=normalize_symbol(
        info["name"]
    )

    n=normalize_symbol(
        name
    )

    sym=normalize_symbol(
        symbol
    )

    key_normalized=normalize_symbol(
        key
    )

    name_without_index=normalize_symbol(
        info["name"].replace(
            "Index",
            ""
        )
    )

    if n==target:
        return True

    if n==name_without_index:
        return True

    if sym==key_normalized:
        return True

    family=info["type"]

    number=(
        key
        .replace("BOOM","")
        .replace("CRASH","")
    )

    if(
        family in n
        and
        number in n
    ):
        return True

    if(
        family in sym
        and
        number in sym
    ):
        return True

    return False


# ============================================================
# ACTIVE SYMBOLS
# ============================================================

async def get_active_symbols(ws):

    request={
        "active_symbols":"brief",
        "req_id":9001
    }

    await ws.send(
        json.dumps(request)
    )

    while True:

        raw=await ws.recv()

        message=json.loads(raw)

        message_type=message.get(
            "msg_type",
            ""
        )

        if(
            message_type=="error"
            or
            "error" in message
        ):

            error=message.get(
                "error",
                {}
            )

            raise RuntimeError(
                "active_symbols: "
                +
                str(
                    error.get(
                        "message",
                        error
                    )
                )
            )

        if(
            message_type
            !=
            "active_symbols"
        ):
            continue

        items=(
            message.get(
                "active_symbols"
            )
            or []
        )

        found={}

        relevant=[]

        for item in items:

            symbol=(
                item.get(
                    "underlying_symbol"
                )
                or
                item.get(
                    "symbol"
                )
            )

            name=(
                item.get(
                    "underlying_symbol_name"
                )
                or
                item.get(
                    "display_name"
                )
                or
                ""
            )

            if not symbol:
                continue

            symbol=str(symbol)
            name=str(name)

            if(
                "BOOM"
                in normalize_symbol(name)
                or
                "CRASH"
                in normalize_symbol(name)
            ):

                relevant.append(
                    f"{name}={symbol}"
                )

            for key,info in INDICES.items():

                if(
                    key not in found
                    and
                    symbol_matches(
                        key,
                        info,
                        item
                    )
                ):

                    found[key]=symbol

        missing=[
            k
            for k in INDICES
            if k not in found
        ]

        if missing:

            preview="; ".join(
                relevant[:40]
            )

            if not preview:
                preview=(
                    "no Boom/Crash "
                    "entries returned"
                )

            raise RuntimeError(
                "Live symbol not resolved: "
                +
                ", ".join(missing)
                +
                " | Deriv active_symbols: "
                +
                preview
            )

        DERIV_SYMBOLS.update(
            found
        )

        logging.info(
            "REAL DERIV SYMBOL MAP: %s",
            found
        )

        return found


# ============================================================
# DERIV WEBSOCKET
# ============================================================

async def deriv_ws(k,app):

    uri=(
        "wss://ws.derivws.com/"
        "websockets/v3?app_id=1089"
    )

    diag[k]["last_error"]=""

    try:

        async with websockets.connect(
            uri,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10
        ) as ws:

            diag[k]["connected"]=1
            diag[k]["last_msg_type"]="CONNECTED"

            logging.info(
                "%s WebSocket CONNECTED",
                k
            )

            # ------------------------------------------------
            # REAL SYMBOL FROM DERIV
            # ------------------------------------------------

            symbols=await get_active_symbols(
                ws
            )

            symbol=symbols[k]

            DERIV_SYMBOLS[k]=symbol

            diag[k]["last_msg_type"]="SYMBOL_RESOLVED"

            logging.info(
                "%s REAL SYMBOL = %s",
                k,
                symbol
            )

            # ------------------------------------------------
            # HISTORY
            # ------------------------------------------------

            history_request={
                "ticks_history":symbol,
                "count":WARMUP_HISTORY,
                "end":"latest",
                "style":"ticks",
                "req_id":1000
            }

            await ws.send(
                json.dumps(
                    history_request
                )
            )

            diag[k]["last_msg_type"]=(
                "HISTORY_REQUESTED"
            )

            # ------------------------------------------------
            # LIVE SUBSCRIPTION
            # ------------------------------------------------

            live_request={
                "ticks":symbol,
                "subscribe":1,
                "req_id":2000
            }

            logging.info(
                "%s LIVE SUBSCRIBE: %s",
                k,
                live_request
            )

            await ws.send(
                json.dumps(
                    live_request
                )
            )

            diag[k]["last_msg_type"]=(
                "SUBSCRIBE_REQUESTED"
            )

            # ------------------------------------------------
            # MESSAGE LOOP
            # ------------------------------------------------

            while k in active:

                raw=await ws.recv()

                message=json.loads(raw)

                message_type=message.get(
                    "msg_type",
                    ""
                )

                # --------------------------------------------
                # ERROR
                # --------------------------------------------

                if(
                    message_type=="error"
                    or
                    "error" in message
                ):

                    error=message.get(
                        "error",
                        {}
                    )

                    error_message=str(
                        error.get(
                            "message",
                            error
                        )
                    )

                    diag[k]["errors"]+=1

                    diag[k]["last_error"]=(
                        error_message
                    )

                    diag[k]["last_msg_type"]=(
                        "ERROR"
                    )

                    logging.error(
                        "%s DERIV ERROR: %s",
                        k,
                        error_message
                    )

                    # A subscription error is fatal for this
                    # connection. Reconnect instead of looping
                    # forever on the same invalid request.
                    if(
                        "invalid"
                        in error_message.lower()
                        or
                        "subscribe"
                        in error_message.lower()
                    ):

                        raise RuntimeError(
                            "Live tick subscription failed: "
                            +
                            error_message
                        )

                    continue

                # --------------------------------------------
                # HISTORY
                # --------------------------------------------

                if(
                    message_type=="history"
                    and
                    message.get("history")
                ):

                    history=message["history"]

                    prices=history.get(
                        "prices",
                        []
                    )

                    times=history.get(
                        "times",
                        []
                    )

                    history_data[k]=[
                        (
                            float(times[i]),
                            float(price)
                        )
                        for i,price
                        in enumerate(prices)
                        if i<len(times)
                    ]

                    diag[k]["history"]=len(
                        history_data[k]
                    )

                    diag[k]["last_msg_type"]=(
                        "HISTORY"
                    )

                    ticks_data[k].clear()

                    for item in history_data[k][
                        -LIVE_HISTORY:
                    ]:

                        ticks_data[k].append(
                            item
                        )

                    await asyncio.to_thread(
                        warmup,
                        k
                    )

                    continue

                # --------------------------------------------
                # LIVE TICK
                # --------------------------------------------

                if(
                    message_type=="tick"
                    and
                    message.get("tick")
                ):

                    tick=message["tick"]

                    try:

                        price=float(
                            tick["quote"]
                        )

                        timestamp=float(
                            tick.get(
                                "epoch",
                                time.time()
                            )
                        )

                    except Exception as e:

                        diag[k]["errors"]+=1

                        diag[k]["last_error"]=(
                            f"tick parse: {e}"
                        )

                        diag[k]["last_msg_type"]=(
                            "TICK_PARSE_ERROR"
                        )

                        continue

                    # ----------------------------------------
                    # LIVE TICK CONFIRMED
                    # ----------------------------------------

                    diag[k]["subscribed"]=1

                    diag[k]["ticks"]+=1

                    diag[k]["last_tick"]=timestamp

                    diag[k]["last_msg_type"]=(
                        "LIVE_TICK"
                    )

                    ticks_data[k].append(
                        (
                            timestamp,
                            price
                        )
                    )

                    prices=[
                        x[1]
                        for x in ticks_data[k]
                    ]

                    if len(prices)<210:
                        continue

                    # ----------------------------------------
                    # ONLINE LEARNING
                    # ----------------------------------------

                    await evaluate(
                        k,
                        timestamp
                    )

                    # ----------------------------------------
                    # FEATURES
                    # ----------------------------------------

                    try:

                        f=features(
                            prices
                        )

                    except Exception as e:

                        diag[k]["errors"]+=1

                        diag[k]["last_error"]=str(e)

                        diag[k]["last_msg_type"]=(
                            "FEATURE_ERROR"
                        )

                        continue

                    if f is None:
                        continue

                    diag[k]["features"]+=1

                    # ----------------------------------------
                    # PREDICTION
                    # ----------------------------------------

                    cls,confidence,probabilities=(
                        predict(
                            k,
                            f
                        )
                    )

                    last_probs[k]=probabilities

                    last_class[k]=cls

                    diag[k]["last_confidence"]=(
                        confidence
                    )

                    if cls!=0:

                        diag[k]["candidates"]+=1

                    # ----------------------------------------
                    # SIGNAL FILTER
                    # ----------------------------------------

                    if not allowed(
                        k,
                        cls,
                        confidence
                    ):

                        continue

                    direction=(
                        "BUY"
                        if cls==1
                        else
                        "SELL"
                    )

                    pending[k].append(
                        {
                            "time":timestamp,
                            "price":price,
                            "direction":direction,
                            "features":f,
                            "confidence":confidence
                        }
                    )

                    last_signal[k]=time.time()

                    diag[k]["signals"]+=1

                    await send_signal(
                        app,
                        k,
                        direction,
                        confidence,
                        probabilities
                    )

    except asyncio.CancelledError:

        diag[k]["connected"]=0
        diag[k]["subscribed"]=0
        diag[k]["last_msg_type"]="STOPPED"

        raise

    except Exception as e:

        diag[k]["connected"]=0
        diag[k]["subscribed"]=0

        diag[k]["errors"]+=1

        diag[k]["last_error"]=(
            f"{type(e).__name__}: {e}"
        )

        diag[k]["last_msg_type"]=(
            "WS_EXCEPTION"
        )

        logging.error(
            "%s websocket exception: %s",
            k,
            e
        )

        raise

    finally:

        diag[k]["connected"]=0
        diag[k]["subscribed"]=0


# ============================================================
# SIGNAL FILTER
# ============================================================

def allowed(
    k,
    cls,
    confidence
):

    diag[k]["last_block_reason"]=""

    if(
        models[k]["samples"]
        <
        MIN_TRAINING_SAMPLES
    ):

        diag[k]["blocked_training"]+=1

        diag[k]["last_block_reason"]=(
            "TRAINING"
        )

        return False

    if cls==0:

        diag[k]["last_block_reason"]=(
            "NO_SPIKE"
        )

        return False

    if confidence<SIGNAL_CONFIDENCE:

        diag[k]["blocked_confidence"]+=1

        diag[k]["last_block_reason"]=(
            "CONFIDENCE"
        )

        return False

    if(
        time.time()
        -
        last_signal[k]
        <
        COOLDOWN_SECONDS
    ):

        diag[k]["blocked_cooldown"]+=1

        diag[k]["last_block_reason"]=(
            "COOLDOWN"
        )

        return False

    return True


# ============================================================
# AUTO RECONNECT
# ============================================================

async def start_ws(
    k,
    app
):

    while k in active:

        try:

            await deriv_ws(
                k,
                app
            )

        except asyncio.CancelledError:

            return

        except Exception as e:

            diag[k]["last_error"]=(
                f"{type(e).__name__}: {e}"
            )

            diag[k]["last_msg_type"]=(
                "RECONNECTING"
            )

            diag[k]["connected"]=0
            diag[k]["subscribed"]=0

            logging.error(
                "%s websocket: %s",
                k,
                e
            )

            if k in active:

                await asyncio.sleep(5)


# ============================================================
# KEEP ALIVE
# ============================================================

def keep_alive():

    port=int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    class Handler(
        BaseHTTPRequestHandler
    ):

        def do_GET(self):

            self.send_response(200)

            self.end_headers()

            self.wfile.write(
                b"AI MANIAC V5 FIX LIVE"
            )

        def log_message(
            self,
            *args
        ):

            pass

    HTTPServer(
        (
            "0.0.0.0",
            port
        ),
        Handler
    ).serve_forever()


threading.Thread(
    target=keep_alive,
    daemon=True
).start()


# ============================================================
# BUTTONS
# ============================================================

def buttons():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{'✅' if k in active else '❌'} {v['name']}",
                    callback_data=k
                )
            ]
            for k,v in INDICES.items()
        ]
    )


# ============================================================
# START
# ============================================================

async def start(
    update,
    context
):

    chat_ids.add(
        update.effective_chat.id
    )

    for k in INDICES:

        active.add(k)

        if(
            k not in tasks
            or
            tasks[k].done()
        ):

            tasks[k]=asyncio.create_task(
                start_ws(
                    k,
                    context.application
                )
            )

    await update.message.reply_text(

        "🧠🔥 AI MANIAC V5 FIX\n\n"
        "ЗӨВХӨН 7 BOOM / CRASH INDEX\n\n"
        "✅ 7 индекс бүгд идэвхжлээ.\n"
        "📡 Live tick data авч байна.\n"
        "🧠 Balanced historical warm-up ON.\n"
        "🔄 Live result бүрийн дараа online learning ON.\n"
        "💾 Persistent memory ON.\n"
        "🎯 Diagnostic threshold: 40%.\n\n"
        "⚠️ Prediction only.",

        reply_markup=buttons()
    )


# ============================================================
# BUTTON CONTROL
# ============================================================

async def button(
    update,
    context
):

    query=update.callback_query

    await query.answer()

    k=query.data

    chat_ids.add(
        query.message.chat.id
    )

    if k in active:

        active.remove(k)

        task=tasks.get(k)

        if(
            task
            and
            not task.done()
        ):

            task.cancel()

        await query.message.reply_text(
            f"❌ {INDICES[k]['name']} унтарлаа."
        )

    else:

        active.add(k)

        if(
            k not in tasks
            or
            tasks[k].done()
        ):

            tasks[k]=asyncio.create_task(
                start_ws(
                    k,
                    context.application
                )
            )

        await query.message.reply_text(

            f"✅ {INDICES[k]['name']} идэвхжлээ!\n"
            f"🧠 Training: {models[k]['samples']}\n"
            "🎯 Threshold: 40%"
        )

    try:

        await query.edit_message_reply_markup(
            reply_markup=buttons()
        )

    except Exception:

        pass


# ============================================================
# STATUS
# ============================================================

async def status(
    update,
    context
):

    chat_ids.add(
        update.effective_chat.id
    )

    await update.message.reply_text(

        "🧠🔥 AI MANIAC V5 FIX STATUS\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Active: {len(active)}/7\n"
        "📡 Live tick engine: ON\n"
        "🧠 Balanced warm-up: ON\n"
        "🔄 Online learning: ON\n"
        "💾 Persistent memory: ON\n"
        "🎯 Confidence filter: 40%"
    )

    for k,info in INDICES.items():

        wins=stats[k]["ok"]
        losses=stats[k]["fail"]

        total=wins+losses

        winrate=(
            wins/total*100
            if total
            else 0
        )

        probabilities=last_probs[k]

        symbol=(
            DERIV_SYMBOLS.get(k)
            or
            "NOT RESOLVED"
        )

        prediction_names=[
            "NO SPIKE",
            "UP SPIKE",
            "DOWN SPIKE"
        ]

        text=(

            f"{'🟢' if k in active else '⚪'} "
            f"{info['name']}\n"

            "━━━━━━━━━━━━━━━━━━\n"

            f"🔑 API Symbol: "
            f"{symbol}\n"

            f"📡 WebSocket: "
            f"{'CONNECTED ✅' if diag[k]['connected'] else 'OFFLINE ❌'}\n"

            f"📚 History: "
            f"{diag[k]['history']}\n"

            f"📥 Subscribed: "
            f"{'YES ✅' if diag[k]['subscribed'] else 'NO ❌'}\n"

            f"🔄 Last message: "
            f"{diag[k]['last_msg_type'] or '-'}\n"

            f"📊 Ticks stored: "
            f"{len(ticks_data[k])}/{LIVE_HISTORY}\n"

            f"⚡ Live ticks: "
            f"{diag[k]['ticks']}\n"

            f"🧩 Features: "
            f"{diag[k]['features']}\n"

            f"🧠 AI Training: "
            f"{models[k]['samples']}\n"

            f"🏆 WIN: "
            f"{wins} | LOSS: {losses}\n"

            f"📈 WinRate: "
            f"{winrate:.1f}%\n"

            f"⏳ Pending: "
            f"{len(pending[k])}\n"

            f"🎯 Candidates: "
            f"{diag[k]['candidates']}\n"

            f"🔮 Last prediction: "
            f"{prediction_names[last_class[k]]}\n"

            f"🧠 Confidence: "
            f"{diag[k]['last_confidence']*100:.1f}%\n"

            f"📈 UP: "
            f"{probabilities[1]*100:.1f}%\n"

            f"📉 DOWN: "
            f"{probabilities[2]*100:.1f}%\n"

            f"⚪ NO SPIKE: "
            f"{probabilities[0]*100:.1f}%\n"

            f"🚨 Signals: "
            f"{diag[k]['signals']}\n"

            f"📨 Telegram sent: "
            f"{diag[k]['telegram_sent']}\n"

            f"❌ Errors: "
            f"{diag[k]['errors']}\n"

            f"⛔ Blocked confidence: "
            f"{diag[k]['blocked_confidence']}\n"

            f"⏱️ Blocked cooldown: "
            f"{diag[k]['blocked_cooldown']}\n"

            f"🧠 Blocked training: "
            f"{diag[k]['blocked_training']}\n"

            f"🚫 Last block: "
            f"{diag[k]['last_block_reason'] or '-'}\n"

            f"⚠️ Last error: "
            f"{diag[k]['last_error'] or '-'}\n"

            f"📨 Telegram error: "
            f"{diag[k]['last_telegram_error'] or '-'}\n"

            "━━━━━━━━━━━━━━━━━━"
        )

        await update.message.reply_text(
            text
        )

    await update.message.reply_text(
        "🟢 /status COMPLETE"
    )


# ============================================================
# SYMBOLS
# ============================================================

async def symbols(
    update,
    context
):

    chat_ids.add(
        update.effective_chat.id
    )

    lines=[
        "🔎 DERIV SYMBOL MAP",
        "━━━━━━━━━━━━━━━━━━"
    ]

    for k,info in INDICES.items():

        lines.append(
            f"{info['name']}: "
            f"{DERIV_SYMBOLS.get(k) or 'NOT RESOLVED'}"
        )

    lines.extend(
        [
            "━━━━━━━━━━━━━━━━━━",
            "ℹ️ Symbols are taken directly from Deriv active_symbols."
        ]
    )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# AI STATUS
# ============================================================

async def ai(
    update,
    context
):

    chat_ids.add(
        update.effective_chat.id
    )

    message=(
        "🧠🔥 AI BRAIN\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    for k,info in INDICES.items():

        message+=(
            f"\n{info['name']}\n"
            f"Learned: {models[k]['samples']}\n"
            f"Live WIN: {stats[k]['ok']}\n"
            f"Live LOSS: {stats[k]['fail']}\n"
        )

    await update.message.reply_text(
        message
        +
        "\n━━━━━━━━━━━━━━━━━━\n"
        "7 индекс тус бүр өөрийн model-той."
    )


# ============================================================
# TEST
# ============================================================

async def test(
    update,
    context
):

    chat_ids.add(
        update.effective_chat.id
    )

    await update.message.reply_text(

        "🧪 AI MANIAC V5 FIX TEST\n\n"
        "Telegram: OK ✅\n"
        "7 Index engine: OK ✅\n"
        "Historical warm-up: ON ✅\n"
        "Balanced training: ON ✅\n"
        "Online learning: ON ✅\n"
        "Persistent memory: ON ✅\n"
        "Diagnostic 40% filter: ON ✅\n"
        "Auto /start activation: ON ✅\n\n"
        "⚠️ TEST MESSAGE ONLY"
    )


# ============================================================
# APPLICATION
# ============================================================

load_memory()

app=(
    ApplicationBuilder()
    .token(TOKEN)
    .build()
)

app.add_handler(
    CommandHandler(
        "start",
        start
    )
)

app.add_handler(
    CommandHandler(
        "status",
        status
    )
)

app.add_handler(
    CommandHandler(
        "symbols",
        symbols
    )
)

app.add_handler(
    CommandHandler(
        "ai",
        ai
    )
)

app.add_handler(
    CommandHandler(
        "test",
        test
    )
)

app.add_handler(
    CallbackQueryHandler(
        button
    )
)


# ============================================================
# RUN
# ============================================================

if __name__=="__main__":

    logging.info(
        "🔥 AI MANIAC V5 FIX STARTING — ONLY 7 INDICES"
    )

    app.run_polling()
