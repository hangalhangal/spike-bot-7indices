# ============================================================
# 👹🧠 AI МАНГАС V5 FIX
# FUTURE SPIKE PREDICTOR
# ============================================================
# EXACTLY 7 INDEX:
# BOOM1000, BOOM500, BOOM600, BOOM900
# CRASH1000, CRASH500, CRASH900
#
# BOOM  -> BUY only
# CRASH -> SELL only
#
# Future prediction target:
# 90 - 150 seconds
# Center target: ~120 seconds
#
# IMPORTANT:
# This is a probabilistic predictor, NOT a guaranteed predictor.
# ============================================================

import os
import json
import asyncio
import logging
import threading
import math
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import deque

import websockets


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

DERIV_WS = "wss://api.derivws.com/trading/v1/options/ws/public"

INDICES = [
    "BOOM1000",
    "BOOM500",
    "BOOM600",
    "BOOM900",
    "CRASH1000",
    "CRASH500",
    "CRASH900",
]

HISTORY_COUNT = 5000
TRAINING_COUNT = 300
TICK_BUFFER = 5000

SWING_LEFT = 5
SWING_RIGHT = 5

MOVE_STRENGTH_LOOKBACK = 20

TELEGRAM_SIGNAL_THRESHOLD = 60.0

# ============================================================
# FUTURE PREDICTOR
# ============================================================

PREDICTIVE_TARGET_SECONDS = 120
PREDICTIVE_MIN_LEAD_SECONDS = 90
PREDICTIVE_MAX_LEAD_SECONDS = 150

PREDICTIVE_SPIKE_ATR_MULTIPLIER = 2.5

PREDICTIVE_TRAINING_SAMPLES = 300
PREDICTIVE_EPOCHS = 8

PREDICTIVE_LEARNING_RATE = 0.025
PREDICTIVE_L2 = 0.0003

PREDICTIVE_COOLDOWN_SECONDS = 120

PREDICTIVE_FEATURE_COUNT = 8

PREDICTIVE_MIN_TRAINING = 40

MEMORY_FILE = "ai_memory_v5_fix_predictor.json"

ONLINE_LEARNING_ENABLED = True


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("AI_MANiac")


# ============================================================
# GLOBAL STATE
# ============================================================

telegram_chats = set()

last_telegram_signal = {}

active = {}
tasks = {}

ticks = {
    symbol: deque(maxlen=TICK_BUFFER)
    for symbol in INDICES
}

diag = {
    symbol: {
        "ticks_processed": 0,
        "candidates": 0,
        "signals": 0,
        "last_price": None,
        "last_tick": None,
        "history": 0,
    }
    for symbol in INDICES
}

features = {
    symbol: {}
    for symbol in INDICES
}

structure = {
    symbol: {}
    for symbol in INDICES
}

advanced = {
    symbol: {}
    for symbol in INDICES
}

score = {
    symbol: {
        "buy": 0.0,
        "sell": 0.0,
    }
    for symbol in INDICES
}


# ============================================================
# PREDICTOR STATE
# ============================================================

predictor = {
    symbol: {
        "weights": [0.0] * PREDICTIVE_FEATURE_COUNT,
        "bias": 0.0,

        "trained": False,
        "training_samples": 0,
        "positive_samples": 0,
        "negative_samples": 0,

        "probability": 0.0,
        "last_prediction": None,

        "wins": 0,
        "losses": 0,

        "pending": [],

        "learning_updates": 0,

        "last_train": 0.0,
    }
    for symbol in INDICES
}


# ============================================================
# MEMORY
# ============================================================

def load_predictor_memory():
    if not os.path.exists(MEMORY_FILE):
        return

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        for symbol in INDICES:
            if symbol not in data:
                continue

            src = data[symbol]
            dst = predictor[symbol]

            if isinstance(src.get("weights"), list):
                if len(src["weights"]) == PREDICTIVE_FEATURE_COUNT:
                    dst["weights"] = [
                        float(x) for x in src["weights"]
                    ]

            if "bias" in src:
                dst["bias"] = float(src["bias"])

            dst["wins"] = int(src.get("wins", 0))
            dst["losses"] = int(src.get("losses", 0))
            dst["learning_updates"] = int(
                src.get("learning_updates", 0)
            )

            dst["trained"] = bool(src.get("trained", False))

        logger.info("Predictor memory loaded")

    except Exception as e:
        logger.warning("Memory load failed: %s", e)


def save_predictor_memory():
    try:
        data = {}

        for symbol in INDICES:
            p = predictor[symbol]

            data[symbol] = {
                "weights": p["weights"],
                "bias": p["bias"],
                "wins": p["wins"],
                "losses": p["losses"],
                "learning_updates": p["learning_updates"],
                "trained": p["trained"],
            }

        tmp = MEMORY_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(tmp, MEMORY_FILE)

    except Exception as e:
        logger.warning("Memory save failed: %s", e)


