import os
import json
import time
import math
import asyncio
import threading
import logging
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

import websockets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ============================================================
# AI MANIAC V3
# ONLY 7 BOOM / CRASH INDICES
# TELEGRAM SIGNAL ONLY
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable байхгүй байна.")

# ============================================================
# ЗӨВХӨН ЭНЭ 7 ИНДЕКС
# ============================================================

INDICES = {
    "BOOM1000": {
        "name": "Boom 1000 Index",
        "symbol": "BOOM1000",
        "type": "BOOM"
    },
    "BOOM500": {
        "name": "Boom 500 Index",
        "symbol": "BOOM500",
        "type": "BOOM"
    },
    "BOOM600": {
        "name": "Boom 600 Index",
        "symbol": "BOOM600",
        "type": "BOOM"
    },
    "BOOM900": {
        "name": "Boom 900 Index",
        "symbol": "BOOM900",
        "type": "BOOM"
    },
    "CRASH1000": {
        "name": "Crash 1000 Index",
        "symbol": "CRASH1000",
        "type": "CRASH"
    },
    "CRASH500": {
        "name": "Crash 500 Index",
        "symbol": "CRASH500",
        "type": "CRASH"
    },
    "CRASH900": {
        "name": "Crash 900 Index",
        "symbol": "CRASH900",
        "type": "CRASH"
    },
}

# ============================================================
# SETTINGS
# ============================================================

# Telegram дээр 500 tick хадгална
LIVE_HISTORY = 500

# Эхний AI сургалтад илүү их historical data авна
BOOTSTRAP_HISTORY = 1500

# Prediction horizon
PREDICTION_SECONDS = 60

# Spike barrier
SPIKE_THRESHOLD = 0.10

# Зөвхөн 85%+ AI confidence
SIGNAL_CONFIDENCE = 0.85

# Нэг индексийн сигналын хоорондын хамгийн бага хугацаа
COOLDOWN_SECONDS = 45

# AI learning rate
LEARNING_RATE = 0.025

# Жинг хэт өсгөхөөс хамгаална
L2 = 0.0005

# AI хангалттай сургалттай болсон эсэх
MIN_TRAINING_SAMPLES = 300

# Сүүлийн үр дүн
RECENT_RESULTS = 100

# AI memory
MEMORY_FILE = "ai_memory.json"

MODEL_VERSION = 3

# ============================================================
# GLOBAL
# ============================================================

active = set()

# Нэг индекс = нэг websocket task
ws_tasks = {}

chat_ids = set()

ticks_data = {
    k: deque(maxlen=LIVE_HISTORY)
    for k in INDICES
}

last_signal_time = {
    k: 0.0
    for k in INDICES
}

# Сигнал гарсан prediction-үүд
pending_predictions = {
    k: []
    for k in INDICES
}

learn_stats = {
    k: {
        "ok": 0,
        "fail": 0,
        "training": 0
    }
    for k in INDICES
}

recent_results = {
    k: deque(maxlen=RECENT_RESULTS)
    for k in INDICES
}

# ============================================================
# AI MODEL
#
# 0 = NO SPIKE
# 1 = UP SPIKE
# 2 = DOWN SPIKE
# ============================================================

FEATURE_COUNT = 12
CLASS_COUNT = 3


def new_model():
    return {
        "weights": [
            [0.0 for _ in range(FEATURE_COUNT)]
            for _ in range(CLASS_COUNT)
        ],
        "bias": [0.0 for _ in range(CLASS_COUNT)],
        "samples": 0,
        "version": MODEL_VERSION
    }


models = {
    k: new_model()
    for k in INDICES
}


# ============================================================
# MEMORY LOAD
# ============================================================

