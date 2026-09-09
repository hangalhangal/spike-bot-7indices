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

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN missing")


# ============================================================
# AI МАНГАС V5 FIX
# STEP 3 — FEATURE ENGINE
# RSI FIX ONLY
# EXACT 7 BOOM / CRASH INDEX
# ============================================================

DERIV_PUBLIC_WS = (
    "wss://api.derivws.com/"
    "trading/v1/options/ws/public"
)


# ============================================================
# EXACT 7 INDEX
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


# ============================================================
# DATA
# ============================================================

active = set()

tasks = {}

ticks = {
    symbol: deque(maxlen=TICK_BUFFER)
    for symbol in INDICES
}


# ============================================================
# DIAGNOSTIC STATUS
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
# FEATURE DATA
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
# KEEP ALIVE
# ============================================================

def keep_alive():

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    class Handler(BaseHTTPRequestHandler):

        def do_GET(self):

            self.send_response(200)
            self.end_headers()

            self.wfile.write(
                b"AI MANGAS V5 FIX - FEATURE ENGINE"
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
# RESET
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
            (price - ema) * multiplier
            + ema
        )

    return ema


# ============================================================
# RSI — STANDARD WILDER RSI
# ONLY FIX IN THIS VERSION
# ============================================================

def calculate_rsi(prices, period=14):

    if len(prices) < period + 1:
        return None

    recent = prices[-(period + 1):]

    gains = []
    losses = []

    for i in range(1, len(recent)):

        change = (
            recent[i]
            - recent[i - 1]
        )

        if change > 0:

            gains.append(change)
            losses.append(0.0)

        else:

            gains.append(0.0)
            losses.append(abs(change))


    # Initial Wilder averages

    average_gain = (
        sum(gains)
        / period
    )

    average_loss = (
        sum(losses)
        / period
    )


    if average_loss == 0:

        if average_gain == 0:
            return 50.0

        return 100.0


    rs = (
        average_gain
        / average_loss
    )

    return 100.0 - (
        100.0 / (1.0 + rs)
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
        sum(
            true_ranges[-period:]
        )
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

    mean = (
        sum(changes)
        / len(changes)
    )

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


    # ========================================================
    # TREND CLASSIFICATION
    # ========================================================

    if ema20 > ema50:

        trend = "BULLISH"

    elif ema20 < ema50:

        trend = "BEARISH"

    else:

        trend = "NEUTRAL"


    # ========================================================
    # MOMENTUM DIRECTION
    # ========================================================

    if momentum10 > 0:

        direction = "UP"

    elif momentum10 < 0:

        direction = "DOWN"

    else:

        direction = "FLAT"


    # ========================================================
    # SAVE FEATURES
    # ========================================================

    features[symbol] = {
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "rsi14": rsi14,
        "atr14": atr14,
        "momentum10": momentum10,
        "volatility20": volatility20,
        "trend": trend,
        "direction": direction,
    }

    diag[symbol]["features"] = 1

    return True


# ============================================================
# HISTORY PROCESS
# ============================================================

def process_history(symbol, prices, times=None):

    if not isinstance(
        prices,
        list
    ):

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
            zip(
                times,
                prices
            )
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


    # ========================================================
    # TRAINING SAMPLE COUNT
    # ========================================================

    diag[symbol]["training"] = min(
        len(prices),
        TRAINING_COUNT
    )


    # ========================================================
    # INITIAL FEATURES
    # ========================================================

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
            # HISTORY
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
                    "ticks_history": symbol,
                    "count": HISTORY_COUNT,
                    "end": "latest",
                    "style": "ticks",
                    "req_id": 2000
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
            # LIVE
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
                    "ticks": symbol,
                    "subscribe": 1,
                    "req_id": 3000
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

                    # =================================================
                    # FEATURE CALCULATION
                    # =================================================

                    calculate_features(
                        symbol
                    )

                    diag[symbol]["stage"] = "LIVE"


        except asyncio.CancelledError:

            return


        except Exception as e:

            diag[symbol]["stage"] = "ERROR"

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

        tasks[symbol] = asyncio.create_task(
            deriv_worker(symbol)
        )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    for symbol in INDICES:

        start_index(symbol)

    await update.message.reply_text(
        "👹🧠 AI МАНГАС V5 FIX\n\n"
        "7 INDEX LIVE + FEATURE ENGINE ✅\n\n"
        "History → Live Tick → Features\n\n"
        "/status"
    )


# ============================================================
# STATUS
# ============================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    lines = [
        "👹🧠 AI МАНГАС V5 FIX",
        "",
        "🧠 FEATURE ENGINE STATUS",
        "========================",
        ""
    ]

    for symbol, name in INDICES.items():

        d = diag[symbol]
        f = features[symbol]

        ws_status = (
            "ON ✅"
            if d["connected"]
            else "OFF ❌"
        )

        sub_status = (
            "YES ✅"
            if d["subscribed"]
            else "NO ❌"
        )

        feature_status = (
            "READY ✅"
            if d["features"]
            else "WAITING ⏳"
        )

        lines.extend(
            [
                f"{name}",
                f"API Symbol: {symbol}",
                f"Stage: {d['stage']}",
                f"WS: {ws_status}",
                f"Sub: {sub_status}",
                f"Live ticks: {d['ticks']}",
                f"History: {d['history']}",
                f"Training: {d['training']}",
                f"Features: {feature_status}",
            ]
        )

        if d["features"]:

            lines.extend(
                [
                    f"Price: {f['price']:.5f}",
                    f"EMA20: {f['ema20']:.5f}",
                    f"EMA50: {f['ema50']:.5f}",
                    f"RSI14: {f['rsi14']:.2f}",
                    f"ATR14: {f['atr14']:.5f}",
                    f"Momentum10: {f['momentum10']:.4f}%",
                    f"Volatility20: {f['volatility20']:.5f}%",
                    f"Trend: {f['trend']}",
                    f"Direction: {f['direction']}",
                ]
            )

        lines.extend(
            [
                f"Err: {d['error'] or 'NONE'}",
                "--------------------",
            ]
        )

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
# SYMBOLS
# ============================================================

async def symbols(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "👹🧠 AI МАНГАС V5 FIX\n\n"
        "ЗӨВХӨН 7 INDEX\n\n"
    )

    for i, (symbol, name) in enumerate(
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
# RAW STATUS
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
        "FEATURE ENGINE MODE"
    )


# ============================================================
# STARTUP
# ============================================================

async def startup(app):

    for symbol in INDICES:

        start_index(symbol)


# ============================================================
# TELEGRAM APP
# ============================================================

app = (
    ApplicationBuilder()
    .token(TOKEN)
    .post_init(startup)
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
        "rawstatus",
        rawstatus
    )
)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run_polling()
