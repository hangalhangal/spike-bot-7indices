import os
import json
import math
import time
import asyncio
import threading
import logging
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import websockets

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ============================================================
# TELEGRAM
# IMPORTANT: TOKEN / TELEGRAM DELIVERY PATH NOT CHANGED
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# ============================================================
# ONLY THESE 7 INDICES
# ============================================================

INDICES = {
    "BOOM1000": {
        "name": "Boom 1000 Index",
        "symbol": "BOOM1000",
        "type": "BOOM"
    },
    "CRASH1000": {
        "name": "Crash 1000 Index",
        "symbol": "CRASH1000",
        "type": "CRASH"
    },
    "BOOM500": {
        "name": "Boom 500 Index",
        "symbol": "BOOM500",
        "type": "BOOM"
    },
    "CRASH500": {
        "name": "Crash 500 Index",
        "symbol": "CRASH500",
        "type": "CRASH"
    },
    "BOOM600": {
        "name": "Boom 600 Index",
        "symbol": "BOOM600",
        "type": "BOOM"
    },
    "CRASH900": {
        "name": "Crash 900 Index",
        "symbol": "CRASH900",
        "type": "CRASH"
    },
    "BOOM900": {
        "name": "Boom 900 Index",
        "symbol": "BOOM900",
        "type": "BOOM"
    },
}

# ============================================================
# SETTINGS
# ============================================================

HISTORY_COUNT = 5000
MAX_TICKS = 10000

# Prediction window:
# Signal should happen BEFORE expected spike.
PREDICT_MIN_SECONDS = 60
PREDICT_MAX_SECONDS = 120

# Minimum confidence required.
# Quality > quantity.
MIN_CONFIDENCE = 0.78

# Model confidence margin.
MIN_MARGIN = 0.12

# Do not spam same market.
SIGNAL_COOLDOWN = 120

# After a signal, evaluate it after 60-120 sec.
EVALUATION_DELAY = 125

# Minimum number of samples before trusting learned model.
MIN_TRAINING_SAMPLES = 150

# Persistent brain.
BRAIN_FILE = "ai_brain.json"

# ============================================================
# GLOBAL STATE
# ============================================================

active = set()

# Each item:
# {"price": float, "time": unix_timestamp}
ticks_data = {
    k: deque(maxlen=MAX_TICKS)
    for k in INDICES
}

chat_ids = set()

last_signal_time = {
    k: 0.0
    for k in INDICES
}

pending_predictions = {
    k: []
    for k in INDICES
}

learn_stats = {
    k: {
        "ok": 0,
        "fail": 0,
        "training": 0,
        "signals": 0,
    }
    for k in INDICES
}

# ============================================================
# FEATURE NAMES
# ============================================================

FEATURE_NAMES = [
    "ret_5",
    "ret_10",
    "ret_20",
    "ret_40",
    "ret_80",

    "ema_fast_gap",
    "ema_slow_gap",

    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",

    "atr_norm",
    "volatility",

    "bb_position",
    "bb_width",

    "momentum",

    "trend_strength",
    "adx",

    "structure",
    "bos",
    "choch",

    "liquidity_sweep",
    "fvg",

    "order_block",

    "amd_accumulation",
    "amd_manipulation",
    "amd_distribution",

    "spike_distance",
    "spike_frequency",

    "buy_pressure",
    "sell_pressure",
]


# ============================================================
# BRAIN
# ============================================================

class AIBrain:

    def __init__(self, key):

        self.key = key

        self.n = len(FEATURE_NAMES)

        self.weights = np.zeros(self.n, dtype=float)
        self.bias = 0.0

        self.samples = 0
        self.correct = 0
        self.wrong = 0

        self.learning_rate = 0.025
        self.l2 = 0.0005

        self.recent_errors = deque(maxlen=100)

        self.load()

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    def save(self):

        try:

            all_brains = {}

            if os.path.exists(BRAIN_FILE):
                try:
                    with open(BRAIN_FILE, "r") as f:
                        all_brains = json.load(f)
                except Exception:
                    all_brains = {}

            all_brains[self.key] = {
                "weights": self.weights.tolist(),
                "bias": float(self.bias),
                "samples": int(self.samples),
                "correct": int(self.correct),
                "wrong": int(self.wrong),
                "learning_rate": self.learning_rate,
            }

            tmp = BRAIN_FILE + ".tmp"

            with open(tmp, "w") as f:
                json.dump(all_brains, f)

            os.replace(tmp, BRAIN_FILE)

        except Exception as e:
            logging.error(f"{self.key} brain save error: {e}")

    # --------------------------------------------------------
    # LOAD
    # --------------------------------------------------------

    def load(self):

        try:

            if not os.path.exists(BRAIN_FILE):
                return

            with open(BRAIN_FILE, "r") as f:
                data = json.load(f)

            item = data.get(self.key)

            if not item:
                return

            weights = item.get("weights", [])

            if len(weights) == self.n:
                self.weights = np.array(weights, dtype=float)

            self.bias = float(item.get("bias", 0.0))
            self.samples = int(item.get("samples", 0))
            self.correct = int(item.get("correct", 0))
            self.wrong = int(item.get("wrong", 0))

            logging.info(
                f"{self.key} AI brain loaded: "
                f"samples={self.samples}"
            )

        except Exception as e:
            logging.error(f"{self.key} brain load error: {e}")

    # --------------------------------------------------------
    # SIGMOID
    # --------------------------------------------------------

    def sigmoid(self, x):

        x = np.clip(x, -20, 20)

        return 1.0 / (1.0 + np.exp(-x))

    # --------------------------------------------------------
    # PREDICT
    # --------------------------------------------------------

    def predict(self, features):

        x = np.asarray(features, dtype=float)

        if len(x) != self.n:
            return 0.5

        score = float(np.dot(self.weights, x) + self.bias)

        return float(self.sigmoid(score))

    # --------------------------------------------------------
    # ONLINE LEARNING
    #
    # target:
    # 1 = expected spike occurred
    # 0 = did not occur
    # --------------------------------------------------------

    def learn(self, features, target):

        x = np.asarray(features, dtype=float)

        prediction = self.predict(x)

        error = float(target - prediction)

        self.weights += (
            self.learning_rate *
            (
                error * x
                - self.l2 * self.weights
            )
        )

        self.bias += self.learning_rate * error

        self.samples += 1

        if target == 1:
            self.correct += 1
        else:
            self.wrong += 1

        self.recent_errors.append(abs(error))

        # Adaptive learning:
        # if recent error is high -> learn a little faster.
        if len(self.recent_errors) >= 20:

            avg_error = np.mean(self.recent_errors)

            if avg_error > 0.40:
                self.learning_rate = min(
                    0.06,
                    self.learning_rate * 1.02
                )

            elif avg_error < 0.20:
                self.learning_rate = max(
                    0.008,
                    self.learning_rate * 0.995
                )

        if self.samples % 10 == 0:
            self.save()