def load_memory():

    global models

    if not os.path.exists(MEMORY_FILE):
        logging.info("AI memory байхгүй. Шинээр суралцана.")
        return

    try:

        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if data.get("version") != MODEL_VERSION:
            logging.warning(
                "Хуучин AI memory version байна. "
                "Шинэ model эхлүүлнэ."
            )
            return

        saved_models = data.get(
            "models",
            {}
        )

        for key in INDICES:

            if key not in saved_models:
                continue

            m = saved_models[key]

            if (
                "weights" not in m
                or "bias" not in m
                or "samples" not in m
            ):
                continue

            if len(m["weights"]) != CLASS_COUNT:
                continue

            if any(
                len(row) != FEATURE_COUNT
                for row in m["weights"]
            ):
                continue

            models[key] = m

        saved_stats = data.get(
            "stats",
            {}
        )

        for key in INDICES:

            if key in saved_stats:

                learn_stats[key].update(
                    saved_stats[key]
                )

        saved_recent = data.get(
            "recent",
            {}
        )

        for key in INDICES:

            if key in saved_recent:

                recent_results[key] = deque(
                    saved_recent[key],
                    maxlen=RECENT_RESULTS
                )

        logging.info("AI MEMORY LOADED")

    except Exception as e:

        logging.error(
            f"AI memory load error: {e}"
        )


# ============================================================
# MEMORY SAVE
# ============================================================

def save_memory():

    try:

        data = {
            "version": MODEL_VERSION,

            "models": models,

            "stats": learn_stats,

            "recent": {
                k: list(v)
                for k, v in recent_results.items()
            }
        }

        tmp_file = MEMORY_FILE + ".tmp"

        with open(
            tmp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False
            )

        os.replace(
            tmp_file,
            MEMORY_FILE
        )

    except Exception as e:

        logging.error(
            f"AI memory save error: {e}"
        )


# ============================================================
# MATH
# ============================================================

def dot(a, b):

    return sum(
        x * y
        for x, y in zip(a, b)
    )


def softmax(values):

    maximum = max(values)

    exp_values = [
        math.exp(
            max(-50, min(50, x - maximum))
        )
        for x in values
    ]

    total = sum(exp_values)

    if total <= 0:
        return [
            1 / 3,
            1 / 3,
            1 / 3
        ]

    return [
        x / total
        for x in exp_values
    ]


# ============================================================
# FEATURE ENGINE
# ============================================================

def calculate_features(prices):

    if len(prices) < 210:
        return None

    p = list(prices)

    current = p[-1]

    if current == 0:
        return None

    def ret(n):

        old = p[-1 - n]

        if old == 0:
            return 0.0

        return (
            (current - old)
            / old
            * 100
        )

    r1 = ret(1)
    r3 = ret(3)
    r5 = ret(5)
    r10 = ret(10)
    r20 = ret(20)
    r50 = ret(50)
    r100 = ret(100)
    r200 = ret(200)

    momentum = (
        r5 * 0.30 +
        r10 * 0.25 +
        r20 * 0.20 +
        r50 * 0.15 +
        r100 * 0.10
    )

    acceleration = r5 - r20

    recent = p[-30:]

    changes = []

    for i in range(1, len(recent)):

        previous = recent[i - 1]

        if previous != 0:

            changes.append(
                abs(
                    (recent[i] - previous)
                    / previous
                    * 100
                )
            )

    volatility = (
        sum(changes) / len(changes)
        if changes
        else 0.0
    )

    up = 0
    down = 0

    for i in range(
        1,
        min(30, len(p))
    ):

        if p[-i] > p[-i - 1]:
            up += 1

        elif p[-i] < p[-i - 1]:
            down += 1

    pressure = (
        (up - down) / 30.0
    )

    trend_ratio = r20 - r100

    tick_acceleration = r1 - r3

    features = [
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
        (
            pressure
            + trend_ratio * 0.01
            + tick_acceleration
        )
    ]

    # Extreme values normalize
    features = [
        max(
            -5.0,
            min(5.0, x)
        )
        for x in features
    ]

    return features


# ============================================================
# AI PREDICTION
# ============================================================