# ============================================================
# KEEP ALIVE HTTP SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        body = b"AI MANIAC V5 FIX ONLINE"

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.end_headers()

        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_http_server():
    port = int(os.getenv("PORT", "8080"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    logger.info("HTTP keep-alive on port %s", port)

    server.serve_forever()


# ============================================================
# DIAGNOSTICS
# ============================================================

def reset_diag(symbol):
    diag[symbol] = {
        "ticks_processed": 0,
        "candidates": 0,
        "signals": 0,
        "last_price": None,
        "last_tick": None,
        "history": 0,
    }

    features[symbol] = {}
    structure[symbol] = {}
    advanced[symbol] = {}

    score[symbol] = {
        "buy": 0.0,
        "sell": 0.0,
    }

    # IMPORTANT:
    # predictor state is NOT reset here.
    # Learned weights survive reconnects.


# ============================================================
# SAFE MATH
# ============================================================

def safe_float(x, default=0.0):
    try:
        v = float(x)

        if math.isfinite(v):
            return v

        return default

    except Exception:
        return default


def clamp(x, low, high):
    return max(low, min(high, x))


def sigmoid(x):
    x = clamp(x, -40.0, 40.0)

    try:
        return 1.0 / (1.0 + math.exp(-x))
    except Exception:
        return 0.5


def normalize(x, scale=1.0):
    if scale == 0:
        return 0.0

    return clamp(x / scale, -5.0, 5.0)


# ============================================================
# BASIC INDICATORS
# ============================================================

def calculate_ema(values, period):
    if not values:
        return 0.0

    period = max(1, int(period))

    alpha = 2.0 / (period + 1.0)

    ema = float(values[0])

    for value in values[1:]:
        ema = (
            alpha * float(value)
            + (1.0 - alpha) * ema
        )

    return ema


def calculate_rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0

    gains = 0.0
    losses = 0.0

    start = len(values) - period

    for i in range(start, len(values)):
        diff = values[i] - values[i - 1]

        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)

    if losses == 0:
        return 100.0 if gains > 0 else 50.0

    rs = gains / losses

    return 100.0 - (
        100.0 / (1.0 + rs)
    )


def calculate_atr(values, period=14):
    if len(values) < 2:
        return 0.0

    start = max(1, len(values) - period)

    ranges = []

    for i in range(start, len(values)):
        ranges.append(
            abs(values[i] - values[i - 1])
        )

    if not ranges:
        return 0.0

    return sum(ranges) / len(ranges)


def calculate_momentum(values, period=10):
    if len(values) <= period:
        return 0.0

    return values[-1] - values[-1 - period]


def calculate_volatility(values, period=20):
    if len(values) < 3:
        return 0.0

    sample = values[-period:]

    if len(sample) < 2:
        return 0.0

    mean = sum(sample) / len(sample)

    variance = sum(
        (x - mean) ** 2
        for x in sample
    ) / len(sample)

    return math.sqrt(max(variance, 0.0))


def calculate_adx_dmi(values, period=14):
    if len(values) < period + 2:
        return {
            "adx": 0.0,
            "plus_di": 0.0,
            "minus_di": 0.0,
        }

    ups = 0.0
    downs = 0.0

    for i in range(
        max(1, len(values) - period),
        len(values)
    ):
        diff = values[i] - values[i - 1]

        if diff > 0:
            ups += diff

        elif diff < 0:
            downs += abs(diff)

    total = ups + downs

    if total == 0:
        return {
            "adx": 0.0,
            "plus_di": 0.0,
            "minus_di": 0.0,
        }

    plus_di = (
        ups / total
    ) * 100.0

    minus_di = (
        downs / total
    ) * 100.0

    dx = (
        abs(plus_di - minus_di)
        / max(plus_di + minus_di, 1e-9)
    ) * 100.0

    return {
        "adx": dx,
        "plus_di": plus_di,
        "minus_di": minus_di,
    }


# ============================================================
# MARKET STRUCTURE
# ============================================================

def calculate_market_structure(symbol):
    prices = [
        x["price"]
        for x in ticks[symbol]
    ]

    if len(prices) < 30:
        return {}

    left = SWING_LEFT
    right = SWING_RIGHT

    highs = []
    lows = []

    end = len(prices) - right

    for i in range(left, end):

        current = prices[i]

        is_high = all(
            current >= prices[j]
            for j in range(i - left, i + right + 1)
            if j != i
        )

        is_low = all(
            current <= prices[j]
            for j in range(i - left, i + right + 1)
            if j != i
        )

        if is_high:
            highs.append(current)

        if is_low:
            lows.append(current)

    current_price = prices[-1]

    resistance = (
        max(highs[-10:])
        if highs
        else current_price
    )

    support = (
        min(lows[-10:])
        if lows
        else current_price
    )

    previous = prices[-20:-5]

    if previous:
        prev_high = max(previous)
        prev_low = min(previous)
    else:
        prev_high = current_price
        prev_low = current_price

    bos_up = current_price > prev_high
    bos_down = current_price < prev_low

    if bos_up:
        trend = "BULLISH"

    elif bos_down:
        trend = "BEARISH"

    else:
        if current_price > (
            (support + resistance) / 2.0
        ):
            trend = "BULLISH"
        else:
            trend = "BEARISH"

    return {
        "trend": trend,
        "support": support,
        "resistance": resistance,
        "bos_up": bos_up,
        "bos_down": bos_down,
        "choch": (
            "UP"
            if bos_up
            else "DOWN"
            if bos_down
            else "NONE"
        ),
    }


# ============================================================
# ORDER BLOCK
# ============================================================

def calculate_order_block(symbol):
    prices = [
        x["price"]
        for x in ticks[symbol]
    ]

    if len(prices) < 30:
        return {
            "bullish": False,
            "bearish": False,
        }

    atr = calculate_atr(prices, 14)

    if atr <= 0:
        return {
            "bullish": False,
            "bearish": False,
        }

    recent = prices[-10:]

    move = recent[-1] - recent[0]

    bullish = move > atr * 1.5
    bearish = move < -atr * 1.5

    return {
        "bullish": bullish,
        "bearish": bearish,
    }


# ============================================================
# FVG
# ============================================================

def calculate_fvg(symbol):
    prices = [
        x["price"]
        for x in ticks[symbol]
    ]

    if len(prices) < 5:
        return {
            "bullish": False,
            "bearish": False,
        }

    atr = calculate_atr(prices, 14)

    if atr <= 0:
        return {
            "bullish": False,
            "bearish": False,
        }

    a = prices[-5]
    b = prices[-3]
    c = prices[-1]

    bullish = (
        b - a
        > atr * 0.8
        and c >= b
    )

    bearish = (
        a - b
        > atr * 0.8
        and c <= b
    )

    return {
        "bullish": bullish,
        "bearish": bearish,
    }


# ============================================================
# SPIKE ANALYSIS
# ============================================================

def calculate_spike_analysis(symbol):
    prices = [
        x["price"]
        for x in ticks[symbol]
    ]

    if len(prices) < 40:
        return {
            "score": 0.0,
            "spike_direction": "NONE",
            "compression": 0.0,
            "expansion": 0.0,
        }

    atr = calculate_atr(prices, 14)

    if atr <= 0:
        return {
            "score": 0.0,
            "spike_direction": "NONE",
            "compression": 0.0,
            "expansion": 0.0,
        }

    short_change = (
        prices[-1]
        - prices[-6]
    )

    long_change = (
        prices[-1]
        - prices[-31]
    )

    short_abs = abs(short_change)
    long_abs = abs(long_change)

    compression = (
        long_abs / max(
            short_abs,
            atr * 0.01
        )
    )

    recent_range = max(
        prices[-10:]
    ) - min(
        prices[-10:]
    )

    previous_range = max(
        prices[-40:-10]
    ) - min(
        prices[-40:-10]
    )

    expansion = (
        recent_range
        / max(
            previous_range,
            atr * 0.01
        )
    )

    momentum = calculate_momentum(
        prices,
        10
    )

    score_value = 0.0
    direction = "NONE"

    if symbol.startswith("BOOM"):

        if momentum < 0:
            score_value += 20.0
            direction = "UP_SPIKE"

        elif momentum > 0:
            score_value += 5.0

    else:

        if momentum > 0:
            score_value += 20.0
            direction = "DOWN_SPIKE"

        elif momentum < 0:
            score_value += 5.0

    if expansion > 1.4:
        score_value += 25.0

    elif expansion > 1.15:
        score_value += 10.0

    if compression > 1.5:
        score_value += 20.0

    elif compression > 1.15:
        score_value += 10.0

    if short_abs > atr * 1.2:
        score_value += 20.0

    score_value = clamp(
        score_value,
        0.0,
        100.0
    )

    return {
        "score": score_value,
        "spike_direction": direction,
        "compression": compression,
        "expansion": expansion,
    }


# ============================================================
# ADVANCED ANALYSIS
# ============================================================

def calculate_advanced_analysis(symbol):
    spike = calculate_spike_analysis(symbol)

    structure_data = structure.get(
        symbol,
        {}
    )

    ob = calculate_order_block(symbol)
    fvg = calculate_fvg(symbol)

    advanced[symbol] = {
        "spike_score": spike.get(
            "score",
            0.0
        ),
        "spike_direction": spike.get(
            "spike_direction",
            "NONE"
        ),
        "compression": spike.get(
            "compression",
            0.0
        ),
        "expansion": spike.get(
            "expansion",
            0.0
        ),

        "order_block": ob,
        "fvg": fvg,

        "trend": structure_data.get(
            "trend",
            "UNKNOWN"
        ),

        "bos_up": structure_data.get(
            "bos_up",
            False
        ),

        "bos_down": structure_data.get(
            "bos_down",
            False
        ),

        "choch": structure_data.get(
            "choch",
            "NONE"
        ),
    }

    return advanced[symbol]


# ============================================================
# SCORE ENGINE
# ============================================================

def calculate_signal_score(symbol):
    prices = [
        x["price"]
        for x in ticks[symbol]
    ]

    if len(prices) < 50:
        score[symbol] = {
            "buy": 0.0,
            "sell": 0.0,
        }

        return score[symbol]

    current = prices[-1]

    ema20 = calculate_ema(
        prices,
        20
    )

    ema50 = calculate_ema(
        prices,
        50
    )

    rsi = calculate_rsi(
        prices,
        14
    )

    atr = calculate_atr(
        prices,
        14
    )

    momentum = calculate_momentum(
        prices,
        10
    )

    adx = calculate_adx_dmi(
        prices,
        14
    )

    st = structure.get(
        symbol,
        {}
    )

    adv = advanced.get(
        symbol,
        {}
    )

    buy = 0.0
    sell = 0.0

    # Trend
    if ema20 > ema50:
        buy += 10
    elif ema20 < ema50:
        sell += 10

    # RSI
    if rsi < 45:
        buy += 10
    elif rsi > 55:
        sell += 10

    # Momentum
    if atr > 0:

        if momentum < -atr:
            buy += 10

        elif momentum > atr:
            sell += 10

    # Structure
    if st.get("trend") == "BULLISH":
        buy += 10

    elif st.get("trend") == "BEARISH":
        sell += 10

    # BOS
    if st.get("bos_up"):
        buy += 10

    if st.get("bos_down"):
        sell += 10

    # S/R
    support = st.get(
        "support",
        current
    )

    resistance = st.get(
        "resistance",
        current
    )

    if atr > 0:

        if abs(current - support) <= atr:
            buy += 10

        if abs(resistance - current) <= atr:
            sell += 10

    # Move strength
    if atr > 0:

        move_strength = abs(
            prices[-1]
            - prices[-21]
        ) / atr

        if move_strength > 2:
            buy += 5
            sell += 5

    # ADX / DMI
    if adx.get("adx", 0) > 20:

        if (
            adx.get("plus_di", 0)
            > adx.get("minus_di", 0)
        ):
            buy += 10

        elif (
            adx.get("minus_di", 0)
            > adx.get("plus_di", 0)
        ):
            sell += 10

    # Order block
    ob = adv.get(
        "order_block",
        {}
    )

    if ob.get("bullish"):
        buy += 5

    if ob.get("bearish"):
        sell += 5

    # FVG
    fvg = adv.get(
        "fvg",
        {}
    )

    if fvg.get("bullish"):
        buy += 5

    if fvg.get("bearish"):
        sell += 5

    # Existing spike score
    spike_score = safe_float(
        adv.get(
            "spike_score",
            0
        )
    )

    if symbol.startswith("BOOM"):
        buy += spike_score * 0.15

    else:
        sell += spike_score * 0.15

    score[symbol] = {
        "buy": clamp(
            buy,
            0,
            100
        ),
        "sell": clamp(
            sell,
            0,
            100
        ),
    }

    return score[symbol]


# ============================================================
# EXISTING FEATURES
# ============================================================

def calculate_features(symbol):
    prices = [
        x["price"]
        for x in ticks[symbol]
    ]

    if len(prices) < 60:
        return False

    ema20 = calculate_ema(
        prices,
        20
    )

    ema50 = calculate_ema(
        prices,
        50
    )

    rsi = calculate_rsi(
        prices,
        14
    )

    atr = calculate_atr(
        prices,
        14
    )

    momentum = calculate_momentum(
        prices,
        10
    )

    volatility = calculate_volatility(
        prices,
        20
    )

    adx = calculate_adx_dmi(
        prices,
        14
    )

    structure[symbol] = calculate_market_structure(
        symbol
    )

    calculate_advanced_analysis(
        symbol
    )

    calculate_signal_score(
        symbol
    )

    features[symbol] = {
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi,
        "atr": atr,
        "momentum": momentum,
        "volatility": volatility,
        "adx": adx,
        "price": prices[-1],
    }

    return True


# ============================================================
# PREDICTIVE FEATURE VECTOR
# ============================================================

def build_predictive_features_from_prices(
    prices
):
    if len(prices) < 60:
        return None

    atr = calculate_atr(
        prices,
        14
    )

    if atr <= 0:
        return None

    current = prices[-1]

    ema20 = calculate_ema(
        prices,
        20
    )

    ema50 = calculate_ema(
        prices,
        50
    )

    rsi = calculate_rsi(
        prices,
        14
    )

    momentum10 = calculate_momentum(
        prices,
        10
    )

    volatility20 = calculate_volatility(
        prices,
        20
    )

    recent_move = (
        prices[-1]
        - prices[-21]
    )

    short_range = (
        max(prices[-10:])
        - min(prices[-10:])
    )

    long_range = (
        max(prices[-40:])
        - min(prices[-40:])
    )

    compression_ratio = (
        long_range
        / max(
            short_range,
            atr * 0.01
        )
    )

    previous_range = (
        max(prices[-40:-10])
        - min(prices[-40:-10])
    )

    range_expansion = (
        short_range
        / max(
            previous_range,
            atr * 0.01
        )
    )

    directional_pressure = (
        prices[-1]
        - prices[-10]
    )

    # Direction-normalized feature set
    return [
        clamp(
            (ema20 - ema50) / atr,
            -5,
            5
        ),

        clamp(
            (rsi - 50.0) / 20.0,
            -3,
            3
        ),

        clamp(
            momentum10 / atr,
            -5,
            5
        ),

        clamp(
            volatility20 / atr,
            0,
            5
        ),

        clamp(
            recent_move / atr,
            -5,
            5
        ),

        clamp(
            compression_ratio,
            0,
            5
        ),

        clamp(
            range_expansion,
            0,
            5
        ),

        clamp(
            directional_pressure / atr,
            -5,
            5
        ),
    ]


# ============================================================
# FUTURE SPIKE LABEL
# ============================================================

def future_spike_label(
    prices,
    times,
    index
):
    """
    Label a historical candidate.

    Positive only when the FIRST qualifying spike
    happens between 90 and 150 seconds after
    the candidate.

    No live future leakage:
    this function is used only on historical
    samples where future ticks are already known.
    """

    if index >= len(prices):
        return None

    if index >= len(times):
        return None

    base_price = prices[index]

    prefix = prices[
        :index + 1
    ]

    atr = calculate_atr(
        prefix,
        14
    )

    if atr <= 0:
        return 0

    base_time = times[index]

    min_time = (
        base_time
        + PREDICTIVE_MIN_LEAD_SECONDS
    )

    max_time = (
        base_time
        + PREDICTIVE_MAX_LEAD_SECONDS
    )

    target = (
        "UP"
        if current_symbol_for_label.startswith("BOOM")
        else "DOWN"
    )

    threshold = (
        atr
        * PREDICTIVE_SPIKE_ATR_MULTIPLIER
    )

    first_spike_time = None

    for j in range(
        index + 1,
        len(prices)
    ):

        future_time = times[j]

        if future_time > max_time:
            break

        move = (
            prices[j]
            - base_price
        )

        if target == "UP":
            qualifies = (
                move >= threshold
            )
        else:
            qualifies = (
                move <= -threshold
            )

        if qualifies:
            first_spike_time = future_time
            break

    if first_spike_time is None:
        return 0

    if first_spike_time < min_time:
        return 0

    if first_spike_time <= max_time:
        return 1

    return 0


# ============================================================
# HISTORICAL TRAINING
# ============================================================

current_symbol_for_label = ""


def build_training_dataset(
    symbol
):
    global current_symbol_for_label

    prices = [
        x["price"]
        for x in ticks[symbol]
    ]

    times = [
        x["time"]
        for x in ticks[symbol]
    ]

    if len(prices) < 300:
        return [], []

    current_symbol_for_label = symbol

    X = []
    Y = []

    start = 70

    end = len(prices) - 10

    candidate_indices = list(
        range(start, end)
    )

    if len(candidate_indices) > (
        PREDICTIVE_TRAINING_SAMPLES * 3
    ):
        candidate_indices = candidate_indices[
            -PREDICTIVE_TRAINING_SAMPLES * 3:
        ]

    # Spread samples through history
    step = max(
        1,
        len(candidate_indices)
        // PREDICTIVE_TRAINING_SAMPLES
    )

    selected = candidate_indices[
        ::step
    ]

    if len(selected) > PREDICTIVE_TRAINING_SAMPLES:
        selected = selected[
            -PREDICTIVE_TRAINING_SAMPLES:
        ]

    for i in selected:

        sample_prices = prices[
            :i + 1
        ]

        vector = (
            build_predictive_features_from_prices(
                sample_prices
            )
        )

        if vector is None:
            continue

        label = future_spike_label(
            prices,
            times,
            i
        )

        if label is None:
            continue

        X.append(vector)
        Y.append(label)

    return X, Y


def train_predictor(
    symbol,
    force=False
):
    p = predictor[symbol]

    if p["trained"] and not force:
        return False

    X, Y = build_training_dataset(
        symbol
    )

    if len(X) < PREDICTIVE_MIN_TRAINING:
        logger.info(
            "%s predictor waiting: %s samples",
            symbol,
            len(X)
        )

        return False

    positives = sum(
        1 for y in Y
        if y == 1
    )

    negatives = (
        len(Y)
        - positives
    )

    # Initialize bias from class prior
    prior = (
        positives
        / max(len(Y), 1)
    )

    prior = clamp(
        prior,
        0.05,
        0.95
    )

    p["weights"] = [
        0.0
        for _ in range(
            PREDICTIVE_FEATURE_COUNT
        )
    ]

    p["bias"] = math.log(
        prior
        / (1.0 - prior)
    )

    for epoch in range(
        PREDICTIVE_EPOCHS
    ):

        for vector, label in zip(
            X,
            Y
        ):

            z = p["bias"]

            for w, x in zip(
                p["weights"],
                vector
            ):
                z += w * x

            prediction = sigmoid(z)

            error = (
                prediction
                - label
            )

            # Bias update
            p["bias"] -= (
                PREDICTIVE_LEARNING_RATE
                * error
            )

            # Weight update
            for i in range(
                PREDICTIVE_FEATURE_COUNT
            ):
                gradient = (
                    error
                    * vector[i]
                    + PREDICTIVE_L2
                    * p["weights"][i]
                )

                p["weights"][i] -= (
                    PREDICTIVE_LEARNING_RATE
                    * gradient
                )

    p["trained"] = True
    p["training_samples"] = len(X)
    p["positive_samples"] = positives
    p["negative_samples"] = negatives
    p["last_train"] = time.time()

    save_predictor_memory()

    logger.info(
        "%s predictor READY | samples=%s | +%s | -%s",
        symbol,
        len(X),
        positives,
        negatives
    )

    return True


# ============================================================
# LIVE PREDICTION
# ============================================================

def predict_future_spike(symbol):
    p = predictor[symbol]

    prices = [
        x["price"]
        for x in ticks[symbol]
    ]

    vector = (
        build_predictive_features_from_prices(
            prices
        )
    )

    if vector is None:
        return {
            "ready": False,
            "probability": 0.0,
            "vector": None,
        }

    if not p["trained"]:
        return {
            "ready": False,
            "probability": 0.0,
            "vector": vector,
        }

    z = p["bias"]

    for w, x in zip(
        p["weights"],
        vector
    ):
        z += w * x

    probability = sigmoid(z)

    p["probability"] = (
        probability * 100.0
    )

    return {
        "ready": True,
        "probability": probability * 100.0,
        "vector": vector,
    }


# ============================================================
# CURRENT SPIKE FILTER
# ============================================================

def spike_already_started(
    symbol
):
    prices = [
        x["price"]
        for x in ticks[symbol]
    ]

    if len(prices) < 25:
        return False

    atr = calculate_atr(
        prices,
        14
    )

    if atr <= 0:
        return False

    recent_move = (
        prices[-1]
        - prices[-10]
    )

    threshold = (
        atr * 1.25
    )

    if symbol.startswith("BOOM"):

        # Up spike already underway
        if recent_move >= threshold:
            return True

    else:

        # Down spike already underway
        if recent_move <= -threshold:
            return True

    return False


# ============================================================
# PENDING PREDICTION
# ============================================================

def add_pending_prediction(
    symbol,
    vector,
    price,
    timestamp,
    direction,
    probability
):
    predictor[symbol]["pending"].append(
        {
            "timestamp": timestamp,
            "base_price": price,
            "vector": list(vector),
            "direction": direction,
            "probability": probability,
            "resolved": False,
        }
    )

    # Prevent unlimited pending records
    if len(
        predictor[symbol]["pending"]
    ) > 20:

        predictor[symbol]["pending"] = (
            predictor[symbol]["pending"][-20:]
        )


# ============================================================
# ONLINE LEARNING
# ============================================================

def resolve_pending_predictions(
    symbol
):
    if not ONLINE_LEARNING_ENABLED:
        return

    p = predictor[symbol]

    if not p["pending"]:
        return

    now = time.time()

    prices = [
        x["price"]
        for x in ticks[symbol]
    ]

    times = [
        x["time"]
        for x in ticks[symbol]
    ]

    if not prices:
        return

    remaining = []

    for item in p["pending"]:

        signal_time = item[
            "timestamp"
        ]

        elapsed = (
            now
            - signal_time
        )

        if elapsed < (
            PREDICTIVE_MAX_LEAD_SECONDS
        ):
            remaining.append(item)
            continue

        base_price = item[
            "base_price"
        ]

        atr = 0.0

        historical_prices = [
            x["price"]
            for x in ticks[symbol]
            if x["time"] <= signal_time
        ]

        if len(historical_prices) >= 20:
            atr = calculate_atr(
                historical_prices,
                14
            )

        if atr <= 0:
            remaining.append(item)
            continue

        threshold = (
            atr
            * PREDICTIVE_SPIKE_ATR_MULTIPLIER
        )

        min_time = (
            signal_time
            + PREDICTIVE_MIN_LEAD_SECONDS
        )

        max_time = (
            signal_time
            + PREDICTIVE_MAX_LEAD_SECONDS
        )

        first_spike = None

        for price, tick_time in zip(
            prices,
            times
        ):

            if tick_time < signal_time:
                continue

            if tick_time > max_time:
                break

            move = (
                price
                - base_price
            )

            if symbol.startswith("BOOM"):
                qualifies = (
                    move >= threshold
                )
            else:
                qualifies = (
                    move <= -threshold
                )

            if qualifies:
                first_spike = tick_time
                break

        win = (
            first_spike is not None
            and first_spike >= min_time
            and first_spike <= max_time
        )

        update_predictor_online(
            symbol,
            item["vector"],
            1 if win else 0
        )

        if win:
            p["wins"] += 1
        else:
            p["losses"] += 1

        p["last_prediction"] = {
            "time": signal_time,
            "result": (
                "WIN"
                if win
                else "LOSS"
            ),
            "probability": item[
                "probability"
            ],
        }

    p["pending"] = remaining


def update_predictor_online(
    symbol,
    vector,
    label
):
    p = predictor[symbol]

    if not p["trained"]:
        return

    z = p["bias"]

    for w, x in zip(
        p["weights"],
        vector
    ):
        z += w * x

    prediction = sigmoid(z)

    error = (
        prediction
        - label
    )

    p["bias"] -= (
        PREDICTIVE_LEARNING_RATE
        * error
    )

    for i in range(
        PREDICTIVE_FEATURE_COUNT
    ):

        gradient = (
            error
            * vector[i]
            + PREDICTIVE_L2
            * p["weights"][i]
        )

        p["weights"][i] -= (
            PREDICTIVE_LEARNING_RATE
            * gradient
        )

    p["learning_updates"] += 1

    save_predictor_memory()


# ============================================================
# TELEGRAM
# ============================================================

async def telegram_api(
    method,
    payload
):
    if not TOKEN:
        return None

    url = (
        "https://api.telegram.org/bot"
        + TOKEN
        + "/"
        + method
    )

    try:
        import urllib.request

        data = json.dumps(
            payload
        ).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type":
                    "application/json"
            }
        )

        loop = asyncio.get_running_loop()

        response = await loop.run_in_executor(
            None,
            lambda: urllib.request.urlopen(
                request,
                timeout=10
            )
        )

        raw = response.read()

        return json.loads(
            raw.decode("utf-8")
        )

    except Exception as e:
        logger.warning(
            "Telegram API error: %s",
            e
        )

        return None