brains = {
    k: AIBrain(k)
    for k in INDICES
}


# ============================================================
# KEEP ALIVE
# ============================================================

def keep_alive():

    port = int(os.environ.get("PORT", 10000))

    class Handler(BaseHTTPRequestHandler):

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"AI PREDICTIVE SPIKE BOT LIVE"
            )

        def log_message(self, *args):
            return

    httpd = HTTPServer(
        ("0.0.0.0", port),
        Handler
    )

    httpd.serve_forever()


threading.Thread(
    target=keep_alive,
    daemon=True
).start()


# ============================================================
# BASIC MATH
# ============================================================

def safe_mean(values):

    if len(values) == 0:
        return 0.0

    return float(np.mean(values))


def safe_std(values):

    if len(values) < 2:
        return 0.0

    return float(np.std(values))


def ema(values, period):

    if len(values) == 0:
        return 0.0

    alpha = 2.0 / (period + 1.0)

    result = float(values[0])

    for value in values[1:]:
        result = alpha * float(value) + (1 - alpha) * result

    return result


def rsi(values, period=14):

    if len(values) < period + 1:
        return 50.0

    arr = np.asarray(values, dtype=float)

    diff = np.diff(arr)

    gains = np.maximum(diff, 0)
    losses = np.maximum(-diff, 0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss

    return 100.0 - (100.0 / (1.0 + rs))


def atr(values, period=14):

    if len(values) < period + 1:
        return 0.0

    arr = np.asarray(values, dtype=float)

    tr = np.abs(np.diff(arr))

    return float(np.mean(tr[-period:]))


def normalize(value, scale):

    if scale == 0:
        return 0.0

    return float(np.clip(value / scale, -5.0, 5.0))


# ============================================================
# MARKET FEATURES
# ============================================================

def calculate_features(key):

    data = ticks_data[key]

    if len(data) < 250:
        return None

    prices = np.array(
        [x["price"] for x in data],
        dtype=float
    )

    current = prices[-1]

    if current <= 0:
        return None

    # --------------------------------------------------------
    # RETURNS
    # --------------------------------------------------------

    def ret(period):

        if len(prices) <= period:
            return 0.0

        return (
            (prices[-1] - prices[-1-period])
            / prices[-1-period]
        )

    ret5 = ret(5)
    ret10 = ret(10)
    ret20 = ret(20)
    ret40 = ret(40)
    ret80 = ret(80)

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    ema_fast = ema(prices[-60:], 9)
    ema_slow = ema(prices[-120:], 21)

    ema_fast_gap = normalize(
        current - ema_fast,
        max(atr(prices[-100:]), 1e-12)
    )

    ema_slow_gap = normalize(
        current - ema_slow,
        max(atr(prices[-150:]), 1e-12)
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi_value = rsi(prices, 14)

    rsi_norm = (rsi_value - 50.0) / 50.0

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    ema12 = ema(prices[-100:], 12)
    ema26 = ema(prices[-150:], 26)

    macd_value = ema12 - ema26

    macd_series = []

    start = max(0, len(prices) - 100)

    temp = prices[start:]

    for i in range(30, len(temp) + 1):

        part = temp[:i]

        e12 = ema(part, 12)
        e26 = ema(part, 26)

        macd_series.append(e12 - e26)

    if len(macd_series) >= 9:
        macd_signal = ema(
            np.array(macd_series),
            9
        )
    else:
        macd_signal = macd_value

    macd_hist = macd_value - macd_signal

    scale_atr = max(
        atr(prices[-100:]),
        current * 1e-8
    )

    macd_norm = normalize(
        macd_value,
        scale_atr
    )

    macd_signal_norm = normalize(
        macd_signal,
        scale_atr
    )

    macd_hist_norm = normalize(
        macd_hist,
        scale_atr
    )

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    atr_value = atr(prices[-100:], 14)

    atr_norm = normalize(
        atr_value,
        current
    )

    # --------------------------------------------------------
    # VOLATILITY
    # --------------------------------------------------------

    returns = np.diff(prices[-100:]) / prices[-101:-1]

    volatility = safe_std(returns)

    volatility_norm = normalize(
        volatility,
        max(
            safe_mean(np.abs(returns)),
            1e-12
        )
    )

    # --------------------------------------------------------
    # BOLLINGER BANDS
    # --------------------------------------------------------

    bb_period = 40

    bb_data = prices[-bb_period:]

    bb_mid = safe_mean(bb_data)
    bb_std = safe_std(bb_data)

    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    if bb_upper != bb_lower:
        bb_position = (
            (current - bb_lower)
            / (bb_upper - bb_lower)
        )
    else:
        bb_position = 0.5

    bb_position = float(
        np.clip(
            (bb_position - 0.5) * 2,
            -1,
            1
        )
    )

    bb_width = normalize(
        bb_upper - bb_lower,
        current
    )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    momentum = normalize(
        prices[-1] - prices[-20],
        scale_atr * 10
    )

    # --------------------------------------------------------
    # TREND STRENGTH / ADX APPROXIMATION
    # --------------------------------------------------------

    movement = np.diff(prices[-60:])

    up = np.sum(movement[movement > 0])
    down = abs(np.sum(movement[movement < 0]))

    total = up + down

    if total > 0:
        trend_strength = (up - down) / total
    else:
        trend_strength = 0.0

    # ADX-like directional strength

    abs_move = np.sum(np.abs(movement))

    net_move = abs(prices[-1] - prices[-60])

    if abs_move > 0:
        adx_value = 100 * net_move / abs_move
    else:
        adx_value = 0.0

    adx_norm = np.clip(adx_value / 50.0, 0, 2)

    # --------------------------------------------------------
    # MARKET STRUCTURE
    # --------------------------------------------------------

    short = prices[-40:]

    first_half = short[:20]
    second_half = short[20:]

    high1 = np.max(first_half)
    low1 = np.min(first_half)

    high2 = np.max(second_half)
    low2 = np.min(second_half)

    structure = 0.0

    if high2 > high1 and low2 > low1:
        structure = 1.0

    elif high2 < high1 and low2 < low1:
        structure = -1.0

    # --------------------------------------------------------
    # BOS
    # --------------------------------------------------------

    previous_high = np.max(prices[-50:-10])
    previous_low = np.min(prices[-50:-10])

    bos = 0.0

    if current > previous_high:
        bos = 1.0

    elif current < previous_low:
        bos = -1.0

    # --------------------------------------------------------
    # CHOCH
    # --------------------------------------------------------

    older = prices[-80:-40]
    recent = prices[-40:]

    old_direction = np.sign(
        older[-1] - older[0]
    )

    recent_direction = np.sign(
        recent[-1] - recent[0]
    )

    choch = 0.0

    if (
        old_direction < 0
        and recent_direction > 0
    ):
        choch = 1.0

    elif (
        old_direction > 0
        and recent_direction < 0
    ):
        choch = -1.0

    # --------------------------------------------------------
    # LIQUIDITY SWEEP
    # --------------------------------------------------------

    window = prices[-80:-10]

    previous_high = np.max(window)
    previous_low = np.min(window)

    recent_high = np.max(prices[-10:])
    recent_low = np.min(prices[-10:])

    liquidity_sweep = 0.0

    if (
        recent_high > previous_high
        and current < previous_high
    ):
        liquidity_sweep = -1.0

    elif (
        recent_low < previous_low
        and current > previous_low
    ):
        liquidity_sweep = 1.0

    # --------------------------------------------------------
    # FVG / GAP STYLE FEATURE
    # --------------------------------------------------------

    fvg = 0.0

    if len(prices) >= 6:

        p1 = prices[-6]
        p2 = prices[-5]
        p3 = prices[-4]

        local_atr = max(
            atr(prices[-80:]),
            1e-12
        )

        # Upward imbalance
        if p3 > p1:
            gap = p3 - p1

            if gap > local_atr * 0.5:
                fvg = 1.0

        # Downward imbalance
        elif p3 < p1:
            gap = p1 - p3

            if gap > local_atr * 0.5:
                fvg = -1.0

    # --------------------------------------------------------
    # ORDER BLOCK STYLE FEATURE
    # --------------------------------------------------------

    order_block = 0.0

    local = prices[-30:]

    local_mean = np.mean(local)
    local_std = np.std(local)

    if local_std > 0:

        z = (
            current - local_mean
        ) / local_std

        # Strong rejection from lower zone
        if z < -1.5 and current > prices[-5]:
            order_block = 1.0

        # Strong rejection from upper zone
        elif z > 1.5 and current < prices[-5]:
            order_block = -1.0

    # --------------------------------------------------------
    # AMD
    # Accumulation / Manipulation / Distribution
    # --------------------------------------------------------

    amd_accumulation = 0.0
    amd_manipulation = 0.0
    amd_distribution = 0.0

    if len(prices) >= 120:

        a = prices[-120:-80]
        m = prices[-80:-40]
        d = prices[-40:]

        a_vol = safe_std(np.diff(a))
        m_vol = safe_std(np.diff(m))
        d_move = abs(d[-1] - d[0])

        d_vol = safe_std(np.diff(d))

        # accumulation = compressed range
        if a_vol < safe_mean(
            np.abs(np.diff(prices[-160:-120]))
        ) * 0.8:
            amd_accumulation = 1.0

        # manipulation = sudden volatility expansion
        if (
            m_vol >
            max(a_vol, 1e-12) * 1.8
        ):
            amd_manipulation = np.sign(
                m[-1] - m[0]
            )

        # distribution = directional expansion
        if (
            d_move >
            max(d_vol * 5, scale_atr * 5)
        ):
            amd_distribution = np.sign(
                d[-1] - d[0]
            )

    # --------------------------------------------------------
    # PREVIOUS SPIKE DISTANCE / FREQUENCY
    # --------------------------------------------------------

    spike_distance = 0.0
    spike_frequency = 0.0

    recent_returns = np.abs(
        np.diff(prices[-500:])
        / prices[-501:-1]
    )

    if len(recent_returns) > 30:

        baseline = np.median(
            recent_returns
        )

        threshold = max(
            baseline * 8,
            0.0005
        )

        spike_positions = np.where(
            recent_returns > threshold
        )[0]

        if len(spike_positions) > 0:

            distance = (
                len(recent_returns)
                - spike_positions[-1]
            )

            spike_distance = np.clip(
                distance / 200.0,
                0,
                2
            )

            spike_frequency = np.clip(
                len(spike_positions) / 10.0,
                0,
                2
            )

    # --------------------------------------------------------
    # BUY / SELL PRESSURE
    # --------------------------------------------------------

    recent_moves = np.diff(
        prices[-50:]
    )

    positive = np.sum(
        recent_moves[recent_moves > 0]
    )

    negative = abs(np.sum(
        recent_moves[recent_moves < 0]
    ))

    pressure_total = positive + negative

    if pressure_total > 0:

        buy_pressure = (
            positive / pressure_total
        )

        sell_pressure = (
            negative / pressure_total
        )

    else:
        buy_pressure = 0.5
        sell_pressure = 0.5

    buy_pressure = (
        buy_pressure - 0.5
    ) * 2

    sell_pressure = (
        sell_pressure - 0.5
    ) * 2

    # --------------------------------------------------------
    # FEATURE VECTOR
    # --------------------------------------------------------

    features = np.array([
        ret5,
        ret10,
        ret20,
        ret40,
        ret80,

        ema_fast_gap,
        ema_slow_gap,

        rsi_norm,
        macd_norm,
        macd_signal_norm,
        macd_hist_norm,

        atr_norm,
        volatility_norm,

        bb_position,
        bb_width,

        momentum,

        trend_strength,
        adx_norm,

        structure,
        bos,
        choch,

        liquidity_sweep,
        fvg,

        order_block,

        amd_accumulation,
        amd_manipulation,
        amd_distribution,

        spike_distance,
        spike_frequency,

        buy_pressure,
        sell_pressure,

    ], dtype=float)

    features = np.nan_to_num(
        features,
        nan=0.0,
        posinf=0.0,
        neginf=0.0
    )

    features = np.clip(
        features,
        -5,
        5
    )

    return features


# ============================================================
# DIRECTION / CONFIDENCE
# ============================================================

def calculate_signal(key, features):

    info = INDICES[key]
    brain = brains[key]

    ai_probability = brain.predict(features)

    # BOOM -> expected upward spike
    # CRASH -> expected downward spike
    #
    # Model itself predicts "spike likelihood".
    # Direction comes from market type + direction features.

    direction_features = (
        features[0] * 0.10 +
        features[1] * 0.10 +
        features[2] * 0.10 +
        features[5] * 0.10 +
        features[6] * 0.10 +
        features[7] * 0.08 +
        features[10] * 0.08 +
        features[15] * 0.08 +
        features[18] * 0.08 +
        features[19] * 0.06 +
        features[20] * 0.05 +
        features[21] * 0.05 +
        features[23] * 0.05 +
        features[26] * 0.05 +
        features[29] * 0.05
    )

    # For BOOM positive direction is preferred.
    # For CRASH negative direction is preferred.

    if info["type"] == "BOOM":

        directional_strength = (
            1.0 /
            (1.0 + math.exp(
                -np.clip(
                    direction_features * 3,
                    -20,
                    20
                )
            ))
        )

    else:

        directional_strength = (
            1.0 /
            (1.0 + math.exp(
                np.clip(
                    direction_features * 3,
                    -20,
                    20
                )
            ))
        )

    # Combine AI spike probability + directional confirmation.
    confidence = (
        ai_probability * 0.65 +
        directional_strength * 0.35
    )

    return (
        float(confidence),
        float(ai_probability),
        float(directional_strength)
    )


# ============================================================
# SPIKE DETECTION FOR TRAINING
# ============================================================

def detect_future_spike(
    key,
    start_time,
    start_price
):

    data = ticks_data[key]

    future = [
        x for x in data
        if x["time"] >= start_time
        and x["time"] <= start_time + PREDICT_MAX_SECONDS
    ]

    if len(future) < 5:
        return None

    info = INDICES[key]

    prices = np.array(
        [x["price"] for x in future],
        dtype=float
    )

    if info["type"] == "BOOM":

        max_move = (
            np.max(prices) - start_price
        ) / start_price

        # Strong upward movement
        return max_move >= 0.001

    else:

        max_move = (
            start_price - np.min(prices)
        ) / start_price

        # Strong downward movement
        return max_move >= 0.001


# ============================================================
# HISTORICAL TRAINING
# ============================================================

def historical_training(key):

    data = ticks_data[key]

    if len(data) < 1000:
        return 0

    prices = np.array(
        [x["price"] for x in data],
        dtype=float
    )

    times = np.array(
        [x["time"] for x in data],
        dtype=float
    )

    # Avoid training on every tick.
    # This prevents the brain from overfitting.
    step = 10

    trained = 0

    brain = brains[key]

    # Use recent history.
    start = max(
        300,
        len(prices) - 3000
    )

    end = len(prices) - 150

    if end <= start:
        return 0

    for i in range(start, end, step):

        # Temporarily build feature context
        old_data = ticks_data[key]

        # We cannot replace deque directly while websocket
        # is running. Build a temporary local feature calculator
        # using a small helper below.
        features = calculate_features_from_prices(
            prices[:i+1]
        )

        if features is None:
            continue

        current_price = prices[i]
        current_time = times[i]

        future_end = current_time + 120

        future_indices = np.where(
            (times > current_time) &
            (times <= future_end)
        )[0]

        if len(future_indices) < 5:
            continue

        future_prices = prices[
            future_indices
        ]

        if INDICES[key]["type"] == "BOOM":

            move = (
                np.max(future_prices)
                - current_price
            ) / current_price

        else:

            move = (
                current_price
                - np.min(future_prices)
            ) / current_price

        target = 1 if move >= 0.001 else 0

        brain.learn(
            features,
            target
        )

        trained += 1

    brain.save()

    return trained


# ============================================================
# FEATURE CALCULATOR FOR HISTORICAL TRAINING
# ============================================================

def calculate_features_from_prices(prices):

    if len(prices) < 250:
        return None

    # Reuse current feature engine by temporarily using
    # an isolated calculation context.

    current = float(prices[-1])

    if current <= 0:
        return None

    def ret(period):

        if len(prices) <= period:
            return 0

        return (
            prices[-1] -
            prices[-1-period]
        ) / prices[-1-period]

    r5 = ret(5)
    r10 = ret(10)
    r20 = ret(20)
    r40 = ret(40)
    r80 = ret(80)

    atr_value = atr(
        prices[-100:],
        14
    )

    scale = max(
        atr_value,
        current * 1e-8
    )

    ef = ema(prices[-60:], 9)
    es = ema(prices[-120:], 21)

    ema_fast_gap = normalize(
        current - ef,
        scale
    )

    ema_slow_gap = normalize(
        current - es,
        scale
    )

    rsi_value = rsi(
        prices,
        14
    )

    rsi_norm = (
        rsi_value - 50
    ) / 50

    e12 = ema(prices[-100:], 12)
    e26 = ema(prices[-150:], 26)

    macd_value = e12 - e26

    macd_norm = normalize(
        macd_value,
        scale
    )

    macd_signal = 0.0
    macd_hist = macd_value

    macd_signal_norm = normalize(
        macd_signal,
        scale
    )

    macd_hist_norm = normalize(
        macd_hist,
        scale
    )

    returns = (
        np.diff(prices[-100:])
        / prices[-101:-1]
    )

    volatility = safe_std(
        returns
    )

    volatility_norm = normalize(
        volatility,
        max(
            safe_mean(
                np.abs(returns)
            ),
            1e-12
        )
    )

    bb_data = prices[-40:]

    mid = np.mean(bb_data)
    sd = np.std(bb_data)

    upper = mid + 2 * sd
    lower = mid - 2 * sd

    if upper != lower:
        bb_pos = (
            (current - lower)
            / (upper - lower)
        )
    else:
        bb_pos = 0.5

    bb_pos = np.clip(
        (bb_pos - 0.5) * 2,
        -1,
        1
    )

    bb_width = normalize(
        upper - lower,
        current
    )

    momentum = normalize(
        prices[-1] - prices[-20],
        scale * 10
    )

    movement = np.diff(
        prices[-60:]
    )

    up = np.sum(
        movement[movement > 0]
    )

    down = abs(
        np.sum(
            movement[movement < 0]
        )
    )

    total = up + down

    trend_strength = (
        (up - down) / total
        if total > 0
        else 0
    )

    abs_move = np.sum(
        np.abs(movement)
    )

    net_move = abs(
        prices[-1] - prices[-60]
    )

    adx_value = (
        100 * net_move / abs_move
        if abs_move > 0
        else 0
    )

    adx_norm = np.clip(
        adx_value / 50,
        0,
        2
    )

    # Structure
    first = prices[-40:-20]
    second = prices[-20:]

    structure = 0

    if (
        np.max(second) > np.max(first)
        and
        np.min(second) > np.min(first)
    ):
        structure = 1

    elif (
        np.max(second) < np.max(first)
        and
        np.min(second) < np.min(first)
    ):
        structure = -1

    previous_high = np.max(
        prices[-50:-10]
    )

    previous_low = np.min(
        prices[-50:-10]
    )

    bos = 0

    if current > previous_high:
        bos = 1

    elif current < previous_low:
        bos = -1

    older = prices[-80:-40]
    recent = prices[-40:]

    old_direction = np.sign(
        older[-1] - older[0]
    )

    recent_direction = np.sign(
        recent[-1] - recent[0]
    )

    choch = 0

    if old_direction < 0 and recent_direction > 0:
        choch = 1

    elif old_direction > 0 and recent_direction < 0:
        choch = -1

    # Liquidity
    window = prices[-80:-10]

    ph = np.max(window)
    pl = np.min(window)

    rh = np.max(prices[-10:])
    rl = np.min(prices[-10:])

    liquidity = 0

    if rh > ph and current < ph:
        liquidity = -1

    elif rl < pl and current > pl:
        liquidity = 1

    # FVG
    fvg = 0

    if len(prices) >= 6:

        p1 = prices[-6]
        p3 = prices[-4]

        if abs(p3 - p1) > scale * 0.5:
            fvg = np.sign(
                p3 - p1
            )

    # Order block
    ob = 0

    local = prices[-30:]

    local_mean = np.mean(local)
    local_std = np.std(local)

    if local_std > 0:

        z = (
            current - local_mean
        ) / local_std

        if z < -1.5:
            ob = 1

        elif z > 1.5:
            ob = -1

    # AMD
    amd_a = 0
    amd_m = 0
    amd_d = 0

    if len(prices) >= 120:

        a = prices[-120:-80]
        m = prices[-80:-40]
        d = prices[-40:]

        av = safe_std(
            np.diff(a)
        )

        mv = safe_std(
            np.diff(m)
        )

        if mv > max(av, 1e-12) * 1.8:
            amd_m = np.sign(
                m[-1] - m[0]
            )

        if av < (
            safe_mean(
                np.abs(
                    np.diff(
                        prices[-160:-120]
                    )
                )
            ) * 0.8
        ):
            amd_a = 1

        if abs(d[-1] - d[0]) > scale * 5:
            amd_d = np.sign(
                d[-1] - d[0]
            )

    # Spike timing
    rr = np.abs(
        np.diff(prices[-500:])
        / prices[-501:-1]
    )

    spike_distance = 0
    spike_frequency = 0

    if len(rr) > 30:

        baseline = np.median(rr)

        threshold = max(
            baseline * 8,
            0.0005
        )

        pos = np.where(
            rr > threshold
        )[0]

        if len(pos):

            distance = (
                len(rr) - pos[-1]
            )

            spike_distance = np.clip(
                distance / 200,
                0,
                2
            )

            spike_frequency = np.clip(
                len(pos) / 10,
                0,
                2
            )

    # Pressure
    moves = np.diff(
        prices[-50:]
    )

    positive = np.sum(
        moves[moves > 0]
    )

    negative = abs(
        np.sum(
            moves[moves < 0]
        )
    )

    total_pressure = positive + negative

    if total_pressure:

        buy = (
            positive / total_pressure
        )

        sell = (
            negative / total_pressure
        )

    else:

        buy = 0.5
        sell = 0.5

    buy = (
        buy - 0.5
    ) * 2

    sell = (
        sell - 0.5
    ) * 2

    result = np.array([
        r5,
        r10,
        r20,
        r40,
        r80,

        ema_fast_gap,
        ema_slow_gap,

        rsi_norm,
        macd_norm,
        macd_signal_norm,
        macd_hist_norm,

        normalize(atr_value, current),
        volatility_norm,

        bb_pos,
        bb_width,

        momentum,

        trend_strength,
        adx_norm,

        structure,
        bos,
        choch,

        liquidity,
        fvg,

        ob,

        amd_a,
        amd_m,
        amd_d,

        spike_distance,
        spike_frequency,

        buy,
        sell,
    ])

    return np.nan_to_num(
        np.clip(result, -5, 5)
    )


# ============================================================
# SIGNAL EVALUATION
# ============================================================

async def evaluate_prediction(
    key,
    prediction
):

    await asyncio.sleep(
        EVALUATION_DELAY
    )

    data = ticks_data[key]

    start_time = prediction["time"]
    start_price = prediction["price"]

    future = [
        x for x in data
        if start_time <= x["time"]
        <= start_time + PREDICT_MAX_SECONDS
    ]

    if len(future) < 5:
        return

    prices = np.array(
        [x["price"] for x in future],
        dtype=float
    )

    info = INDICES[key]

    if info["type"] == "BOOM":

        move = (
            np.max(prices) -
            start_price
        ) / start_price

    else:

        move = (
            start_price -
            np.min(prices)
        ) / start_price

    success = move >= 0.001

    brain = brains[key]

    # Learn from actual outcome.
    brain.learn(
        prediction["features"],
        1 if success else 0
    )

    learn_stats[key]["training"] = (
        brain.samples
    )

    if success:

        learn_stats[key]["ok"] += 1

    else:

        learn_stats[key]["fail"] += 1

    if success:

        logging.info(
            f"{key} PREDICTION OK | "
            f"move={move*100:.3f}%"
        )

    else:

        logging.info(
            f"{key} PREDICTION FAIL | "
            f"move={move*100:.3f}%"
        )

    brain.save()


# ============================================================
# REAL-TIME SIGNAL ENGINE
# ============================================================

async def analyze_market(
    key,
    app
):

    if key not in active:
        return

    data = ticks_data[key]

    if len(data) < 300:
        return

    now = time.time()

    # --------------------------------------------------------
    # COOLDOWN
    # --------------------------------------------------------

    if (
        now - last_signal_time[key]
        < SIGNAL_COOLDOWN
    ):
        return

    features = calculate_features(key)

    if features is None:
        return

    confidence, ai_probability, direction_strength = (
        calculate_signal(
            key,
            features
        )
    )

    brain = brains[key]

    # --------------------------------------------------------
    # WARM-UP
    #
    # Before enough learning, require extremely strong
    # confirmation.
    # --------------------------------------------------------

    if brain.samples < MIN_TRAINING_SAMPLES:

        required_confidence = 0.88

    else:

        required_confidence = MIN_CONFIDENCE

    # --------------------------------------------------------
    # MARGIN / CONFIDENCE FILTER
    # --------------------------------------------------------

    if confidence < required_confidence:
        return

    if direction_strength < 0.70:
        return

    if (
        ai_probability < 0.65
        and brain.samples >= MIN_TRAINING_SAMPLES
    ):
        return

    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    info = INDICES[key]

    price = data[-1]["price"]

    if info["type"] == "BOOM":

        action = (
            "BUY 🟢"
        )

    else:

        action = (
            "SELL 🔴"
        )

    last_signal_time[key] = now

    learn_stats[key]["signals"] += 1

    prediction = {
        "time": now,
        "price": price,
        "features": features.tolist(),
        "confidence": confidence,
        "ai_probability": ai_probability,
    }

    pending_predictions[key].append(
        prediction
    )

    # Keep only recent pending predictions.
    pending_predictions[key] = (
        pending_predictions[key][-5:]
    )

    text = (
        "🚨 PREDICTIVE AI SPIKE SIGNAL\n\n"
        f"{info['name']}\n"
        f"📌 {action}\n\n"
        f"🧠 AI confidence: "
        f"{confidence*100:.1f}%\n"
        f"📊 Spike probability: "
        f"{ai_probability*100:.1f}%\n"
        f"🎯 Direction confirmation: "
        f"{direction_strength*100:.1f}%\n\n"
        "⏱ Expected window: "
        "NEXT 1–2 MINUTES\n\n"
        f"💰 Price: {price}\n"
        f"🧠 Training samples: "
        f"{brain.samples}\n\n"
        "⚠️ Predictive signal — "
        "not a guarantee."
    )

    for cid in list(chat_ids):

        try:

            await app.bot.send_message(
                chat_id=cid,
                text=text
            )

        except Exception as e:

            logging.error(
                f"Telegram signal error: {e}"
            )

    logging.info(
        f"{key} SIGNAL {action} | "
        f"confidence={confidence:.3f}"
    )

    # Evaluate asynchronously.
    asyncio.create_task(
        evaluate_prediction(
            key,
            prediction
        )
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
        ping_timeout=20,
        close_timeout=10,
    ) as ws:

        # ----------------------------------------------------
        # HISTORICAL DATA
        # ----------------------------------------------------

        await ws.send(
            json.dumps({
                "ticks_history": symbol,
                "count": HISTORY_COUNT,
                "end": "latest",
                "style": "ticks",
                "subscribe": 1,
            })
        )

        # ----------------------------------------------------
        # LIVE SUBSCRIPTION
        # ----------------------------------------------------

        await ws.send(
            json.dumps({
                "ticks": symbol,
                "subscribe": 1,
            })
        )

        logging.info(
            f"{key} websocket connected"
        )

        history_loaded = False

        while key in active:

            try:

                raw = await asyncio.wait_for(
                    ws.recv(),
                    timeout=40
                )

            except asyncio.TimeoutError:

                logging.warning(
                    f"{key} websocket timeout"
                )

                break

            msg = json.loads(raw)

            # ------------------------------------------------
            # HISTORY
            # ------------------------------------------------

            if "history" in msg:

                try:

                    prices = [
                        float(x)
                        for x in msg["history"]["prices"]
                    ]

                    times = msg["history"].get(
                        "times",
                        []
                    )

                    ticks_data[key].clear()

                    if times and len(times) == len(prices):

                        for p, t in zip(
                            prices,
                            times
                        ):

                            ticks_data[key].append({
                                "price": p,
                                "time": float(t)
                            })

                    else:

                        now = time.time()

                        for i, p in enumerate(prices):

                            ticks_data[key].append({
                                "price": p,
                                "time": now - (
                                    len(prices) - i
                                )
                            })

                    history_loaded = True

                    logging.info(
                        f"{key} history loaded: "
                        f"{len(ticks_data[key])}"
                    )

                except Exception as e:

                    logging.error(
                        f"{key} history error: {e}"
                    )

            # ------------------------------------------------
            # LIVE TICK
            # ------------------------------------------------

            if "tick" in msg:

                try:

                    tick = msg["tick"]

                    price = float(
                        tick["quote"]
                    )

                    tick_time = float(
                        tick.get(
                            "epoch",
                            time.time()
                        )
                    )

                    ticks_data[key].append({
                        "price": price,
                        "time": tick_time
                    })

                except Exception as e:

                    logging.error(
                        f"{key} tick error: {e}"
                    )

            # ------------------------------------------------
            # ANALYSIS
            # ------------------------------------------------

            if (
                history_loaded
                and
                len(ticks_data[key]) >= 300
            ):

                await analyze_market(
                    key,
                    app
                )


# ============================================================
# WEBSOCKET RECONNECT
# ============================================================

async def start_ws(
    symbol,
    key,
    app
):

    # Only one connection loop per index.
    while key in active:

        try:

            await deriv_ws(
                symbol,
                key,
                app
            )

        except Exception as e:

            logging.error(
                f"{key} websocket error: {e}"
            )

        if key in active:

            await asyncio.sleep(5)


# ============================================================
# TELEGRAM /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(
        update.effective_chat.id
    )

    buttons = [
        [
            InlineKeyboardButton(
                f"{'✅' if k in active else '❌'} "
                f"{v['name']}",
                callback_data=k
            )
        ]
        for k, v in INDICES.items()
    ]

    await update.message.reply_text(
        "🧠 AI PREDICTIVE SPIKE BOT\n\n"
        "ЗӨВХӨН 7 ИНДЕКС\n"
        "• BOOM 1000\n"
        "• CRASH 1000\n"
        "• BOOM 500\n"
        "• CRASH 500\n"
        "• BOOM 600\n"
        "• CRASH 900\n"
        "• BOOM 900\n\n"
        "AI нь spike-ийг 1–2 минутын "
        "өмнөөс таамаглахыг оролдоно.\n\n"
        "🧠 Online learning идэвхтэй.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# ============================================================
# TELEGRAM BUTTON
# ============================================================

async def button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    q = update.callback_query

    await q.answer()

    key = q.data

    if key not in INDICES:
        return

    chat_ids.add(
        q.message.chat.id
    )

    if key in active:

        active.remove(key)

        await q.message.reply_text(
            f"❌ {INDICES[key]['name']} "
            "унтраалаа!"
        )

    else:

        active.add(key)

        await q.message.reply_text(
            f"✅ {INDICES[key]['name']} "
            "идэвхжлээ!\n\n"
            "🧠 AI analysis эхэллээ."
        )

        asyncio.create_task(
            start_ws(
                INDICES[key]["symbol"],
                key,
                context.application
            )
        )

    buttons = [
        [
            InlineKeyboardButton(
                f"{'✅' if k in active else '❌'} "
                f"{v['name']}",
                callback_data=k
            )
        ]
        for k, v in INDICES.items()
    ]

    try:

        await q.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

    except Exception:
        pass


# ============================================================
# TELEGRAM /STATUS
# ============================================================

async def status_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(
        update.effective_chat.id
    )

    msg = (
        "📊 AI PREDICTIVE STATUS\n\n"
    )

    for k, v in INDICES.items():

        brain = brains[k]
        stats = learn_stats[k]

        msg += (
            f"{'✅' if k in active else '❌'} "
            f"{v['name']}\n"
            f"Ticks: "
            f"{len(ticks_data[k])}/{MAX_TICKS}\n"
            f"Signals: "
            f"{stats['signals']}\n"
            f"AI Training: "
            f"{brain.samples}\n"
            f"OK: {stats['ok']} | "
            f"FAIL: {stats['fail']}\n"
        )

        if (
            stats["ok"] +
            stats["fail"]
        ) > 0:

            accuracy = (
                stats["ok"]
                /
                (
                    stats["ok"] +
                    stats["fail"]
                )
            ) * 100

            msg += (
                f"Accuracy: "
                f"{accuracy:.1f}%\n"
            )

        msg += "\n"

    msg += (
        f"🟢 Active: "
        f"{len(active)}/7\n\n"
        "🧠 Persistent AI brain: ON\n"
        "🎯 Objective: NEXT SPIKE"
    )

    await update.message.reply_text(
        msg
    )


# ============================================================
# TELEGRAM /TEST
# IMPORTANT: PRESERVED
# ============================================================

async def test_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(
        update.effective_chat.id
    )

    await update.message.reply_text(
        "⚠️ PREDICTIVE MANIAC\n"
        "BOOM1000 - BUY NOW 🟢\n"
        "Ticks:500 Drift 0.3% ✅\n"
        "AI МАНГАС TEST OK!"
    )


# ============================================================
# APPLICATION
# ============================================================

if not TELEGRAM_TOKEN:

    raise RuntimeError(
        "TELEGRAM_TOKEN environment variable "
        "not found."
    )


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
        "test",
        test_cmd
    )
)

app.add_handler(
    CommandHandler(
        "status",
        status_cmd
    )
)

app.add_handler(
    CallbackQueryHandler(
        button
    )
)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    logging.info(
        "===================================="
    )

    logging.info(
        "AI PREDICTIVE SPIKE BOT STARTING"
    )

    logging.info(
        "7 INDICES ONLY"
    )

    logging.info(
        "BOOM1000 / CRASH1000 / BOOM500 / "
        "CRASH500 / BOOM600 / CRASH900 / BOOM900"
    )

    logging.info(
        "Telegram delivery path preserved"
    )

    logging.info(
        "===================================="
    )

    app.run_polling()