def predict(
    key,
    features
):

    model = models[key]

    scores = []

    for c in range(CLASS_COUNT):

        score = (
            dot(
                model["weights"][c],
                features
            )
            + model["bias"][c]
        )

        scores.append(score)

    probabilities = softmax(
        scores
    )

    predicted_class = max(
        range(CLASS_COUNT),
        key=lambda x: probabilities[x]
    )

    confidence = probabilities[
        predicted_class
    ]

    return (
        predicted_class,
        confidence,
        probabilities
    )


# ============================================================
# ONLINE AI LEARNING
# ============================================================

def train_model(
    key,
    features,
    actual_class
):

    model = models[key]

    scores = []

    for c in range(CLASS_COUNT):

        scores.append(
            dot(
                model["weights"][c],
                features
            )
            + model["bias"][c]
        )

    probabilities = softmax(
        scores
    )

    for c in range(CLASS_COUNT):

        target = (
            1.0
            if c == actual_class
            else 0.0
        )

        error = (
            target
            - probabilities[c]
        )

        for i in range(
            FEATURE_COUNT
        ):

            gradient = (
                error * features[i]
                - L2 * model["weights"][c][i]
            )

            model["weights"][c][i] += (
                LEARNING_RATE
                * gradient
            )

        model["bias"][c] += (
            LEARNING_RATE
            * error
        )

    model["samples"] += 1

    learn_stats[key]["training"] += 1


# ============================================================
# HISTORICAL SPIKE LABEL
#
# Одоогийн цэгээс дараагийн 60 секундэд
# аль barrier эхэлж хүрснийг олно.
# ============================================================

def historical_label(
    data,
    start_index
):

    if start_index >= len(data):
        return None

    start_time, start_price = data[
        start_index
    ]

    if start_price == 0:
        return None

    deadline = (
        start_time
        + PREDICTION_SECONDS
    )

    up_barrier = (
        start_price
        * (1 + SPIKE_THRESHOLD / 100)
    )

    down_barrier = (
        start_price
        * (1 - SPIKE_THRESHOLD / 100)
    )

    for j in range(
        start_index + 1,
        len(data)
    ):

        current_time, current_price = data[j]

        if current_time > deadline:
            break

        if current_price >= up_barrier:
            return 1

        if current_price <= down_barrier:
            return 2

    return 0


# ============================================================
# BOOTSTRAP AI
#
# 1500 historical ticks ашиглаж эхний сургалтыг хийнэ.
# Ингэснээр Training 0 дээр гацахгүй.
# ============================================================

def bootstrap_train(
    key,
    historical_data
):

    model = models[key]

    # Өмнө нь сурсан бол дахин эхнээс нь сургахгүй
    if model["samples"] >= MIN_TRAINING_SAMPLES:
        return

    if len(historical_data) < 250:
        logging.warning(
            f"{key}: bootstrap data бага байна."
        )
        return

    logging.info(
        f"{key}: AI bootstrap эхэллээ..."
    )

    trained = 0

    # Future 60 секундийн label гаргахын тулд
    # эхний хэсгээс нь дарааллаар сургана.
    for i in range(
        210,
        len(historical_data) - 1
    ):

        features = calculate_features(
            [
                x[1]
                for x in historical_data[:i + 1]
            ]
        )

        if features is None:
            continue

        label = historical_label(
            historical_data,
            i
        )

        if label is None:
            continue

        train_model(
            key,
            features,
            label
        )

        trained += 1

    save_memory()

    logging.info(
        f"{key}: bootstrap complete "
        f"trained={trained}"
    )


# ============================================================
# LIVE PREDICTION RESULT
# ============================================================

def live_result(
    prediction,
    current_data
):

    signal_time = prediction[
        "time"
    ]

    start_price = prediction[
        "price"
    ]

    if start_price == 0:
        return 0

    deadline = (
        signal_time
        + PREDICTION_SECONDS
    )

    up_barrier = (
        start_price
        * (1 + SPIKE_THRESHOLD / 100)
    )

    down_barrier = (
        start_price
        * (1 - SPIKE_THRESHOLD / 100)
    )

    # Сигнал гарснаас хойших tick-үүд
    for timestamp, price in current_data:

        if timestamp <= signal_time:
            continue

        if timestamp > deadline:
            break

        # ЭХЭЛЖ аль barrier хүрснийг шалгана
        if price >= up_barrier:
            return 1

        if price <= down_barrier:
            return 2

    # 60 секундэд аль ч barrier хүрээгүй
    return 0