async def send_message(
    chat_id,
    text
):
    return await telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
        }
    )


# ============================================================
# TELEGRAM SIGNAL
# ============================================================

async def send_telegram_signal(
    symbol
):
    if not telegram_chats:
        return False

    pred = predict_future_spike(
        symbol
    )

    if not pred["ready"]:
        return False

    probability = pred[
        "probability"
    ]

    if probability < (
        TELEGRAM_SIGNAL_THRESHOLD
    ):
        return False

    # IMPORTANT:
    # Never send if spike already started.
    if spike_already_started(symbol):
        return False

    now = time.time()

    last = last_telegram_signal.get(
        symbol,
        0
    )

    if (
        now - last
        < PREDICTIVE_COOLDOWN_SECONDS
    ):
        return False

    # Direction is fixed by instrument.
    if symbol.startswith("BOOM"):
        direction = "BUY"
    else:
        direction = "SELL"

    current_score = score.get(
        symbol,
        {}
    )

    buy_score = safe_float(
        current_score.get(
            "buy",
            0
        )
    )

    sell_score = safe_float(
        current_score.get(
            "sell",
            0
        )
    )

    price = (
        diag[symbol].get(
            "last_price"
        )
    )

    text = (
        "👹🧠 AI МАНГАС V5 FIX\n\n"
        "🔮 FUTURE SPIKE PREDICTION\n\n"
        f"📊 {symbol}\n"
        f"📈 Direction: {direction}\n"
        f"🧠 Probability: {probability:.1f}%\n"
        f"⏱ Expected: ~2 minutes\n"
        f"🎯 Target window: "
        f"{PREDICTIVE_MIN_LEAD_SECONDS}-"
        f"{PREDICTIVE_MAX_LEAD_SECONDS} sec\n"
        f"💰 Price: {price}\n\n"
        f"📊 Existing BUY score: "
        f"{buy_score:.1f}\n"
        f"📊 Existing SELL score: "
        f"{sell_score:.1f}\n\n"
        "⚠️ This is a probabilistic "
        "future-spike prediction."
    )

    sent = False

    for chat_id in list(
        telegram_chats
    ):

        result = await send_message(
            chat_id,
            text
        )

        if result and result.get(
            "ok"
        ):
            sent = True

    if sent:

        last_telegram_signal[
            symbol
        ] = now

        diag[symbol][
            "signals"
        ] += 1

        add_pending_prediction(
            symbol=symbol,
            vector=pred["vector"],
            price=price,
            timestamp=now,
            direction=direction,
            probability=probability
        )

        logger.info(
            "FUTURE SIGNAL %s %s %.1f%%",
            symbol,
            direction,
            probability
        )

        return True

    return False


