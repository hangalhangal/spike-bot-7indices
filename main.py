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


# ============================================================
# AI МАНГАС V5 FIX
# DERIV NEW PUBLIC API SYMBOL DIAGNOSTIC
# ============================================================

DERIV_PUBLIC_WS = (
    "wss://api.derivws.com/"
    "trading/v1/options/ws/public"
)

loader_status = "NOT STARTED"
loader_error = ""

all_symbols = []


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
                b"AI MANGAS V5 FIX - NEW DERIV API"
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
# LOAD ACTIVE SYMBOLS
# ============================================================

async def load_symbols():

    global loader_status
    global loader_error
    global all_symbols

    loader_status = "CONNECTING"
    loader_error = ""
    all_symbols = []

    try:

        logging.info(
            "Connecting to NEW Deriv Public WebSocket..."
        )

        async with websockets.connect(
            DERIV_PUBLIC_WS,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=20
        ) as ws:

            loader_status = "CONNECTED"

            request = {
                "active_symbols": "brief",
                "req_id": 1001
            }

            logging.info(
                "Sending active_symbols..."
            )

            await ws.send(
                json.dumps(request)
            )

            raw = await asyncio.wait_for(
                ws.recv(),
                timeout=20
            )

            logging.info(
                "RAW DERIV RESPONSE:"
            )

            logging.info(
                raw[:10000]
            )

            msg = json.loads(raw)

            # ------------------------------------------------
            # ERROR
            # ------------------------------------------------

            if "error" in msg:

                raise RuntimeError(
                    msg["error"].get(
                        "message",
                        "Deriv API error"
                    )
                )

            # ------------------------------------------------
            # CHECK MESSAGE TYPE
            # ------------------------------------------------

            msg_type = msg.get(
                "msg_type",
                ""
            )

            logging.info(
                "MESSAGE TYPE: %s",
                msg_type
            )

            # ------------------------------------------------
            # ACTIVE SYMBOLS
            # ------------------------------------------------

            symbols = msg.get(
                "active_symbols"
            )

            if symbols is None:

                raise RuntimeError(
                    "Response has no active_symbols field"
                )

            if not isinstance(
                symbols,
                list
            ):

                raise RuntimeError(
                    "active_symbols is not a list"
                )

            all_symbols = symbols

            loader_status = (
                f"OK - {len(symbols)} SYMBOLS RECEIVED"
            )

            logging.info(
                "TOTAL SYMBOLS: %s",
                len(symbols)
            )

    except Exception as e:

        loader_status = "ERROR"

        loader_error = (
            f"{type(e).__name__}: "
            f"{str(e)}"
        )

        logging.error(
            "DERIV ERROR: %s",
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
            "🔎 DERIV NEW API DEBUG\n\n"
            f"Status: {loader_status}\n\n"
            "❌ active_symbols хоосон байна.\n\n"
            f"Error: "
            f"{loader_error or 'NONE'}"
        )

        return

    # --------------------------------------------------------
    # First find Boom / Crash
    # --------------------------------------------------------

    boom_crash = []

    for item in all_symbols:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = (
            item.get(
                "underlying_symbol"
            )
            or item.get(
                "symbol"
            )
            or ""
        )

        name = (
            item.get(
                "underlying_symbol_name"
            )
            or item.get(
                "display_name"
            )
            or ""
        )

        combined = (
            str(symbol)
            + " "
            + str(name)
        ).upper()

        if (
            "BOOM" in combined
            or "CRASH" in combined
        ):

            boom_crash.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "market": item.get(
                        "market",
                        ""
                    ),
                    "submarket": item.get(
                        "submarket",
                        ""
                    )
                }
            )

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    lines = [
        "👹🧠 AI МАНГАС V5 FIX",
        "",
        "🔎 DERIV NEW API",
        "BOOM / CRASH SYMBOL CHECK",
        "",
        f"Loader: {loader_status}",
        f"Total symbols: {len(all_symbols)}",
        f"Boom/Crash found: {len(boom_crash)}",
        ""
    ]

    if boom_crash:

        for i, item in enumerate(
            boom_crash,
            start=1
        ):

            lines.append(
                f"{i}.\n"
                f"SYMBOL: {item['symbol']}\n"
                f"NAME: {item['name']}\n"
                f"MARKET: {item['market']}\n"
                f"SUBMARKET: {item['submarket']}\n"
                f"--------------------"
            )

    else:

        lines.append(
            "❌ Boom/Crash symbol олдсонгүй."
        )

        lines.append("")

        lines.append(
            "FIRST 30 SYMBOLS:"
        )

        lines.append(
            "===================="
        )

        for i, item in enumerate(
            all_symbols[:30],
            start=1
        ):

            if isinstance(
                item,
                dict
            ):

                symbol = (
                    item.get(
                        "underlying_symbol"
                    )
                    or item.get(
                        "symbol"
                    )
                    or "N/A"
                )

                name = (
                    item.get(
                        "underlying_symbol_name"
                    )
                    or item.get(
                        "display_name"
                    )
                    or "N/A"
                )

                lines.append(
                    f"{i}. "
                    f"{symbol} — {name}"
                )

    text = "\n".join(lines)

    # Telegram max message protection
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
# RAW RESPONSE SUMMARY
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
        f"Loader: {loader_status}\n"
        f"Total symbols: "
        f"{len(all_symbols)}\n"
        f"Error: "
        f"{loader_error or 'NONE'}"
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
        "NEW DERIV PUBLIC API diagnostic "
        "ажиллаж байна.\n\n"
        "Одоо /debugsymbols явуул."
    )


# ============================================================
# STARTUP
# ============================================================

async def startup(app):

    asyncio.create_task(
        load_symbols()
    )


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
        "debugsymbols",
        debugsymbols
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