# ============================================================
# EVALUATE PENDING PREDICTIONS
# ============================================================

async def evaluate_predictions(
    key,
    current_time
):

    if not pending_predictions[key]:
        return

    data = list(
        ticks_data[key]
    )

    remaining = []

    for prediction in pending_predictions[key]:

        age = (
            current_time
            - prediction["time"]
        )

        if age < PREDICTION_SECONDS:
            remaining.append(
                prediction
            )
            continue

        actual_class = live_result(
            prediction,
            data
        )

        predicted_direction = (
            prediction["direction"]
        )

        predicted_class = (
            1
            if predicted_direction == "BUY"
            else 2
        )

        # AI өөрийн алдааг засна
        train_model(
            key,
            prediction["features"],
            actual_class
        )

        if (
            actual_class
            == predicted_class
        ):

            learn_stats[key]["ok"] += 1

            recent_results[key].append(1)

            result_text = "WIN ✅"

        else:

            learn_stats[key]["fail"] += 1

            recent_results[key].append(0)

            result_text = (
                "LOSS / NO SPIKE ❌"
            )

        logging.info(
            f"{key} "
            f"{predicted_direction} "
            f"-> {result_text}"
        )

        # AI memory байнга шинэчлэгдэнэ
        save_memory()

    pending_predictions[key] = remaining


# ============================================================
# RECENT WIN RATE
# ============================================================

def recent_winrate(key):

    data = list(
        recent_results[key]
    )

    if len(data) < 20:
        return None

    return (
        sum(data)
        / len(data)
    )


# ============================================================
# SIGNAL FILTER
# ============================================================

def signal_allowed(
    key,
    predicted_class,
    confidence,
    features
):

    model = models[key]

    # AI эхлээд хангалттай сурах ёстой
    if model["samples"] < MIN_TRAINING_SAMPLES:
        return False

    # 85% minimum
    if confidence < SIGNAL_CONFIDENCE:
        return False

    # Cooldown
    if (
        time.time()
        - last_signal_time[key]
        < COOLDOWN_SECONDS
    ):
        return False

    # 0 = NO SPIKE
    if predicted_class == 0:
        return False

    momentum = features[8]

    # BUY
    if predicted_class == 1:

        if momentum <= 0:
            return False

    # SELL
    elif predicted_class == 2:

        if momentum >= 0:
            return False

    # Сүүлийн үр дүнгээр хамгаална
    wr = recent_winrate(key)

    if wr is not None:

        if wr < 0.55:
            return False

    return True


# ============================================================
# TELEGRAM SIGNAL
# ============================================================

async def send_signal(
    app,
    key,
    direction,
    confidence,
    probabilities
):

    info = INDICES[key]

    if direction == "BUY":

        action = "BUY NOW 🟢"
        move = "UP SPIKE 📈"

    else:

        action = "SELL NOW 🔴"
        move = "DOWN SPIKE 📉"

    training = models[key][
        "samples"
    ]

    wr = recent_winrate(key)

    wr_text = (
        f"{wr * 100:.1f}%"
        if wr is not None
        else "N/A"
    )

    text = (
        "🧠🔥 AI MANIAC V3\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊 {info['name']}\n"
        f"⚡ {action}\n"
        f"🎯 {move}\n\n"
        f"🧠 AI Confidence: "
        f"{confidence * 100:.1f}%\n"
        f"📚 AI Training: "
        f"{training}\n"
        f"📈 Recent WinRate: "
        f"{wr_text}\n\n"
        "AI Probability:\n"
        f"🟢 UP: "
        f"{probabilities[1] * 100:.1f}%\n"
        f"🔴 DOWN: "
        f"{probabilities[2] * 100:.1f}%\n"
        f"⚪ NO SPIKE: "
        f"{probabilities[0] * 100:.1f}%\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ AI prediction. "
        "Баталгаатай ашиг гэсэн үг биш."
    )

    for cid in list(chat_ids):

        try:

            await app.bot.send_message(
                chat_id=cid,
                text=text
            )

        except Exception as e:

            logging.error(
                f"Telegram send error: {e}"
            )