# ============================================================
# TELEGRAM COMMAND POLLER
# ============================================================

telegram_offset = 0


async def telegram_poll():
    global telegram_offset

    if not TOKEN:
        logger.warning(
            "TELEGRAM_TOKEN not set"
        )
        return

    while True:

        try:

            result = await telegram_api(
                "getUpdates",
                {
                    "timeout": 20,
                    "offset": telegram_offset,
                }
            )

            if not result:
                await asyncio.sleep(2)
                continue

            updates = result.get(
                "result",
                []
            )

            for update in updates:

                telegram_offset = (
                    update["update_id"]
                    + 1
                )

                message = update.get(
                    "message"
                )

                if not message:
                    continue

                chat = message.get(
                    "chat",
                    {}
                )

                chat_id = chat.get(
                    "id"
                )

                if chat_id is None:
                    continue

                telegram_chats.add(
                    chat_id
                )

                text = (
                    message.get(
                        "text",
                        ""
                    )
                    .strip()
                )

                if text == "/start":

                    await send_message(
                        chat_id,
                        "👹🧠 AI МАНГАС V5 FIX\n\n"
                        "7 индекс идэвхтэй.\n"
                        "Future Spike Predictor ON.\n"
                        "Target: ~2 minutes."
                    )

                elif text == "/status":

                    await send_message(
                        chat_id,
                        build_status_text()
                    )

                elif text == "/symbols":

                    await send_message(
                        chat_id,
                        "\n".join(
                            INDICES
                        )
                    )

                elif text == "/rawstatus":

                    await send_message(
                        chat_id,
                        build_raw_status()
                    )

        except Exception as e:

            logger.warning(
                "Telegram polling error: %s",
                e
            )

            await asyncio.sleep(3)


