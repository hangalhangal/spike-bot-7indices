import os
import json
import time
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


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN missing")

APP_ID = os.getenv("DERIV_APP_ID", "1089")


# =========================================================
# 7 INDEX
# =========================================================

INDICES = {
    "BOOM1000": "Boom 1000 Index",
    "BOOM500": "Boom 500 Index",
    "BOOM600": "Boom 600 Index",
    "BOOM900": "Boom 900 Index",
    "CRASH1000": "Crash 1000 Index",
    "CRASH500": "Crash 500 Index",
    "CRASH900": "Crash 900 Index",
}


# =========================================================
# STATE
# =========================================================

active_symbols_raw = []

api_status = {
    "stage": "IDLE",
    "connected": 0,
    "error": ""
}


# =========================================================
# KEEP ALIVE
# =========================================================

def keep_alive():

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    class H(BaseHTTPRequestHandler):

        def do_GET(self):

            self.send_response(200)
            self.end_headers()

            self.wfile.write(
                b"AI MANIAC SYMBOL DIAGNOSTIC ONLINE"
            )

        def log_message(self, *args):
            pass

    HTTPServer(
        ("0.0.0.0", port),
        H
    ).serve_forever()


threading.Thread(
    target=keep_alive,
    daemon=True
).start()


# =========================================================
# GET ACTIVE SYMBOLS FROM DERIV
# =========================================================

async def fetch_active_symbols():

    global active_symbols_raw

    uri = (
        f"wss://ws.derivws.com/"
        f"websockets/v3?app_id={APP_ID}"
    )

    api_status["stage"] = "CONNECTING"
    api_status["connected"] = 0
    api_status["error"] = ""

    try:

        async with websockets.connect(
            uri,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=15,
            close_timeout=5
        ) as ws:

            api_status["connected"] = 1
            api_status["stage"] = "REQUEST"

            await ws.send(
                json.dumps({
                    "active_symbols": "brief"
                })
            )

            raw = await asyncio.wait_for(
                ws.recv(),
                timeout=20
            )

            msg = json.loads(raw)

            if "error" in msg:

                raise RuntimeError(
                    msg["error"].get(
                        "message",
                        "Deriv API error"
                    )
                )

            if not msg.get("active_symbols"):

                raise RuntimeError(
                    "Deriv returned no active_symbols"
                )

            active_symbols_raw = (
                msg["active_symbols"]
            )

            api_status["stage"] = "READY"

            logging.info(
                "Received %d active symbols",
                len(active_symbols_raw)
            )

            return True

    except Exception as e:

        api_status["stage"] = "ERROR"
        api_status["connected"] = 0
        api_status["error"] = (
            f"{type(e).__name__}: "
            f"{str(e)[:200]}"
        )

        logging.error(
            "Deriv symbol diagnostic error: %s",
            api_status["error"]
        )

        return False


# =========================================================
# FILTER BOOM / CRASH
# =========================================================

def get_boom_crash_symbols():

    results = []

    for item in active_symbols_raw:

        symbol = str(
            item.get(
                "symbol",
                ""
            )
        )

        display_name = str(
            item.get(
                "display_name",
                ""
            )
        )

        underlying_symbol = str(
            item.get(
                "underlying_symbol",
                ""
            )
        )

        underlying_name = str(
            item.get(
                "underlying_symbol_name",
                ""
            )
        )

        combined = (
            f"{symbol} "
            f"{display_name} "
            f"{underlying_symbol} "
            f"{underlying_name}"
        ).lower()

        if (
            "boom" in combined
            or
            "crash" in combined
        ):

            results.append({
                "symbol": symbol,
                "display_name": display_name,
                "underlying_symbol": underlying_symbol,
                "underlying_symbol_name": underlying_name,
            })

    return results


# =========================================================
# /symbols
# =========================================================

async def symbols(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔎 Deriv API symbol шалгаж байна..."
    )

    ok = await fetch_active_symbols()

    if not ok:

        await update.message.reply_text(
            "❌ DERIV API SYMBOL ERROR\n\n"
            f"Stage: {api_status['stage']}\n"
            f"WS: "
            f"{'ON ✅' if api_status['connected'] else 'OFF ❌'}\n"
            f"Error: {api_status['error']}"
        )

        return

    results = get_boom_crash_symbols()

    if not results:

        await update.message.reply_text(
            "❌ BOOM / CRASH SYMBOL ОЛДСОНГҮЙ.\n\n"
            f"Total active symbols: "
            f"{len(active_symbols_raw)}\n"
            f"API stage: {api_status['stage']}"
        )

        return

    # Telegram message length limit хамгаалалт
    lines = [
        "📡 DERIV RAW BOOM / CRASH SYMBOLS",
        "",
        f"Total API symbols: {len(active_symbols_raw)}",
        f"Boom/Crash found: {len(results)}",
        ""
    ]

    for i, item in enumerate(
        results,
        start=1
    ):

        lines.append(
            f"{i}. "
            f"symbol = {item['symbol']}"
        )

        lines.append(
            f"   display = "
            f"{item['display_name']}"
        )

        lines.append(
            f"   underlying = "
            f"{item['underlying_symbol']}"
        )

        lines.append(
            f"   underlying_name = "
            f"{item['underlying_symbol_name']}"
        )

        lines.append("")

    text = "\n".join(lines)

    # Telegram 4096 character limit
    if len(text) <= 4000:

        await update.message.reply_text(
            text
        )

    else:

        # Хэсэгчилж илгээнэ
        chunk = ""

        for line in lines:

            if len(chunk) + len(line) + 1 > 3800:

                await update.message.reply_text(
                    chunk
                )

                chunk = ""

            chunk += line + "\n"

        if chunk:

            await update.message.reply_text(
                chunk
            )


# =========================================================
# /symbol
# =========================================================

async def symbol(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await symbols(
        update,
        context
    )


# =========================================================
# /status
# =========================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🧠 AI MANIAC SYMBOL DIAGNOSTIC\n\n"
        f"API Stage: {api_status['stage']}\n"
        f"WS: "
        f"{'ON ✅' if api_status['connected'] else 'OFF ❌'}\n"
        f"Active symbols loaded: "
        f"{len(active_symbols_raw)}\n"
        f"Error: "
        f"{api_status['error'] or 'NONE'}"
    )


# =========================================================
# /start
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🧠🔥 AI MANIAC V5 FIX\n\n"
        "SYMBOL DIAGNOSTIC MODE\n\n"
        "Командууд:\n"
        "/symbols - Deriv-ийн бодит Boom/Crash symbol\n"
        "/symbol - ижил\n"
        "/status - API status"
    )


# =========================================================
# APPLICATION
# =========================================================

app = (
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
        "symbols",
        symbols
    )
)

app.add_handler(
    CommandHandler(
        "symbol",
        symbol
    )
)

app.add_handler(
    CommandHandler(
        "status",
        status
    )
)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    app.run_polling()
