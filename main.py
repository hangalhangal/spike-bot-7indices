import os
import json
import asyncio
import logging
import threading
import math
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import deque

import websockets

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(level=logging.INFO)


# ============================================================
# TELEGRAM TOKEN
# ============================================================

TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN missing")


# ============================================================
# DERIV NEW PUBLIC API
# ============================================================

DERIV_PUBLIC_WS = (
    "wss://api.derivws.com/"
    "trading/v1/options/ws/public"
)


# ============================================================
# EXACTLY 7 INDEXES
# ============================================================

INDICES = {
    "BOOM1000": "Boom 1000 Index",
    "BOOM500": "Boom 500 Index",
    "BOOM600": "Boom 600 Index",
    "BOOM900": "Boom 900 Index",
    "CRASH1000": "Crash 1000 Index",
    "CRASH500": "Crash 500 Index",
    "CRASH900": "Crash 900 Index",
}


# ============================================================
# SETTINGS
# ============================================================

HISTORY_COUNT = 5000
TRAINING_COUNT = 300
TICK_BUFFER = 5000

SWING_LEFT = 5
SWING_RIGHT = 5

MOVE_STRENGTH_LOOKBACK = 20


# ============================================================
# GLOBAL DATA
# ============================================================

active = set()

tasks = {}

ticks = {
    symbol: deque(maxlen=TICK_BUFFER)
    for symbol in INDICES
}


# ============================================================
# DIAGNOSTIC STATE
# ============================================================

diag = {
    symbol: {
        "stage": "IDLE",
        "connected": 0,
        "subscribed": 0,
        "ticks": 0,
        "history": 0,
        "training": 0,
        "features": 0,
        "error": "",
    }
    for symbol in INDICES
}


# ============================================================
# FEATURE STATE
# ============================================================

features = {
    symbol: {
        "price": 0.0,
        "ema20": 0.0,
        "ema50": 0.0,
        "rsi14": 0.0,
        "atr14": 0.0,
        "momentum10": 0.0,
        "volatility20": 0.0,
        "trend": "NEUTRAL",
        "direction": "NONE",
    }
    for symbol in INDICES
}


# ============================================================
# MARKET STRUCTURE STATE
# ============================================================

structure = {
    symbol: {
        "swing_high": 0.0,
        "swing_low": 0.0,
        "previous_swing_high": 0.0,
        "previous_swing_low": 0.0,
        "structure": "NONE",
        "bos": "NONE",
        "choch": "NONE",
        "support": 0.0,
        "resistance": 0.0,
        "move_strength": 0.0,

        "swing_high_count": 0,
        "swing_low_count": 0,
        "structure_error": "",
    }
    for symbol in INDICES
}


# ============================================================
# ADVANCED ANALYSIS STATE
# ============================================================

advanced = {
    symbol: {
        "adx14": 0.0,
        "plus_di": 0.0,
        "minus_di": 0.0,

        "ob_bullish": 0,
        "ob_bearish": 0,

        "fvg_bullish": 0,
        "fvg_bearish": 0,

        "spike_score": 0.0,
        "spike_direction": "NONE",

        "compression": 0.0,
        "range_expansion": 0.0,
    }
    for symbol in INDICES
}


# ============================================================
# SCORE STATE
# ============================================================

score = {
    symbol: {
        "buy": 0.0,
        "sell": 0.0,

        "trend": 0.0,
        "rsi": 0.0,
        "momentum": 0.0,
        "volatility": 0.0,
        "structure": 0.0,
        "bos_choch": 0.0,
        "sr": 0.0,
        "move_strength": 0.0,
        "adx": 0.0,
        "order_block": 0.0,
        "fvg": 0.0,
        "spike": 0.0,

        "decision": "WAIT",
        "strength": "LOW",
    }
    for symbol in INDICES
}


# ============================================================
# KEEP ALIVE SERVER
# ============================================================