# ============================================================
# STATUS
# ============================================================

def build_status_text():
    lines = []

    lines.append(
        "👹🧠 AI МАНГАС V5 FIX"
    )

    lines.append("")
    lines.append(
        "🧠 FUTURE SPIKE PREDICTOR"
    )

    lines.append(
        f"🎯 Target: ~"
        f"{PREDICTIVE_TARGET_SECONDS} sec"
    )

    lines.append(
        f"⏱ Window: "
        f"{PREDICTIVE_MIN_LEAD_SECONDS}-"
        f"{PREDICTIVE_MAX_LEAD_SECONDS} sec"
    )

    lines.append(
        f"🎚 Threshold: "
        f"{TELEGRAM_SIGNAL_THRESHOLD:.0f}%"
    )

    lines.append(
        "🔄 Online learning: "
        + (
            "ON"
            if ONLINE_LEARNING_ENABLED
            else "OFF"
        )
    )

    lines.append("")

    for symbol in INDICES:

        p = predictor[symbol]
        d = diag[symbol]

        ready = (
            "READY"
            if p["trained"]
            else "WAITING"
        )

        total_results = (
            p["wins"]
            + p["losses"]
        )

        if total_results > 0:
            winrate = (
                p["wins"]
                / total_results
                * 100.0
            )
        else:
            winrate = 0.0

        lines.append(
            f"📊 {symbol}"
        )

        lines.append(
            f"  Predictor: {ready}"
        )

        lines.append(
            f"  Train: "
            f"{p['training_samples']}"
        )

        lines.append(
            f"  + / -: "
            f"{p['positive_samples']} / "
            f"{p['negative_samples']}"
        )

        lines.append(
            f"  Prediction: "
            f"{p['probability']:.1f}%"
        )

        lines.append(
            f"  Pending: "
            f"{len(p['pending'])}"
        )

        lines.append(
            f"  W/L: "
            f"{p['wins']}/"
            f"{p['losses']}"
        )

        lines.append(
            f"  Win rate: "
            f"{winrate:.1f}%"
        )

        lines.append(
            f"  Updates: "
            f"{p['learning_updates']}"
        )

        lines.append(
            f"  Ticks: "
            f"{d['ticks_processed']}"
        )

        lines.append("")

    return "\n".join(lines)