# ============================================================
# DERIV WEBSOCKET
# ============================================================

async def deriv_ws(
    symbol,
    key,
    app
):

    uri = (
        "wss://ws.binaryws.com/"
        "websockets/v3?app_id=1089"
    )

    async with websockets.connect(
        uri,
        ping_interval=20,
        ping_timeout=20
    ) as ws:

        # ----------------------------------------------------
        # HISTORICAL DATA
        # ----------------------------------------------------

        await ws.send(
            json.dumps({
                "ticks_history": symbol,
                "count": BOOTSTRAP_HISTORY,
                "end": "latest",
                "style": "ticks",
                "req_id": 1
            })
        )

        # History response-г эхлээд авна
        while True:

            raw = await ws.recv()

            msg = json.loads(raw)

            if "history" in msg:

                prices = msg["history"].get(
                    "prices",
                    []
                )

                times = msg["history"].get(
                    "times",
                    []
                )

                historical = []

                for i, price in enumerate(prices):

                    try:

                        timestamp = float(
                            times[i]
                        )

                        historical.append(
                            (
                                timestamp,
                                float(price)
                            )
                        )

                    except:
                        pass

                # ------------------------------------------------
                # AI FIRST LEARNING
                # ------------------------------------------------

                bootstrap_train(
                    key,
                    historical
                )

                # ------------------------------------------------
                # Live history = last 500
                # ------------------------------------------------

                ticks_data[key].clear()

                for item in historical[
                    -LIVE_HISTORY:
                ]:

                    ticks_data[key].append(
                        item
                    )

                logging.info(
                    f"{key}: "
                    f"{len(historical)} historical ticks loaded"
                )

                break

        # ----------------------------------------------------
        # LIVE SUBSCRIBE
        # ----------------------------------------------------

        await ws.send(
            json.dumps({
                "ticks": symbol,
                "subscribe": 1,
                "req_id": 2
            })
        )

        logging.info(
            f"{key}: LIVE CONNECTED"
        )

        # ----------------------------------------------------
        # LIVE LOOP
        # ----------------------------------------------------

        while key in active:

            raw = await ws.recv()

            msg = json.loads(raw)

            if "tick" not in msg:
                continue

            tick = msg["tick"]

            price = float(
                tick["quote"]
            )

            timestamp = float(
                tick.get(
                    "epoch",
                    time.time()
                )
            )

            ticks_data[key].append(
                (
                    timestamp,
                    price
                )
            )

            # ------------------------------------------------
            # CHECK OLD PREDICTIONS
            # ------------------------------------------------

            await evaluate_predictions(
                key,
                timestamp
            )

            # ------------------------------------------------
            # FEATURES
            # ------------------------------------------------

            prices = [
                x[1]
                for x in ticks_data[key]
            ]

            if len(prices) < 210:
                continue

            features = calculate_features(
                prices
            )

            if features is None:
                continue

            # ------------------------------------------------
            # AI PREDICTION
            # ------------------------------------------------

            (
                predicted_class,
                confidence,
                probabilities
            ) = predict(
                key,
                features
            )

            # ------------------------------------------------
            # FILTER
            # ------------------------------------------------

            if not signal_allowed(
                key,
                predicted_class,
                confidence,
                features
            ):
                continue

            if predicted_class == 1:

                direction = "BUY"

            else:

                direction = "SELL"

            # ------------------------------------------------
            # SAVE PREDICTION
            # ------------------------------------------------

            pending_predictions[key].append({

                "time": timestamp,

                "price": price,

                "direction": direction,

                "features": features,

                "confidence": confidence
            })

            last_signal_time[key] = time.time()

            # ------------------------------------------------
            # TELEGRAM
            # ------------------------------------------------

            await send_signal(
                app,
                key,
                direction,
                confidence,
                probabilities
            )