def keep_alive():

    port = int(os.environ.get("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):

        def do_GET(self):

            self.send_response(200)
            self.end_headers()

            self.wfile.write(
                b"AI MANGAS V5 FIX - SCORE ENGINE V1"
            )

        def log_message(self, *args):
            pass

    HTTPServer(
        ("0.0.0.0", port),
        Handler
    ).serve_forever()


threading.Thread(
    target=keep_alive,
    daemon=True
).start()


# ============================================================
# RESET SYMBOL
# ============================================================

def reset_diag(symbol):

    diag[symbol] = {
        "stage": "IDLE",
        "connected": 0,
        "subscribed": 0,
        "ticks": 0,
        "history": 0,
        "training": 0,
        "features": 0,
        "error": "",
    }

    features[symbol] = {
        "price": 0.0,
        "ema20": 0.0,
        "ema50": 0.0,
        "rsi14": 0.0,
        "atr14": 0.0,
        "momentum10": 0.0,
        "volatility20": 0.0,
        "trend": "NEUTRAL",
        "direction": "NONE",
    }

    structure[symbol] = {
        "swing_high": 0.0,
        "swing_low": 0.0,
        "previous_swing_high": 0.0,
        "previous_swing_low": 0.0,
        "structure": "NONE",
        "bos": "NONE",
        "choch": "NONE",
        "support": 0.0,
        "resistance": 0.0,
        "move_strength": 0.0,
        "swing_high_count": 0,
        "swing_low_count": 0,
        "structure_error": "",
    }

    advanced[symbol] = {
        "adx14": 0.0,
        "plus_di": 0.0,
        "minus_di": 0.0,
        "ob_bullish": 0,
        "ob_bearish": 0,
        "fvg_bullish": 0,
        "fvg_bearish": 0,
        "spike_score": 0.0,
        "spike_direction": "NONE",
        "compression": 0.0,
        "range_expansion": 0.0,
    }

    score[symbol] = {
        "buy": 0.0,
        "sell": 0.0,
        "trend": 0.0,
        "rsi": 0.0,
        "momentum": 0.0,
        "volatility": 0.0,
        "structure": 0.0,
        "bos_choch": 0.0,
        "sr": 0.0,
        "move_strength": 0.0,
        "adx": 0.0,
        "order_block": 0.0,
        "fvg": 0.0,
        "spike": 0.0,
        "decision": "WAIT",
        "strength": "LOW",
    }

    ticks[symbol].clear()


# ============================================================
# EMA
# ============================================================

def calculate_ema(prices, period):

    if len(prices) < period:
        return None

    values = prices[-period:]

    ema = sum(values) / period

    multiplier = 2.0 / (period + 1.0)

    for price in values[1:]:

        ema = (
            (price - ema)
            * multiplier
            + ema
        )

    return ema


# ============================================================
# RSI - WILDER STYLE
# ============================================================

def calculate_rsi(prices, period=14):

    if len(prices) < period + 1:
        return None

    changes = []

    for i in range(1, len(prices)):

        change = (
            prices[i]
            - prices[i - 1]
        )

        changes.append(change)

    if len(changes) < period:
        return None

    gains = [
        max(change, 0.0)
        for change in changes[:period]
    ]

    losses = [
        max(-change, 0.0)
        for change in changes[:period]
    ]

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    for change in changes[period:]:

        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        average_gain = (
            (
                average_gain
                * (period - 1)
            )
            + gain
        ) / period

        average_loss = (
            (
                average_loss
                * (period - 1)
            )
            + loss
        ) / period

    if average_loss == 0:

        if average_gain == 0:
            return 50.0

        return 100.0

    relative_strength = (
        average_gain
        / average_loss
    )

    rsi = (
        100.0
        - (
            100.0
            / (1.0 + relative_strength)
        )
    )

    return max(
        0.0,
        min(100.0, rsi)
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(prices, period=14):

    if len(prices) < period + 1:
        return None

    recent = prices[-(period + 1):]

    true_ranges = []

    for i in range(1, len(recent)):

        current = recent[i]
        previous = recent[i - 1]

        true_range = abs(
            current - previous
        )

        true_ranges.append(
            true_range
        )

    if not true_ranges:
        return None

    return (
        sum(true_ranges[-period:])
        / min(
            period,
            len(true_ranges)
        )
    )


# ============================================================
# MOMENTUM
# ============================================================

def calculate_momentum(prices, period=10):

    if len(prices) <= period:
        return None

    old_price = prices[-period - 1]
    current_price = prices[-1]

    if old_price == 0:
        return 0.0

    return (
        (
            current_price
            - old_price
        )
        / old_price
    ) * 100.0


# ============================================================
# VOLATILITY
# ============================================================

def calculate_volatility(prices, period=20):

    if len(prices) < period + 1:
        return None

    changes = []

    recent = prices[-(period + 1):]

    for i in range(1, len(recent)):

        previous = recent[i - 1]
        current = recent[i]

        if previous == 0:
            continue

        change = (
            current - previous
        ) / previous

        changes.append(change)

    if len(changes) < 2:
        return 0.0

    mean = sum(changes) / len(changes)

    variance = (
        sum(
            (x - mean) ** 2
            for x in changes
        )
        / len(changes)
    )

    return (
        math.sqrt(variance)
        * 100.0
    )


# ============================================================
# ADX / DMI
# ============================================================

def calculate_adx_dmi(prices, period=14):

    if len(prices) < period * 2 + 1:
        return None

    recent = prices[-(period * 2 + 1):]

    tr_values = []
    plus_dm_values = []
    minus_dm_values = []

    for i in range(1, len(recent)):

        current = recent[i]
        previous = recent[i - 1]

        movement = current - previous

        tr = abs(movement)

        if movement > 0:
            plus_dm = movement
            minus_dm = 0.0

        elif movement < 0:
            plus_dm = 0.0
            minus_dm = abs(movement)

        else:
            plus_dm = 0.0
            minus_dm = 0.0

        tr_values.append(tr)
        plus_dm_values.append(plus_dm)
        minus_dm_values.append(minus_dm)

    if len(tr_values) < period:
        return None

    atr = sum(
        tr_values[:period]
    ) / period

    plus_dm = sum(
        plus_dm_values[:period]
    ) / period

    minus_dm = sum(
        minus_dm_values[:period]
    ) / period

    dx_values = []

    plus_di = 0.0
    minus_di = 0.0

    for i in range(
        period,
        len(tr_values)
    ):

        atr = (
            (
                atr * (period - 1)
            )
            + tr_values[i]
        ) / period

        plus_dm = (
            (
                plus_dm * (period - 1)
            )
            + plus_dm_values[i]
        ) / period

        minus_dm = (
            (
                minus_dm * (period - 1)
            )
            + minus_dm_values[i]
        ) / period

        if atr > 0:

            plus_di = (
                100.0
                * plus_dm
                / atr
            )

            minus_di = (
                100.0
                * minus_dm
                / atr
            )

        else:

            plus_di = 0.0
            minus_di = 0.0

        denominator = (
            plus_di
            + minus_di
        )

        if denominator > 0:

            dx = (
                100.0
                * abs(
                    plus_di
                    - minus_di
                )
                / denominator
            )

            dx_values.append(dx)

    if not dx_values:
        return None

    adx = (
        sum(dx_values[-period:])
        / min(
            period,
            len(dx_values)
        )
    )

    return {
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
    }


# ============================================================
# MARKET STRUCTURE
# ============================================================

def calculate_market_structure(symbol):

    prices = [
        float(item[1])
        for item in ticks[symbol]
    ]

    minimum_needed = (
        SWING_LEFT
        + SWING_RIGHT
        + 20
    )

    if len(prices) < minimum_needed:

        structure[symbol][
            "structure_error"
        ] = (
            f"WAITING: PRICE DATA "
            f"{len(prices)}/{minimum_needed}"
        )

        return False

    swing_highs = []
    swing_lows = []

    start = SWING_LEFT
    end = len(prices) - SWING_RIGHT

    for i in range(start, end):

        current = prices[i]

        left = prices[
            i - SWING_LEFT:i
        ]

        right = prices[
            i + 1:
            i + SWING_RIGHT + 1
        ]

        if (
            current > max(left)
            and current >= max(right)
        ):

            swing_highs.append(
                (i, current)
            )

        if (
            current < min(left)
            and current <= min(right)
        ):

            swing_lows.append(
                (i, current)
            )

    high_count = len(swing_highs)
    low_count = len(swing_lows)

    structure[symbol][
        "swing_high_count"
    ] = high_count

    structure[symbol][
        "swing_low_count"
    ] = low_count

    if (
        high_count < 2
        or low_count < 2
    ):

        missing = []

        if high_count < 2:
            missing.append(
                f"HIGH {high_count}/2"
            )

        if low_count < 2:
            missing.append(
                f"LOW {low_count}/2"
            )

        structure[symbol][
            "structure_error"
        ] = (
            "WAITING: "
            + ", ".join(missing)
        )

        return False

    (
        latest_high_index,
        latest_high
    ) = swing_highs[-1]

    (
        previous_high_index,
        previous_high
    ) = swing_highs[-2]

    (
        latest_low_index,
        latest_low
    ) = swing_lows[-1]

    (
        previous_low_index,
        previous_low
    ) = swing_lows[-2]

    current_price = prices[-1]

    high_is_higher = (
        latest_high > previous_high
    )

    low_is_higher = (
        latest_low > previous_low
    )

    high_is_lower = (
        latest_high < previous_high
    )

    low_is_lower = (
        latest_low < previous_low
    )

    if (
        high_is_higher
        and low_is_higher
    ):

        structure_name = "HH + HL"

    elif (
        high_is_lower
        and low_is_lower
    ):

        structure_name = "LH + LL"

    elif (
        high_is_higher
        and low_is_lower
    ):

        structure_name = "HH + LL"

    elif (
        high_is_lower
        and low_is_higher
    ):

        structure_name = "LH + HL"

    else:

        structure_name = "RANGE"

    bos = "NONE"
    choch = "NONE"

    if (
        high_is_higher
        and low_is_higher
    ):

        if current_price < latest_low:

            choch = "BEARISH"

        elif current_price > latest_high:

            bos = "BULLISH"

    elif (
        high_is_lower
        and low_is_lower
    ):

        if current_price > latest_high:

            choch = "BULLISH"

        elif current_price < latest_low:

            bos = "BEARISH"

    atr = features[symbol]["atr14"]

    if atr and atr > 0:

        lookback = min(
            MOVE_STRENGTH_LOOKBACK,
            len(prices) - 1
        )

        start_price = (
            prices[-lookback - 1]
        )

        recent_price = prices[-1]

        recent_move = abs(
            recent_price
            - start_price
        )

        normalized_range = (
            atr
            * math.sqrt(lookback)
        )

        if normalized_range > 0:

            move_strength = (
                recent_move
                / normalized_range
            )

        else:

            move_strength = 0.0

    else:

        move_strength = 0.0

    structure[symbol] = {

        "swing_high":
            latest_high,

        "swing_low":
            latest_low,

        "previous_swing_high":
            previous_high,

        "previous_swing_low":
            previous_low,

        "structure":
            structure_name,

        "bos":
            bos,

        "choch":
            choch,

        "support":
            latest_low,

        "resistance":
            latest_high,

        "move_strength":
            move_strength,

        "swing_high_count":
            high_count,

        "swing_low_count":
            low_count,

        "structure_error":
            "",
    }

    return True


# ============================================================
# ORDER BLOCK PROXY
#
# Tick data does not contain OHLC candle bodies.
# Therefore this is a conservative price-action proxy.
# ============================================================

def calculate_order_block(symbol):

    prices = [
        float(item[1])
        for item in ticks[symbol]
    ]

    if len(prices) < 30:
        return {
            "bullish": 0,
            "bearish": 0,
        }

    atr = features[symbol]["atr14"]

    if not atr or atr <= 0:
        return {
            "bullish": 0,
            "bearish": 0,
        }

    recent = prices[-30:]

    bullish = 0
    bearish = 0

    for i in range(5, len(recent) - 3):

        base = recent[i]
        future = recent[i + 3]

        move = future - base

        if move >= atr * 2.0:

            bullish = 1

        if move <= -atr * 2.0:

            bearish = 1

    return {
        "bullish": bullish,
        "bearish": bearish,
    }


# ============================================================
# FVG / IMBALANCE PROXY
#
# With tick data, there are no candle highs/lows.
# We use a 3-point displacement model.
# ============================================================

def calculate_fvg(symbol):

    prices = [
        float(item[1])
        for item in ticks[symbol]
    ]

    if len(prices) < 10:

        return {
            "bullish": 0,
            "bearish": 0,
        }

    atr = features[symbol]["atr14"]

    if not atr or atr <= 0:

        return {
            "bullish": 0,
            "bearish": 0,
        }

    bullish = 0
    bearish = 0

    start = max(
        2,
        len(prices) - 30
    )

    for i in range(
        start,
        len(prices)
    ):

        p1 = prices[i - 2]
        p2 = prices[i - 1]
        p3 = prices[i]

        move1 = p2 - p1
        move2 = p3 - p2

        if (
            move1 > 0
            and move2 > 0
            and (
                p3 - p1
            ) >= atr * 2.0
        ):

            bullish = 1

        if (
            move1 < 0
            and move2 < 0
            and (
                p1 - p3
            ) >= atr * 2.0
        ):

            bearish = 1

    return {
        "bullish": bullish,
        "bearish": bearish,
    }


# ============================================================
# SPIKE / PRE-SPIKE ANALYSIS
#
# This is a setup detector, NOT a guarantee.
# ============================================================

def calculate_spike_analysis(symbol):

    prices = [
        float(item[1])
        for item in ticks[symbol]
    ]

    if len(prices) < 50:

        return {
            "score": 0.0,
            "direction": "NONE",
            "compression": 0.0,
            "expansion": 0.0,
        }

    atr = features[symbol]["atr14"]

    if not atr or atr <= 0:

        return {
            "score": 0.0,
            "direction": "NONE",
            "compression": 0.0,
            "expansion": 0.0,
        }

    short_changes = []

    for i in range(
        max(1, len(prices) - 10),
        len(prices)
    ):

        short_changes.append(
            abs(
                prices[i]
                - prices[i - 1]
            )
        )

    long_changes = []

    for i in range(
        max(1, len(prices) - 40),
        len(prices)
    ):

        long_changes.append(
            abs(
                prices[i]
                - prices[i - 1]
            )
        )

    short_avg = (
        sum(short_changes)
        / max(1, len(short_changes))
    )

    long_avg = (
        sum(long_changes)
        / max(1, len(long_changes))
    )

    if long_avg > 0:

        compression_ratio = (
            short_avg
            / long_avg
        )

    else:

        compression_ratio = 1.0

    recent_move = abs(
        prices[-1]
        - prices[-11]
    )

    expansion_ratio = (
        recent_move
        / max(
            atr,
            0.000000001
        )
    )

    symbol_is_boom = symbol.startswith(
        "BOOM"
    )

    symbol_is_crash = symbol.startswith(
        "CRASH"
    )

    score_value = 0.0
    direction = "NONE"

    # --------------------------------------------------------
    # Compression
    # --------------------------------------------------------

    if compression_ratio < 0.75:

        score_value += 20.0

    elif compression_ratio < 0.90:

        score_value += 10.0


    # --------------------------------------------------------
    # Expansion / displacement
    # --------------------------------------------------------

    if expansion_ratio >= 3.0:

        score_value += 25.0

    elif expansion_ratio >= 2.0:

        score_value += 15.0


    # --------------------------------------------------------
    # Directional bias
    # --------------------------------------------------------

    momentum = features[symbol]["momentum10"]

    if symbol_is_boom:

        # Boom spike is upward.
        # A compressed/downward phase can precede
        # a possible upward spike setup.

        if momentum < 0:

            score_value += 20.0
            direction = "UP_SPIKE"

        elif momentum > 0:

            score_value += 5.0
            direction = "UP_SPIKE"

    elif symbol_is_crash:

        # Crash spike is downward.

        if momentum > 0:

            score_value += 20.0
            direction = "DOWN_SPIKE"

        elif momentum < 0:

            score_value += 5.0
            direction = "DOWN_SPIKE"

    score_value = min(
        100.0,
        score_value
    )

    return {
        "score": score_value,
        "direction": direction,
        "compression": compression_ratio,
        "expansion": expansion_ratio,
    }


# ============================================================
# ADVANCED ANALYSIS ENGINE
# ============================================================

def calculate_advanced_analysis(symbol):

    adx_data = calculate_adx_dmi(
        [
            float(item[1])
            for item in ticks[symbol]
        ],
        14
    )

    if adx_data:

        advanced[symbol]["adx14"] = (
            adx_data["adx"]
        )

        advanced[symbol]["plus_di"] = (
            adx_data["plus_di"]
        )

        advanced[symbol]["minus_di"] = (
            adx_data["minus_di"]
        )

    else:

        advanced[symbol]["adx14"] = 0.0
        advanced[symbol]["plus_di"] = 0.0
        advanced[symbol]["minus_di"] = 0.0

    ob = calculate_order_block(symbol)

    advanced[symbol]["ob_bullish"] = (
        ob["bullish"]
    )

    advanced[symbol]["ob_bearish"] = (
        ob["bearish"]
    )

    fvg = calculate_fvg(symbol)

    advanced[symbol]["fvg_bullish"] = (
        fvg["bullish"]
    )

    advanced[symbol]["fvg_bearish"] = (
        fvg["bearish"]
    )

    spike = calculate_spike_analysis(
        symbol
    )

    advanced[symbol]["spike_score"] = (
        spike["score"]
    )

    advanced[symbol]["spike_direction"] = (
        spike["direction"]
    )

    advanced[symbol]["compression"] = (
        spike["compression"]
    )

    advanced[symbol]["range_expansion"] = (
        spike["expansion"]
    )


# ============================================================
# SIGNAL SCORE ENGINE V1
#
# IMPORTANT:
# This creates a technical score only.
# It DOES NOT send Telegram signals.
# ============================================================

def calculate_signal_score(symbol):

    if not diag[symbol]["features"]:

        return False

    f = features[symbol]
    s = structure[symbol]
    a = advanced[symbol]

    buy = 0.0
    sell = 0.0

    component_buy = {}
    component_sell = {}

    # ========================================================
    # 1. TREND — 15 POINTS
    # ========================================================

    if f["trend"] == "BULLISH":

        buy += 15.0
        component_buy["trend"] = 15.0

    elif f["trend"] == "BEARISH":

        sell += 15.0
        component_sell["trend"] = 15.0

    else:

        component_buy["trend"] = 0.0
        component_sell["trend"] = 0.0


    # ========================================================
    # 2. RSI — 10 POINTS
    #
    # Extreme RSI is treated as context, not blind reversal.
    # ========================================================

    rsi = f["rsi14"]

    if rsi <= 20:

        buy += 10.0
        component_buy["rsi"] = 10.0

    elif rsi <= 30:

        buy += 6.0
        component_buy["rsi"] = 6.0

    elif rsi >= 80:

        sell += 10.0
        component_sell["rsi"] = 10.0

    elif rsi >= 70:

        sell += 6.0
        component_sell["rsi"] = 6.0


    # ========================================================
    # 3. MOMENTUM — 10 POINTS
    # ========================================================

    momentum = f["momentum10"]

    if momentum > 0:

        buy += 10.0
        component_buy["momentum"] = 10.0

    elif momentum < 0:

        sell += 10.0
        component_sell["momentum"] = 10.0


    # ========================================================
    # 4. MARKET STRUCTURE — 15 POINTS
    # ========================================================

    structure_name = s["structure"]

    if structure_name == "HH + HL":

        buy += 15.0
        component_buy["structure"] = 15.0

    elif structure_name == "LH + LL":

        sell += 15.0
        component_sell["structure"] = 15.0

    elif structure_name == "HH + LL":

        buy += 7.5
        sell += 7.5

    elif structure_name == "LH + HL":

        buy += 7.5
        sell += 7.5


    # ========================================================
    # 5. BOS / CHoCH — 10 POINTS
    # ========================================================

    if s["bos"] == "BULLISH":

        buy += 10.0
        component_buy["bos_choch"] = 10.0

    elif s["bos"] == "BEARISH":

        sell += 10.0
        component_sell["bos_choch"] = 10.0

    elif s["choch"] == "BULLISH":

        buy += 10.0
        component_buy["bos_choch"] = 10.0

    elif s["choch"] == "BEARISH":

        sell += 10.0
        component_sell["bos_choch"] = 10.0


    # ========================================================
    # 6. SUPPORT / RESISTANCE — 10 POINTS
    # ========================================================

    price = f["price"]
    atr = f["atr14"]

    if atr > 0 and s["support"] > 0:

        distance_support = (
            price
            - s["support"]
        )

        distance_resistance = (
            s["resistance"]
            - price
        )

        if (
            0 <= distance_support
            <= atr * 1.5
        ):

            buy += 10.0
            component_buy["sr"] = 10.0

        if (
            0 <= distance_resistance
            <= atr * 1.5
        ):

            sell += 10.0
            component_sell["sr"] = 10.0


    # ========================================================
    # 7. MOVE STRENGTH — 5 POINTS
    # ========================================================

    move_strength = s["move_strength"]

    if move_strength >= 4.0:

        if f["direction"] == "UP":

            buy += 5.0
            component_buy["move_strength"] = 5.0

        elif f["direction"] == "DOWN":

            sell += 5.0
            component_sell["move_strength"] = 5.0


    # ========================================================
    # 8. ADX / DMI — 10 POINTS
    # ========================================================

    adx = a["adx14"]
    plus_di = a["plus_di"]
    minus_di = a["minus_di"]

    if adx >= 20:

        if plus_di > minus_di:

            buy += 10.0
            component_buy["adx"] = 10.0

        elif minus_di > plus_di:

            sell += 10.0
            component_sell["adx"] = 10.0

    elif adx >= 15:

        if plus_di > minus_di:

            buy += 5.0
            component_buy["adx"] = 5.0

        elif minus_di > plus_di:

            sell += 5.0
            component_sell["adx"] = 5.0


    # ========================================================
    # 9. ORDER BLOCK — 5 POINTS
    # ========================================================

    if a["ob_bullish"]:

        buy += 5.0
        component_buy["order_block"] = 5.0

    if a["ob_bearish"]:

        sell += 5.0
        component_sell["order_block"] = 5.0


    # ========================================================
    # 10. FVG / IMBALANCE — 5 POINTS
    # ========================================================

    if a["fvg_bullish"]:

        buy += 5.0
        component_buy["fvg"] = 5.0

    if a["fvg_bearish"]:

        sell += 5.0
        component_sell["fvg"] = 5.0


    # ========================================================
    # 11. BOOM / CRASH SPIKE SETUP — 5 POINTS
    # ========================================================

    spike_score = a["spike_score"]

    if spike_score >= 40:

        if a["spike_direction"] == "UP_SPIKE":

            buy += 5.0
            component_buy["spike"] = 5.0

        elif a["spike_direction"] == "DOWN_SPIKE":

            sell += 5.0
            component_sell["spike"] = 5.0

    elif spike_score >= 20:

        if a["spike_direction"] == "UP_SPIKE":

            buy += 2.5
            component_buy["spike"] = 2.5

        elif a["spike_direction"] == "DOWN_SPIKE":

            sell += 2.5
            component_sell["spike"] = 2.5


    # ========================================================
    # NORMALIZE TO 100
    #
    # Maximum theoretical raw score can be above 100
    # because components are independent.
    # ========================================================

    buy = min(
        100.0,
        buy
    )

    sell = min(
        100.0,
        sell
    )


    # ========================================================
    # DECISION
    #
    # NO TELEGRAM SIGNAL.
    # Just analysis classification.
    # ========================================================

    difference = abs(
        buy - sell
    )

    highest = max(
        buy,
        sell
    )

    if highest >= 80 and difference >= 20:

        if buy > sell:

            decision = "BUY WATCH"

        else:

            decision = "SELL WATCH"

        strength = "VERY HIGH"

    elif highest >= 70 and difference >= 15:

        if buy > sell:

            decision = "BUY WATCH"

        else:

            decision = "SELL WATCH"

        strength = "HIGH"

    elif highest >= 55 and difference >= 10:

        if buy > sell:

            decision = "BUY BIAS"

        else:

            decision = "SELL BIAS"

        strength = "MEDIUM"

    else:

        decision = "WAIT"
        strength = "LOW"


    score[symbol] = {

        "buy":
            buy,

        "sell":
            sell,

        "trend":
            max(
                component_buy.get(
                    "trend",
                    0.0
                ),
                component_sell.get(
                    "trend",
                    0.0
                )
            ),

        "rsi":
            max(
                component_buy.get(
                    "rsi",
                    0.0
                ),
                component_sell.get(
                    "rsi",
                    0.0
                )
            ),

        "momentum":
            max(
                component_buy.get(
                    "momentum",
                    0.0
                ),
                component_sell.get(
                    "momentum",
                    0.0
                )
            ),

        "volatility":
            0.0,

        "structure":
            max(
                component_buy.get(
                    "structure",
                    0.0
                ),
                component_sell.get(
                    "structure",
                    0.0
                )
            ),

        "bos_choch":
            max(
                component_buy.get(
                    "bos_choch",
                    0.0
                ),
                component_sell.get(
                    "bos_choch",
                    0.0
                )
            ),

        "sr":
            max(
                component_buy.get(
                    "sr",
                    0.0
                ),
                component_sell.get(
                    "sr",
                    0.0
                )
            ),

        "move_strength":
            max(
                component_buy.get(
                    "move_strength",
                    0.0
                ),
                component_sell.get(
                    "move_strength",
                    0.0
                )
            ),

        "adx":
            max(
                component_buy.get(
                    "adx",
                    0.0
                ),
                component_sell.get(
                    "adx",
                    0.0
                )
            ),

        "order_block":
            max(
                component_buy.get(
                    "order_block",
                    0.0
                ),
                component_sell.get(
                    "order_block",
                    0.0
                )
            ),

        "fvg":
            max(
                component_buy.get(
                    "fvg",
                    0.0
                ),
                component_sell.get(
                    "fvg",
                    0.0
                )
            ),

        "spike":
            max(
                component_buy.get(
                    "spike",
                    0.0
                ),
                component_sell.get(
                    "spike",
                    0.0
                )
            ),

        "decision":
            decision,

        "strength":
            strength,
    }

    return True


# ============================================================
# FEATURE ENGINE
# ============================================================

def calculate_features(symbol):

    if len(ticks[symbol]) < 50:

        diag[symbol]["features"] = 0

        return False

    prices = [
        float(item[1])
        for item in ticks[symbol]
    ]

    if len(prices) < 50:
        return False

    price = prices[-1]

    ema20 = calculate_ema(
        prices,
        20
    )

    ema50 = calculate_ema(
        prices,
        50
    )

    rsi14 = calculate_rsi(
        prices,
        14
    )

    atr14 = calculate_atr(
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

    if (
        ema20 is None
        or ema50 is None
        or rsi14 is None
        or atr14 is None
        or momentum10 is None
        or volatility20 is None
    ):

        return False

    if ema20 > ema50:

        trend = "BULLISH"

    elif ema20 < ema50:

        trend = "BEARISH"

    else:

        trend = "NEUTRAL"

    if momentum10 > 0:

        direction = "UP"

    elif momentum10 < 0:

        direction = "DOWN"

    else:

        direction = "FLAT"

    features[symbol] = {

        "price":
            price,

        "ema20":
            ema20,

        "ema50":
            ema50,

        "rsi14":
            rsi14,

        "atr14":
            atr14,

        "momentum10":
            momentum10,

        "volatility20":
            volatility20,

        "trend":
            trend,

        "direction":
            direction,
    }

    diag[symbol]["features"] = 1

    calculate_market_structure(
        symbol
    )

    calculate_advanced_analysis(
        symbol
    )

    calculate_signal_score(
        symbol
    )

    return True


# ============================================================
# HISTORY PROCESSING
# ============================================================

def process_history(
    symbol,
    prices,
    times=None
):

    if not isinstance(prices, list):

        raise RuntimeError(
            "History prices is not a list"
        )

    if len(prices) == 0:

        raise RuntimeError(
            "History returned 0 prices"
        )

    diag[symbol]["history"] = len(prices)

    ticks[symbol].clear()

    if isinstance(times, list):

        pairs = list(
            zip(times, prices)
        )

    else:

        pairs = [
            (0.0, price)
            for price in prices
        ]

    for epoch, price in pairs:

        try:

            ticks[symbol].append(
                (
                    float(epoch),
                    float(price)
                )
            )

        except Exception:

            continue

    diag[symbol]["training"] = min(
        len(prices),
        TRAINING_COUNT
    )

    calculate_features(
        symbol
    )


# ============================================================
# DERIV WORKER
# ============================================================

async def deriv_worker(symbol):

    while symbol in active:

        reset_diag(symbol)

        try:

            # =================================================
            # HISTORY CONNECTION
            # =================================================

            diag[symbol]["stage"] = (
                "CONNECTING HISTORY"
            )

            async with websockets.connect(
                DERIV_PUBLIC_WS,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=20
            ) as ws:

                diag[symbol]["connected"] = 1

                diag[symbol]["stage"] = (
                    "HISTORY"
                )

                request = {

                    "ticks_history":
                        symbol,

                    "count":
                        HISTORY_COUNT,

                    "end":
                        "latest",

                    "style":
                        "ticks",

                    "req_id":
                        2000
                }

                await ws.send(
                    json.dumps(request)
                )

                while True:

                    raw = await asyncio.wait_for(
                        ws.recv(),
                        timeout=30
                    )

                    msg = json.loads(raw)

                    if "error" in msg:

                        raise RuntimeError(
                            msg["error"].get(
                                "message",
                                "Deriv API error"
                            )
                        )

                    history = msg.get(
                        "history"
                    )

                    if isinstance(
                        history,
                        dict
                    ):

                        prices = history.get(
                            "prices",
                            []
                        )

                        times = history.get(
                            "times",
                            []
                        )

                        process_history(
                            symbol,
                            prices,
                            times
                        )

                        break


            # =================================================
            # LIVE CONNECTION
            # =================================================

            diag[symbol]["stage"] = (
                "CONNECTING LIVE"
            )

            async with websockets.connect(
                DERIV_PUBLIC_WS,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=20
            ) as ws:

                diag[symbol]["connected"] = 1

                diag[symbol]["stage"] = "LIVE"

                request = {

                    "ticks":
                        symbol,

                    "subscribe":
                        1,

                    "req_id":
                        3000
                }

                await ws.send(
                    json.dumps(request)
                )

                while symbol in active:

                    raw = await asyncio.wait_for(
                        ws.recv(),
                        timeout=60
                    )

                    msg = json.loads(raw)

                    if "error" in msg:

                        raise RuntimeError(
                            msg["error"].get(
                                "message",
                                "Deriv API error"
                            )
                        )

                    tick = msg.get(
                        "tick"
                    )

                    if not isinstance(
                        tick,
                        dict
                    ):

                        continue

                    quote = tick.get(
                        "quote"
                    )

                    epoch = tick.get(
                        "epoch",
                        0
                    )

                    if quote is None:

                        continue

                    price = float(
                        quote
                    )

                    ticks[symbol].append(
                        (
                            float(epoch),
                            price
                        )
                    )

                    diag[symbol]["subscribed"] = 1

                    diag[symbol]["ticks"] += 1

                    calculate_features(
                        symbol
                    )

                    diag[symbol]["stage"] = "LIVE"


        except asyncio.CancelledError:

            return

        except Exception as e:

            diag[symbol]["stage"] = (
                "ERROR"
            )

            diag[symbol]["connected"] = 0

            diag[symbol]["subscribed"] = 0

            diag[symbol]["error"] = (
                f"{type(e).__name__}: "
                f"{str(e)[:200]}"
            )

            logging.error(
                "[%s] %s",
                symbol,
                diag[symbol]["error"]
            )

            if symbol in active:

                await asyncio.sleep(5)


# ============================================================
# START INDEX
# ============================================================

def start_index(symbol):

    active.add(symbol)

    if (
        symbol not in tasks
        or tasks[symbol].done()
    ):

        tasks[symbol] = (
            asyncio.create_task(
                deriv_worker(symbol)
            )
        )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    for symbol in INDICES:

        start_index(symbol)

    await update.message.reply_text(

        "👹🧠 AI МАНГАС V5 FIX\n\n"

        "7 INDEX LIVE + FEATURE ENGINE ✅\n"

        "MARKET STRUCTURE ON ✅\n"

        "BOS / CHoCH LOGIC FIX ON ✅\n"

        "MOVE STRENGTH NORMALIZED FIX ON ✅\n"

        "ADX / DMI ANALYSIS ON ✅\n"

        "ORDER BLOCK ANALYSIS ON ✅\n"

        "FVG / IMBALANCE ANALYSIS ON ✅\n"

        "SPIKE ANALYSIS ON ✅\n"

        "SIGNAL SCORE ENGINE V1 ON ✅\n\n"

        "⚠️ SCORE ONLY MODE\n"

        "Telegram BUY/SELL SIGNAL: OFF ❌\n\n"

        "History → Live Tick → "
        "Features → Structure → "
        "Advanced Analysis → Score\n\n"

        "/status"
    )


# ============================================================
# /STATUS
# ============================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    lines = [

        "👹🧠 AI МАНГАС V5 FIX",

        "",

        "🧠 FEATURE + MARKET STRUCTURE + SCORE",

        "==============================",

        ""
    ]

    for symbol, name in INDICES.items():

        d = diag[symbol]

        f = features[symbol]

        s = structure[symbol]

        a = advanced[symbol]

        sc = score[symbol]

        ws_status = (
            "ON ✅"
            if d["connected"]
            else
            "OFF ❌"
        )

        sub_status = (
            "YES ✅"
            if d["subscribed"]
            else
            "NO ❌"
        )

        feature_status = (
            "READY ✅"
            if d["features"]
            else
            "WAITING ⏳"
        )

        lines.extend([

            f"{name}",

            f"API Symbol: {symbol}",

            f"Stage: {d['stage']}",

            f"WS: {ws_status}",

            f"Sub: {sub_status}",

            f"Live ticks: {d['ticks']}",

            f"History: {d['history']}",

            f"Training: {d['training']}",

            f"Features: {feature_status}",
        ])

        if d["features"]:

            lines.extend([

                f"Price: {f['price']:.5f}",

                f"EMA20: {f['ema20']:.5f}",

                f"EMA50: {f['ema50']:.5f}",

                f"RSI14: {f['rsi14']:.2f}",

                f"ATR14: {f['atr14']:.5f}",

                f"Momentum10: "
                f"{f['momentum10']:.4f}%",

                f"Volatility20: "
                f"{f['volatility20']:.5f}%",

                f"Trend: {f['trend']}",

                f"Direction: "
                f"{f['direction']}",
            ])

        if s["swing_high"] > 0:

            lines.extend([

                "",

                "📊 MARKET STRUCTURE",

                f"Swing High: "
                f"{s['swing_high']:.5f}",

                f"Prev High: "
                f"{s['previous_swing_high']:.5f}",

                f"Swing Low: "
                f"{s['swing_low']:.5f}",

                f"Prev Low: "
                f"{s['previous_swing_low']:.5f}",

                f"Structure: "
                f"{s['structure']}",

                f"BOS: {s['bos']}",

                f"CHoCH: {s['choch']}",

                f"Support: "
                f"{s['support']:.5f}",

                f"Resistance: "
                f"{s['resistance']:.5f}",

                f"Move Strength: "
                f"{s['move_strength']:.2f} ATR",

                f"Swing High Count: "
                f"{s['swing_high_count']}",

                f"Swing Low Count: "
                f"{s['swing_low_count']}",
            ])

        else:

            lines.extend([

                "",

                "📊 MARKET STRUCTURE",

                "Structure: WAITING ⏳",

                (
                    f"Swing High Count: "
                    f"{s['swing_high_count']}/2"
                ),

                (
                    f"Swing Low Count: "
                    f"{s['swing_low_count']}/2"
                ),

                (
                    f"Reason: "
                    f"{s['structure_error'] or 'UNKNOWN'}"
                ),
            ])

        # ====================================================
        # ADVANCED ANALYSIS
        # ====================================================

        if d["features"]:

            lines.extend([

                "",

                "🧠 ADVANCED ANALYSIS",

                f"ADX14: "
                f"{a['adx14']:.2f}",

                f"+DI: "
                f"{a['plus_di']:.2f}",

                f"-DI: "
                f"{a['minus_di']:.2f}",

                (
                    "Order Block: "
                    f"BULLISH={'YES' if a['ob_bullish'] else 'NO'} "
                    f"BEARISH={'YES' if a['ob_bearish'] else 'NO'}"
                ),

                (
                    "FVG/Imbalance: "
                    f"BULLISH={'YES' if a['fvg_bullish'] else 'NO'} "
                    f"BEARISH={'YES' if a['fvg_bearish'] else 'NO'}"
                ),

                f"Spike Setup: "
                f"{a['spike_score']:.0f}/100",

                f"Spike Direction: "
                f"{a['spike_direction']}",

                f"Compression: "
                f"{a['compression']:.2f}",

                f"Range Expansion: "
                f"{a['range_expansion']:.2f} ATR",
            ])

            # =================================================
            # SCORE
            # =================================================

            lines.extend([

                "",

                "🎯 SIGNAL SCORE V1",

                f"BUY SCORE: "
                f"{sc['buy']:.0f}/100",

                f"SELL SCORE: "
                f"{sc['sell']:.0f}/100",

                f"Decision: "
                f"{sc['decision']}",

                f"Strength: "
                f"{sc['strength']}",

                "",

                "Score Components:",

                f"Trend: "
                f"{sc['trend']:.0f}",

                f"RSI: "
                f"{sc['rsi']:.0f}",

                f"Momentum: "
                f"{sc['momentum']:.0f}",

                f"Structure: "
                f"{sc['structure']:.0f}",

                f"BOS/CHoCH: "
                f"{sc['bos_choch']:.0f}",

                f"S/R: "
                f"{sc['sr']:.0f}",

                f"Move Strength: "
                f"{sc['move_strength']:.0f}",

                f"ADX/DMI: "
                f"{sc['adx']:.0f}",

                f"Order Block: "
                f"{sc['order_block']:.0f}",

                f"FVG: "
                f"{sc['fvg']:.0f}",

                f"Spike: "
                f"{sc['spike']:.0f}",

                "Telegram Signal: OFF ❌",
            ])

        lines.extend([

            f"Err: "
            f"{d['error'] or 'NONE'}",

            "--------------------",
        ])

    text = "\n".join(lines)

    while len(text) > 3800:

        cut = text.rfind(
            "\n",
            0,
            3800
        )

        if cut <= 0:
            cut = 3800

        await update.message.reply_text(
            text[:cut]
        )

        text = text[cut:]

    if text:

        await update.message.reply_text(
            text
        )


# ============================================================
# /SYMBOLS
# ============================================================

async def symbols(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "👹🧠 AI МАНГАС V5 FIX\n\n"
        "ЗӨВХӨН 7 INDEX\n\n"
    )

    for i, (
        symbol,
        name
    ) in enumerate(
        INDICES.items(),
        start=1
    ):

        text += (
            f"{i}. {symbol}\n"
            f"   {name}\n\n"
        )

    await update.message.reply_text(
        text
    )


# ============================================================
# /RAWSTATUS
# ============================================================

async def rawstatus(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "👹🧠 AI МАНГАС V5 FIX\n\n"

        "DERIV NEW PUBLIC API\n\n"

        f"Endpoint:\n"
        f"{DERIV_PUBLIC_WS}\n\n"

        f"Active workers: "
        f"{len(active)}/7\n\n"

        "FEATURE + MARKET STRUCTURE + "
        "ADVANCED ANALYSIS + SCORE MODE\n\n"

        "BOS / CHoCH LOGIC FIX ON\n"

        "MOVE STRENGTH NORMALIZED FIX ON\n"

        "ADX / DMI ON\n"

        "ORDER BLOCK ON\n"

        "FVG / IMBALANCE ON\n"

        "SPIKE ANALYSIS ON\n"

        "SIGNAL SCORE V1 ON\n"

        "TELEGRAM SIGNAL: OFF"
    )


# ============================================================
# STARTUP
# ============================================================

async def startup(app):

    for symbol in INDICES:

        start_index(symbol)


# ============================================================
# TELEGRAM APPLICATION
# ============================================================

app = (
    ApplicationBuilder()
    .token(TOKEN)
    .post_init(startup)
    .build()
)


# ============================================================
# COMMANDS
# ============================================================

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
        "rawstatus",
        rawstatus
    )
)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run_polling()