def build_raw_status():
    result = {}

    for symbol in INDICES:

        result[symbol] = {
            "active": active.get(
                symbol,
                False
            ),
            "ticks": diag[symbol],
            "features": features.get(
                symbol,
                {}
            ),
            "structure": structure.get(
                symbol,
                {}
            ),
            "advanced": advanced.get(
                symbol,
                {}
            ),
            "score": score.get(
                symbol,
                {}
            ),
            "predictor": {
                "trained": predictor[
                    symbol
                ]["trained"],

                "training_samples":
                    predictor[symbol][
                        "training_samples"
                    ],

                "positive_samples":
                    predictor[symbol][
                        "positive_samples"
                    ],

                "negative_samples":
                    predictor[symbol][
                        "negative_samples"
                    ],

                "probability":
                    predictor[symbol][
                        "probability"
                    ],

                "pending":
                    len(
                        predictor[symbol][
                            "pending"
                        ]
                    ),

                "wins":
                    predictor[symbol][
                        "wins"
                    ],

                "losses":
                    predictor[symbol][
                        "losses"
                    ],
            }
        }

    return json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )


# ============================================================
# HISTORY PROCESSING
# ============================================================

def process_history(
    symbol,
    history
):
    reset_diag(symbol)

    prices = history.get(
        "prices",
        []
    )

    times = history.get(
        "times",
        []
    )

    if not prices:
        return

    count = min(
        len(prices),
        len(times)
    )

    for i in range(count):

        ticks[symbol].append(
            {
                "price":
                    safe_float(
                        prices[i]
                    ),

                "time":
                    safe_float(
                        times[i]
                    ),
            }
        )

    diag[symbol][
        "history"
    ] = len(
        ticks[symbol]
    )

    # Calculate current features
    calculate_features(
        symbol
    )

    # Train predictor only if
    # not already learned.
    if not predictor[symbol][
        "trained"
    ]:

        train_predictor(
            symbol
        )