# ============================================================
# START / RECONNECT
# ============================================================

async def start_ws(
    symbol,
    key,
    app
):

    # Давхар task үүсэхээс хамгаална
    current_task = asyncio.current_task()

    ws_tasks[key] = current_task

    while key in active:

        try:

            await deriv_ws(
                symbol,
                key,
                app
            )

        except asyncio.CancelledError:

            break

        except Exception as e:

            logging.error(
                f"{key} websocket error: {e}"
            )

            if key in active:

                await asyncio.sleep(5)

    if ws_tasks.get(key) == current_task:

        ws_tasks.pop(
            key,
            None
        )


# ============================================================
# KEEP ALIVE
# ============================================================

def keep_alive():

    port = int(
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
                b"AI MANIAC V3 LIVE"
            )

        def log_message(
            self,
            *args
        ):
            return

    server = HTTPServer(
        ("0.0.0.0", port),
        Handler
    )

    server.serve_forever()


threading.Thread(
    target=keep_alive,
    daemon=True
).start()


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(
        update.effective_chat.id
    )

    buttons = []

    for key, info in INDICES.items():

        buttons.append([
            InlineKeyboardButton(
                (
                    "🟢 "
                    if key in active
                    else "⚪ "
                )
                + info["name"],
                callback_data=key
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔥 БҮХ 7-Г ИДЭВХЖҮҮЛЭХ",
            callback_data="ACTIVATE_ALL"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "🛑 БҮХ 7-Г УНТРААХ",
            callback_data="DEACTIVATE_ALL"
        )
    ])

    await update.message.reply_text(

        "🧠🔥 AI MANIAC V3\n\n"
        "ЗӨВХӨН 7 BOOM / CRASH INDEX\n\n"
        "📊 BOOM1000\n"
        "📊 BOOM500\n"
        "📊 BOOM600\n"
        "📊 BOOM900\n"
        "📊 CRASH1000\n"
        "📊 CRASH500\n"
        "📊 CRASH900\n\n"
        "🧠 Historical AI learning\n"
        "🔄 Online self-learning\n"
        "🛠 Алдааны дараа model шинэчлэгдэнэ\n"
        "🎯 85%+ confidence үед л сигнал\n"
        "⏱ 60 секундийн spike prediction\n\n"
        "Доороос сонгоно уу:",

        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# ============================================================
# BUTTON
# ============================================================

async def button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    q = update.callback_query

    await q.answer()

    key = q.data

    chat_ids.add(
        q.message.chat.id
    )

    # ========================================================
    # ACTIVATE ALL
    # ========================================================

    if key == "ACTIVATE_ALL":

        for index_key in INDICES:

            if index_key not in active:

                active.add(
                    index_key
                )

                if index_key not in ws_tasks:

                    asyncio.create_task(
                        start_ws(
                            INDICES[index_key]["symbol"],
                            index_key,
                            context.application
                        )
                    )

        await q.message.reply_text(
            "🔥 7/7 ИНДЕКС ИДЭВХЖЛЭЭ!\n"
            "🧠 AI бүх 7 индексийг зэрэг шинжилж байна."
        )

    # ========================================================
    # DEACTIVATE ALL
    # ========================================================

    elif key == "DEACTIVATE_ALL":

        active.clear()

        await q.message.reply_text(
            "🛑 7/7 индекс унтраалаа."
        )

    # ========================================================
    # SINGLE INDEX
    # ========================================================

    elif key in INDICES:

        if key in active:

            active.remove(
                key
            )

            await q.message.reply_text(
                f"⚪ {INDICES[key]['name']} унтраалаа."
            )

        else:

            active.add(
                key
            )

            await q.message.reply_text(

                f"🟢 {INDICES[key]['name']} идэвхжлээ!\n"
                f"🧠 AI Training: "
                f"{models[key]['samples']}\n"
                f"🎯 Signal: "
                f"{SIGNAL_CONFIDENCE * 100:.0f}%+"
            )

            if key not in ws_tasks:

                asyncio.create_task(
                    start_ws(
                        INDICES[key]["symbol"],
                        key,
                        context.application
                    )
                )

    # ========================================================
    # REFRESH BUTTONS
    # ========================================================

    buttons = []

    for index_key, info in INDICES.items():

        buttons.append([
            InlineKeyboardButton(
                (
                    "🟢 "
                    if index_key in active
                    else "⚪ "
                )
                + info["name"],
                callback_data=index_key
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔥 БҮХ 7-Г ИДЭВХЖҮҮЛЭХ",
            callback_data="ACTIVATE_ALL"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "🛑 БҮХ 7-Г УНТРААХ",
            callback_data="DEACTIVATE_ALL"
        )
    ])

    try:

        await q.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

    except Exception:
        pass


# ============================================================
# /STATUS
# ============================================================

async def status_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(
        update.effective_chat.id
    )

    msg = (
        "🧠🔥 AI MANIAC V3 STATUS\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    for key, info in INDICES.items():

        model = models[key]

        ok = learn_stats[key]["ok"]

        fail = learn_stats[key]["fail"]

        total = ok + fail

        winrate = (
            ok / total
            if total > 0
            else 0
        )

        msg += (
            f"{'🟢' if key in active else '⚪'} "
            f"{info['name']}\n"
            f"Ticks: "
            f"{len(ticks_data[key])}/500\n"
            f"AI Training: "
            f"{model['samples']}\n"
            f"WIN: {ok} | "
            f"LOSS: {fail}\n"
            f"WinRate: "
            f"{winrate * 100:.1f}%\n"
            f"Pending: "
            f"{len(pending_predictions[key])}\n\n"
        )

    msg += (
        "━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Active: "
        f"{len(active)}/7\n\n"
        "🧠 AI historical + online learning\n"
        "🔄 Prediction бүрийн дараа model шинэчлэгдэнэ."
    )

    await update.message.reply_text(
        msg
    )


# ============================================================
# /AI
# ============================================================

async def ai_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(
        update.effective_chat.id
    )

    msg = (
        "🧠🔥 AI BRAIN\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    for key, info in INDICES.items():

        model = models[key]

        msg += (
            f"\n{info['name']}\n"
            f"📚 Learned: "
            f"{model['samples']}\n"
            f"🧠 Memory: "
            f"{'YES' if model['samples'] > 0 else 'NO'}\n"
        )

    msg += (
        "\n━━━━━━━━━━━━━━━━━━\n"
        "7 индекс тус бүр өөрийн AI model-той."
    )

    await update.message.reply_text(
        msg
    )


# ============================================================
# /TEST
# ============================================================

async def test_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(
        update.effective_chat.id
    )

    await update.message.reply_text(

        "🧪 AI MANIAC V3 TEST\n\n"
        "Telegram: ✅\n"
        "7 Index engine: ✅\n"
        "Historical learning: ✅\n"
        "Online learning: ✅\n"
        "Self-correction: ✅\n"
        "Persistent memory: ✅\n"
        "85% signal filter: ✅\n\n"
        "⚠️ Энэ нь зөвхөн connection test."
    )


# ============================================================
# APPLICATION
# ============================================================

load_memory()

app = (
    ApplicationBuilder()
    .token(TELEGRAM_TOKEN)
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
        status_cmd
    )
)

app.add_handler(
    CommandHandler(
        "ai",
        ai_cmd
    )
)

app.add_handler(
    CommandHandler(
        "test",
        test_cmd
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

if __name__ == "__main__":

    logging.info(
        "🔥 AI MANIAC V3 STARTING"
    )

    logging.info(
        "ONLY 7 BOOM/CRASH INDEX"
    )

    app.run_polling()
