import os
import json
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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

APP_ID = os.getenv("DERIV_APP_ID", "1089")

loader_status = "NOT STARTED"
loader_error = ""

all_symbols = []


# ============================================================
# KEEP ALIVE
# ============================================================

def keep_alive():

    port = int(os.environ.get("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"AI MANGAS V5 FIX - DEBUG SYMBOLS"
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
# GET ALL DERIV SYMBOLS
# ============================================================

async def load_symbols():

    global loader_status
    global loader_error
    global all_symbols

    uri = (
        "wss://ws.derivws.com/websockets/v3"
        f"?app_id={APP_ID}"
    )

    try:

        loader_status = "CONNECTING"

        async with websockets.connect(
            uri,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=20
        ) as ws:

            loader_status = "REQUESTING"

            await ws.send(
                json.dumps({
                    "active_symbols": "brief"
                })
            )

            raw = await asyncio.wait_for(
                ws.recv(),
                timeout=20
            )

            logging.info(
                "DERIV RAW RESPONSE: %s",
                raw[:5000]
            )

            msg = json.loads(raw)

            if "error" in msg:

                raise RuntimeError(
                    msg["error"].get(
                        "message",
                        "Unknown Deriv error"
                    )
                )

            data = msg.get("active_symbols")

            if data is None:

                raise RuntimeError(
                    "No active_symbols field in response"
                )

            if not isinstance(data, list):

                raise RuntimeError(
                    "active_symbols is not a list"
                )

            all_symbols = data

            loader_status = (
                f"OK - {len(data)} SYMBOLS RECEIVED"
            )

            logging.info(
                "TOTAL SYMBOLS: %s",
                len(data)
            )

    except Exception as e:

        loader_status = "ERROR"

        loader_error = (
            f"{type(e).__name__}: {str(e)}"
        )

        logging.error(
            "SYMBOL DEBUG ERROR: %s",
            loader_error
        )


# ============================================================
# DEBUG SYMBOLS
# ============================================================

async def debugsymbols(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not all_symbols:

        await update.message.reply_text(
            "🔎 DERIV SYMBOL DEBUG\n\n"
            f"Loader: {loader_status}\n\n"
            "❌ Symbol жагсаалт хоосон байна.\n\n"
            f"Error: {loader_error or 'NONE'}"
        )

        return

    lines = [
        "🔎 DERIV ACTIVE SYMBOL DEBUG",
        "",
        f"Loader: {loader_status}",
        f"Total: {len(all_symbols)}",
        "",
        "FIRST 100 SYMBOLS:",
        "===================="
    ]

    for i, item in enumerate(
        all_symbols[:100],
        start=1
    ):

        if not isinstance(item, dict):
            lines.append(
                f"{i}. {str(item)[:150]}"
            )
            continue

        symbol = (
            item.get("underlying_symbol")
            or item.get("symbol")
            or "N/A"
        )

        name = (
            item.get("underlying_symbol_name")
            or item.get("display_name")
            or "N/A"
        )

        market = item.get(
            "market",
            "N/A"
        )

        submarket = item.get(
            "submarket",
            "N/A"
        )

        lines.append(
            f"{i}.\n"
            f"SYMBOL: {symbol}\n"
            f"NAME: {name}\n"
            f"MARKET: {market}\n"
            f"SUBMARKET: {submarket}\n"
            f"--------------------"
        )

    # Telegram message length limit
    text = "\n".join(lines)

    chunks = []

    while len(text) > 3500:

        cut = text.rfind(
            "\n",
            0,
            3500
        )

        if cut <= 0:
            cut = 3500

        chunks.append(
            text[:cut]
        )

        text = text[cut:]

    if text:
        chunks.append(text)

    for chunk in chunks:

        await update.message.reply_text(
            chunk
        )


# ============================================================
# SIMPLE STATUS
# ============================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👹🧠 AI МАНГАС V5 FIX\n\n"
        "DERIV SYMBOL DEBUG\n\n"
        f"Loader: {loader_status}\n"
        f"Total symbols: {len(all_symbols)}\n"
        f"Error: {loader_error or 'NONE'}"
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👹🧠 AI МАНГАС V5 FIX\n\n"
        "Deriv symbol diagnostic ажиллаж байна.\n\n"
        "Одоо /debugsymbols явуул."
    )


# ============================================================
# STARTUP
# ============================================================

async def startup(app):

    # Telegram startup-ийг блоклохгүй
    asyncio.create_task(
        load_symbols()
    )


# ============================================================
# TELEGRAM
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
        "debugsymbols",
        debugsymbols
    )
)

app.add_handler(
    CommandHandler(
        "status",
        status
    )
)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    app.run_polling()