# ============================================================
# DERIV REQUEST
# ============================================================

async def deriv_send(
    ws,
    payload
):
    await ws.send(
        json.dumps(payload)
    )


async def get_history(
    ws,
    symbol
):
    await deriv_send(
        ws,
        {
            "ticks_history": symbol,
            "count": HISTORY_COUNT,
            "end": "latest",
            "style": "ticks",
            "req_id": 1,
        }
    )

    while True:

        raw = await ws.recv()

        data = json.loads(raw)

        if (
            data.get("history")
            is not None
        ):
            history = data[
                "history"
            ]

            return {
                "prices":
                    history.get(
                        "prices",
                        []
                    ),

                "times":
                    history.get(
                        "times",
                        []
                    ),
            }

        if data.get("error"):
            raise RuntimeError(
                str(
                    data["error"]
                )
            )


# ============================================================
# DERIV WORKER
# ============================================================

async def deriv_worker(
    symbol
):
    while True:

        try:

            active[symbol] = False

            uri = DERIV_WS

            async with websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=None
            ) as ws:

                logger.info(
                    "%s WS connected",
                    symbol
                )

                history = await get_history(
                    ws,
                    symbol
                )

                process_history(
                    symbol,
                    history
                )

                await deriv_send(
                    ws,
                    {
                        "ticks": symbol,
                        "subscribe": 1,
                        "req_id": 2,
                    }
                )

                active[symbol] = True

                logger.info(
                    "%s LIVE",
                    symbol
                )

                while True:

                    raw = await ws.recv()

                    data = json.loads(
                        raw
                    )

                    if data.get(
                        "error"
                    ):
                        logger.warning(
                            "%s Deriv error: %s",
                            symbol,
                            data["error"]
                        )

                        break

                    tick = data.get(
                        "tick"
                    )

                    if not tick:
                        continue

                    price = safe_float(
                        tick.get(
                            "quote"
                        )
                    )

                    epoch = safe_float(
                        tick.get(
                            "epoch"
                        )
                    )

                    if price <= 0:
                        continue

                    ticks[symbol].append(
                        {
                            "price": price,
                            "time": epoch,
                        }
                    )

                    diag[symbol][
                        "ticks_processed"
                    ] += 1

                    diag[symbol][
                        "last_price"
                    ] = price

                    diag[symbol][
                        "last_tick"
                    ] = epoch

                    diag[symbol][
                        "candidates"
                    ] += 1

                    # Existing engine
                    ready = calculate_features(
                        symbol
                    )

                    if not ready:
                        continue

                    # Resolve old predictions
                    resolve_pending_predictions(
                        symbol
                    )

                    # New future prediction
                    pred = predict_future_spike(
                        symbol
                    )

                    if pred["ready"]:

                        # Keep current predictor
                        # status updated.
                        predictor[symbol][
                            "probability"
                        ] = pred[
                            "probability"
                        ]

                        await send_telegram_signal(
                            symbol
                        )

        except asyncio.CancelledError:
            raise

        except Exception as e:

            active[symbol] = False

            logger.warning(
                "%s worker reconnect: %s",
                symbol,
                e
            )

            await asyncio.sleep(3)


# ============================================================
# START INDEX
# ============================================================

async def start_index(
    symbol
):
    if symbol in tasks:
        return

    tasks[symbol] = asyncio.create_task(
        deriv_worker(symbol)
    )


# ============================================================
# START ALL
# ============================================================

async def start():

    load_predictor_memory()

    for symbol in INDICES:

        reset_diag(
            symbol
        )

        await start_index(
            symbol
        )

    await telegram_poll()


# ============================================================
# STARTUP
# ============================================================

def startup():

    logger.info(
        "======================================"
    )

    logger.info(
        "👹🧠 AI МАНГАС V5 FIX STARTING"
    )

    logger.info(
        "7 INDEX MODE"
    )

    logger.info(
        "Future target: ~120 seconds"
    )

    logger.info(
        "Prediction window: 90-150 sec"
    )

    logger.info(
        "Telegram threshold: %.1f%%",
        TELEGRAM_SIGNAL_THRESHOLD
    )

    logger.info(
        "======================================"
    )

    http_thread = threading.Thread(
        target=start_http_server,
        daemon=True
    )

    http_thread.start()

    asyncio.run(
        start()
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    startup()
