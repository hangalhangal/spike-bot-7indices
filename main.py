import os
import json
import time
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

# ============================================================
# AI МАНГАС V5 FIX
# SYMBOL DIAGNOSTIC ONLY
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

resolved_symbols = {}
raw_boom_crash = []

loader_status = "NOT STARTED"
loader_error = ""


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
                b"AI MANGAS V5 FIX - SYMBOL CHECK"
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
# DERIV ACTIVE SYMBOLS
# ============================================================

async def get_active_symbols():

    global loader_status
    global loader_error
    global raw_boom_crash
    global resolved_symbols

    loader_status = "CONNECTING"
    loader_error = ""

    uri = (
        "wss://ws.derivws.com/websockets/v3"
        f"?app_id={APP_ID}"
    )

    try:

        logging.info(
            "Connecting to Deriv..."
        )

        async with websockets.connect(
            uri,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=20
        ) as ws:

            loader_status = "REQUESTING"

            # IMPORTANT:
            # product_type intentionally removed
            request = {
                "active_symbols": "brief"
            }

            logging.info(
                "Sending active_symbols request..."
            )

            await ws.send(
                json.dumps(request)
            )

            raw = await asyncio.wait_for(
                ws.recv(),
                timeout=20
            )

            logging.info(
                "RAW RESPONSE: %s",
                raw[:2000]
            )

            msg = json.loads(raw)

            if "error" in msg:

                raise RuntimeError(
                    msg["error"].get(
                        "message",
                        "Deriv API error"
                    )
                )

            symbols = msg.get(
                "active_symbols"
            )

            if symbols is None:

                raise RuntimeError(
                    "Deriv response contains no "
                    "'active_symbols' field"
                )

            if not isinstance(symbols, list):

                raise RuntimeError(
                    "active_symbols is not a list"
                )

            loader_status = (
                f"RECEIVED {len(symbols)} SYMBOLS"
            )

            logging.info(
                "Received %s active symbols",
                len(symbols)
            )

            # ------------------------------------------------
            # FIND ALL BOOM / CRASH
            # ------------------------------------------------

            raw_boom_crash = []

            for item in symbols:

                if not isinstance(item, dict):
                    continue

                symbol = (
                    item.get("underlying_symbol")
                    or item.get("symbol")
                    or ""
                )

                name = (
                    item.get("underlying_symbol_name")
                    or item.get("display_name")
                    or ""
                )

                symbol_text = str(symbol)
                name_text = str(name)

                combined = (
                    symbol_text + " " + name_text
                ).upper()

                if (
                    "BOOM" in combined
                    or "CRASH" in combined
                ):

                    raw_boom_crash.append({
                        "symbol": symbol_text,
                        "name": name_text,
                        "raw": item
                    })

            logging.info(
                "Boom/Crash candidates: %s",
                len(raw_boom_crash)
            )

            # ------------------------------------------------
            # TRY TO RESOLVE OUR 7 INDEXES
            # ------------------------------------------------

            resolved_symbols = {}

            for key, wanted_name in INDICES.items():

                wanted_upper = wanted_name.upper()

                # First: exact name
                for item in raw_boom_crash:

                    if (
                        item["name"].upper()
                        == wanted_upper
                    ):

                        resolved_symbols[key] = (
                            item["symbol"]
                        )

                        break

                if key in resolved_symbols:
                    continue

                # Second: BOOM/CRASH + NUMBER
                number = (
                    key.replace("BOOM", "")
                       .replace("CRASH", "")
                )

                direction = (
                    "BOOM"
                    if key.startswith("BOOM")
                    else "CRASH"
                )

                for item in raw_boom_crash:

                    combined = (
                        item["symbol"]
                        + " "
                        + item["name"]
                    ).upper()

                    if (
                        direction in combined
                        and number in combined
                    ):

                        resolved_symbols[key] = (
                            item["symbol"]
                        )

                        break

            loader_status = (
                f"OK - {len(resolved_symbols)}/7 RESOLVED"
            )

            logging.info(
                "Resolved symbols: %s",
                resolved_symbols
            )

    except Exception as e:

        loader_status = "ERROR"

        loader_error = (
            f"{type(e).__name__}: {str(e)}"
        )

        logging.error(
            "DERIV SYMBOL ERROR: %s",
            loader_error
        )


# ============================================================
# /rawsymbols
# ============================================================

async def rawsymbols(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not raw_boom_crash:

        await update.message.reply_text(
            "🔎 DERIV RAW SYMBOL CHECK\n\n"
            f"Loader: {loader_status}\n\n"
            "❌ Boom/Crash symbol олдсонгүй.\n\n"
            f"Error: {loader_error or 'NONE'}"
        )

        return

    lines = [
        "🔎 DERIV RAW BOOM / CRASH SYMBOLS",
        "",
        f"Loader: {loader_status}",
        ""
    ]

    for item in raw_boom_crash:

        lines.append(
            f"SYMBOL: {item['symbol']}\n"
            f"NAME: {item['name']}\n"
            f"--------------------"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# /symbols
# ============================================================

async def symbols(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    lines = [
        "👹🧠 AI МАНГАС V5 FIX",
        "",
        "DERIV SYMBOL STATUS",
        "",
        f"Loader: {loader_status}",
        ""
    ]

    for key, name in INDICES.items():

        symbol = (
            resolved_symbols.get(key)
            or "NOT RESOLVED"
        )

        lines.append(
            f"{name}\n"
            f"API Symbol: {symbol}\n"
        )

    if loader_error:

        lines.append(
            f"ERROR:\n{loader_error}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# /status
# ============================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    lines = [
        "👹🧠 AI МАНГАС V5 FIX",
        "",
        "DERIV API CONNECTION STATUS",
        "",
        f"Symbol Loader: {loader_status}",
        ""
    ]

    for key, name in INDICES.items():

        symbol = (
            resolved_symbols.get(key)
            or "NOT RESOLVED"
        )

        if symbol != "NOT RESOLVED":

            state = "🟢 SYMBOL FOUND"

        else:

            state = "🔴 SYMBOL NOT FOUND"

        lines.append(
            f"{state}\n"
            f"{name}\n"
            f"API Symbol: {symbol}\n"
        )

    if loader_error:

        lines.append(
            f"Error:\n{loader_error}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# /start
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👹🧠 AI МАНГАС V5 FIX\n\n"
        "Deriv symbol diagnostic ажиллаж байна.\n\n"
        "Эхлээд /rawsymbols явуул."
    )


# ============================================================
# STARTUP
# ============================================================

async def startup(
    app
):

    # Telegram startup-ийг BLOCK хийхгүй.
    asyncio.create_task(
        get_active_symbols()
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
    CommandHandler("start", start)
)

app.add_handler(
    CommandHandler("rawsymbols", rawsymbols)
)

app.add_handler(
    CommandHandler("symbols", symbols)
)

app.add_handler(
    CommandHandler("status", status)
)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    app.run_polling()
