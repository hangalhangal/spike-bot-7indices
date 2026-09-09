import os
import json
import asyncio
import logging
import threading
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


# ============================================================
# TOKEN
# ============================================================

TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN missing")


# ============================================================
# AI МАНГАС V5 FIX
# DERIV NEW PUBLIC API
# 7 BOOM / CRASH INDEX
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
# DATA
# ============================================================

active = set()

tasks = {}

ticks = {
    symbol: deque(maxlen=500)
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
        "error": "",
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
                b"AI MANGAS V5 FIX - 7 INDEX LIVE"
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
# RESET STATUS
# ============================================================

def reset_diag(symbol):

    diag[symbol] = {
        "stage": "IDLE",
        "connected": 0,
        "subscribed": 0,
        "ticks": 0,
        "history": 0,
        "training": 0,
        "error": "",
    }

    ticks[symbol].clear()


# ============================================================
# PROCESS HISTORY
# ============================================================

def process_history(symbol, prices):

    if not isinstance(prices, list):
        raise RuntimeError(
            "History prices is not a list"
        )

    if len(prices) == 0:
        raise RuntimeError(
            "History returned 0 prices"
        )

    diag[symbol]["history"] = len(prices)

    # Keep latest 500 prices for live processing
    ticks[symbol].clear()

    latest_prices = prices[-500:]

    for price in latest_prices:

        try:

            ticks[symbol].append(
                (
                    0.0,
                    float(price)
                )
            )

        except Exception:
            continue

    # --------------------------------------------------------
    # Training sample count
    # --------------------------------------------------------

    diag[symbol]["training"] = min(
        len(prices),
        300
    )


# ============================================================
# DERIV CONNECTION
# ============================================================

async def deriv_worker(symbol):

    while symbol in active:

        reset_diag(symbol)

        try:

            # =================================================
            # HISTORY CONNECTION
            # =================================================

            diag[symbol]["stage"] = "CONNECTING HISTORY"

            logging.info(
                "[%s] Connecting for history...",
                symbol
            )

            async with websockets.connect(
                DERIV_PUBLIC_WS,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=20
            ) as ws:

                diag[symbol]["connected"] = 1

                diag[symbol]["stage"] = "HISTORY"

                history_request = {
                    "ticks_history": symbol,
                    "count": 5000,
                    "end": "latest",
                    "style": "ticks",
                    "req_id": 2000
                }

                logging.info(
                    "[%s] Requesting 5000 history...",
                    symbol
                )

                await ws.send(
                    json.dumps(history_request)
                )

                history_received = False

                while True:

                    raw = await asyncio.wait_for(
                        ws.recv(),
                        timeout=30
                    )

                    msg = json.loads(raw)

                    if "error" in msg:

                        error_message = (
                            msg["error"].get(
                                "message",
                                "Deriv API error"
                            )
                        )

                        raise RuntimeError(
                            error_message
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

                        process_history(
                            symbol,
                            prices
                        )

                        history_received = True

                        logging.info(
                            "[%s] History received: %s",
                            symbol,
                            len(prices)
                        )

                        break

                if not history_received:

                    raise RuntimeError(
                        "History response not received"
                    )

            # =================================================
            # LIVE CONNECTION
            # =================================================

            diag[symbol]["stage"] = (
                "CONNECTING LIVE"
            )

            logging.info(
                "[%s] Connecting for live ticks...",
                symbol
            )

            async with websockets.connect(
                DERIV_PUBLIC_WS,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=20
            ) as ws:

                diag[symbol]["connected"] = 1

                diag[symbol]["stage"] = "LIVE"

                live_request = {
                    "ticks": symbol,
                    "subscribe": 1,
                    "req_id": 3000
                }

                logging.info(
                    "[%s] Subscribing live ticks...",
                    symbol
                )

                await ws.send(
                    json.dumps(live_request)
                )

                while symbol in active:

                    raw = await asyncio.wait_for(
                        ws.recv(),
                        timeout=60
                    )

                    msg = json.loads(raw)

                    if "error" in msg:

                        error_message = (
                            msg["error"].get(
                                "message",
                                "Deriv API error"
                            )
                        )

                        raise RuntimeError(
                            error_message
                        )

                    # ------------------------------------------------
                    # LIVE TICK
                    # ------------------------------------------------

                    tick = msg.get(
                        "tick"
                    )

                    if isinstance(
                        tick,
                        dict
                    ):

                        quote = tick.get(
                            "quote"
                        )

                        epoch = tick.get(
                            "epoch",
                            0
                        )

                        if quote is not None:

                            price = float(
                                quote
                            )

                            ticks[symbol].append(
                                (
                                    float(epoch),
                                    price
                                )
                            )

                            diag[symbol][
                                "subscribed"
                            ] = 1

                            diag[symbol][
                                "ticks"
                            ] += 1

                            diag[symbol][
                                "stage"
                            ] = "LIVE"

                            logging.info(
                                "[%s] LIVE tick: %s",
                                symbol,
                                price
                            )

            if symbol in active:

                raise RuntimeError(
                    "Live WebSocket closed"
                )

        except asyncio.CancelledError:

            logging.info(
                "[%s] Worker cancelled",
                symbol
            )

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
                "[%s] ERROR: %s",
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
# STOP INDEX
# ============================================================

def stop_index(symbol):

    active.discard(symbol)

    task = tasks.get(symbol)

    if task and not task.done():

        task.cancel()


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
        "7 INDEX CONNECTION STARTED ✅\n\n"
        "5000 History → Training 300 → "
        "Live Tick\n\n"
        "/status — одоогийн төлөв"
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
        "📡 7 INDEX LIVE STATUS",
        "====================",
        ""
    ]

    for symbol, name in INDICES.items():

        d = diag[symbol]

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
                f"Err: {d['error'] or 'NONE'}",
                "--------------------",
            ]
        )

    text = "\n".join(lines)

    # Telegram message limit protection
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
        "7 INDEX CONNECTION MODE"
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
