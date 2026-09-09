import os
import json
import time
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import deque

import websockets

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN missing")

APP_ID = os.getenv("DERIV_APP_ID", "1089")

# ============================================================
# AI МАНГАС V5 FIX
# ONLY 7 BOOM / CRASH INDICES
# ============================================================

INDICES = {
    "BOOM1000": {"name": "Boom 1000 Index"},
    "BOOM500": {"name": "Boom 500 Index"},
    "BOOM600": {"name": "Boom 600 Index"},
    "BOOM900": {"name": "Boom 900 Index"},
    "CRASH1000": {"name": "Crash 1000 Index"},
    "CRASH500": {"name": "Crash 500 Index"},
    "CRASH900": {"name": "Crash 900 Index"},
}

active = set()
chat_ids = set()

ticks = {
    k: deque(maxlen=500)
    for k in INDICES
}

diag = {
    k: {
        "stage": "IDLE",
        "connected": 0,
        "subscribed": 0,
        "ticks": 0,
        "history": 0,
        "error": "",
        "symbol": "",
    }
    for k in INDICES
}

tasks = {}

# ============================================================
# SYMBOL DATABASE
# ============================================================

resolved_symbols = {}

symbol_loader_done = False
symbol_loader_error = ""


# ============================================================
# KEEP ALIVE
# ============================================================

def keep_alive():
    port = int(os.environ.get("PORT", "10000"))

    class H(BaseHTTPRequestHandler):

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"AI MANGAS V5 FIX - SYMBOL DIAGNOSTIC")

        def log_message(self, *args):
            pass

    HTTPServer(("0.0.0.0", port), H).serve_forever()


threading.Thread(
    target=keep_alive,
    daemon=True
).start()


# ============================================================
# GET ACTIVE SYMBOLS
# ============================================================

async def load_deriv_symbols():

    global symbol_loader_done
    global symbol_loader_error

    uri = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"

    try:

        logging.info("Connecting to Deriv for active_symbols...")

        async with websockets.connect(
            uri,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=15
        ) as ws:

            diag_global = "CONNECTED"

            await ws.send(
                json.dumps({
                    "active_symbols": "brief",
                    "product_type": "basic"
                })
            )

            raw = await asyncio.wait_for(
                ws.recv(),
                timeout=15
            )

            msg = json.loads(raw)

            logging.info(
                "Deriv active_symbols response received"
            )

            if "error" in msg:

                raise RuntimeError(
                    msg["error"].get(
                        "message",
                        "Unknown Deriv error"
                    )
                )

            symbols = msg.get("active_symbols")

            if not symbols:

                raise RuntimeError(
                    "Deriv returned no active_symbols"
                )

            logging.info(
                "Deriv returned %s active symbols",
                len(symbols)
            )

            # ------------------------------------------------
            # SEARCH ALL BOOM / CRASH ENTRIES
            # ------------------------------------------------

            found = []

            for item in symbols:

                if not isinstance(item, dict):
                    continue

                symbol = (
                    item.get("underlying_symbol")
                    or item.get("symbol")
                    or ""
                )

                display = (
                    item.get("underlying_symbol_name")
                    or item.get("display_name")
                    or ""
                )

                symbol_upper = str(symbol).upper()
                display_upper = str(display).upper()

                if (
                    "BOOM" in symbol_upper
                    or "CRASH" in symbol_upper
                    or "BOOM" in display_upper
                    or "CRASH" in display_upper
                ):

                    found.append({
                        "symbol": symbol,
                        "display": display,
                        "raw": item
                    })

            logging.info(
                "Boom/Crash candidates found: %s",
                len(found)
            )

            # ------------------------------------------------
            # SAVE EXACT API SYMBOLS
            # ------------------------------------------------

            for key, info in INDICES.items():

                wanted_name = info["name"].upper()

                candidates = []

                for item in found:

                    symbol = str(
                        item["symbol"]
                    ).upper()

                    display = str(
                        item["display"]
                    ).upper()

                    # Exact display-name match
                    if wanted_name == display:
                        candidates.append(item)
                        continue

                    # Match index number + Boom/Crash
                    if key.startswith("BOOM"):

                        if (
                            "BOOM" in symbol
                            and key[4:] in symbol
                        ):
                            candidates.append(item)

                        elif (
                            "BOOM" in display
                            and key[4:] in display
                        ):
                            candidates.append(item)

                    elif key.startswith("CRASH"):

                        if (
                            "CRASH" in symbol
                            and key[5:] in symbol
                        ):
                            candidates.append(item)

                        elif (
                            "CRASH" in display
                            and key[5:] in display
                        ):
                            candidates.append(item)

                if candidates:

                    # Prefer exact display match
                    exact = [
                        x for x in candidates
                        if str(x["display"]).upper()
                        == wanted_name
                    ]

                    selected = (
                        exact[0]
                        if exact
                        else candidates[0]
                    )

                    real_symbol = selected["symbol"]

                    resolved_symbols[key] = real_symbol

                    diag[key]["symbol"] = real_symbol
                    diag[key]["stage"] = "SYMBOL"
                    diag[key]["error"] = ""

                    logging.info(
                        "%s -> %s",
                        key,
                        real_symbol
                    )

                else:

                    diag[key]["symbol"] = ""
                    diag[key]["stage"] = "SYMBOL"
                    diag[key]["error"] = (
                        "No matching Boom/Crash symbol found"
                    )

            symbol_loader_done = True

    except Exception as e:

        symbol_loader_error = (
            f"{type(e).__name__}: {str(e)[:150]}"
        )

        logging.error(
            "SYMBOL LOADER ERROR: %s",
            symbol_loader_error
        )

        for k in INDICES:

            diag[k]["stage"] = "ERROR"
            diag[k]["error"] = symbol_loader_error

        symbol_loader_done = False


# ============================================================
# SYMBOL LOADER LOOP
# ============================================================

async def symbol_loader_loop():

    while True:

        await load_deriv_symbols()

        if resolved_symbols:
            return

        await asyncio.sleep(10)


# ============================================================
# DERIV HISTORY + LIVE
# ============================================================

async def deriv_ws(k, app):

    while k in active:

        api_symbol = resolved_symbols.get(k)

        if not api_symbol:

            diag[k]["stage"] = "SYMBOL"
            diag[k]["connected"] = 0
            diag[k]["subscribed"] = 0
            diag[k]["error"] = (
                "Waiting for real Deriv symbol"
            )

            await asyncio.sleep(3)
            continue

        uri = (
            f"wss://ws.derivws.com/websockets/v3"
            f"?app_id={APP_ID}"
        )

        try:

            # =================================================
            # HISTORY
            # =================================================

            diag[k]["stage"] = "HISTORY"
            diag[k]["connected"] = 0
            diag[k]["subscribed"] = 0

            async with websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=15
            ) as ws:

                diag[k]["connected"] = 1

                await ws.send(
                    json.dumps({
                        "ticks_history": api_symbol,
                        "count": 5000,
                        "end": "latest",
                        "style": "ticks"
                    })
                )

                while True:

                    raw = await asyncio.wait_for(
                        ws.recv(),
                        timeout=20
                    )

                    msg = json.loads(raw)

                    if "error" in msg:

                        raise RuntimeError(
                            msg["error"].get(
                                "message",
                                "History error"
                            )
                        )

                    history = msg.get("history")

                    if history:

                        prices = history.get(
                            "prices",
                            []
                        )

                        diag[k]["history"] = len(
                            prices
                        )

                        ticks[k].clear()

                        for price in prices[-500:]:

                            ticks[k].append(
                                (
                                    time.time(),
                                    float(price)
                                )
                            )

                        break

            # =================================================
            # LIVE TICKS
            # =================================================

            diag[k]["stage"] = "LIVE"

            async with websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=15
            ) as ws:

                diag[k]["connected"] = 1

                await ws.send(
                    json.dumps({
                        "ticks": api_symbol,
                        "subscribe": 1
                    })
                )

                async for raw in ws:

                    msg = json.loads(raw)

                    if "error" in msg:

                        raise RuntimeError(
                            msg["error"].get(
                                "message",
                                "Live tick error"
                            )
                        )

                    tick = msg.get("tick")

                    if not isinstance(tick, dict):
                        continue

                    quote = tick.get("quote")

                    if quote is None:
                        continue

                    epoch = tick.get(
                        "epoch",
                        time.time()
                    )

                    diag[k]["subscribed"] = 1
                    diag[k]["ticks"] += 1

                    ticks[k].append(
                        (
                            float(epoch),
                            float(quote)
                        )
                    )

        except asyncio.CancelledError:

            return

        except Exception as e:

            diag[k]["stage"] = "ERROR"
            diag[k]["connected"] = 0
            diag[k]["subscribed"] = 0

            diag[k]["error"] = (
                f"{type(e).__name__}: "
                f"{str(e)[:150]}"
            )

            logging.error(
                "%s ERROR: %s",
                k,
                diag[k]["error"]
            )

            await asyncio.sleep(5)


# ============================================================
# BUTTONS
# ============================================================

def buttons():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{'🟢' if k in active else '⚪'} "
                    f"{v['name']}",
                    callback_data=k
                )
            ]
            for k, v in INDICES.items()
        ]
    )


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_ids.add(
        update.effective_chat.id
    )

    for k in INDICES:

        active.add(k)

        if (
            k not in tasks
            or tasks[k].done()
        ):

            tasks[k] = asyncio.create_task(
                deriv_ws(
                    k,
                    context.application
                )
            )

    await update.message.reply_text(
        "👹🧠 AI МАНГАС V5 FIX\n\n"
        "7 BOOM / CRASH INDEX\n"
        "Symbol resolution + Live tick ON",
        reply_markup=buttons()
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

    k = q.data

    if k in active:

        active.remove(k)

        task = tasks.get(k)

        if task:
            task.cancel()

    else:

        active.add(k)

        tasks[k] = asyncio.create_task(
            deriv_ws(
                k,
                context.application
            )
        )

    try:

        await q.edit_message_reply_markup(
            reply_markup=buttons()
        )

    except Exception:
        pass


# ============================================================
# STATUS
# ============================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(
        update.effective_chat.id
    )

    for k in INDICES:

        info = INDICES[k]

        symbol = (
            resolved_symbols.get(k)
            or "NOT RESOLVED"
        )

        text = (
            f"{'🟢' if k in active else '⚪'} "
            f"{info['name']}\n"
            f"API Symbol: {symbol}\n"
            f"Stage: {diag[k]['stage']}\n"
            f"WS: "
            f"{'ON ✅' if diag[k]['connected'] else 'OFF ❌'}\n"
            f"Sub: "
            f"{'YES ✅' if diag[k]['subscribed'] else 'NO ❌'}\n"
            f"Live ticks: {diag[k]['ticks']}\n"
            f"History: {diag[k]['history']}\n"
            f"Err: {diag[k]['error']}\n"
        )

        await update.message.reply_text(text)


# ============================================================
# SYMBOLS
# ============================================================

async def symbols(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(
        update.effective_chat.id
    )

    lines = [
        "👹🧠 AI МАНГАС V5 FIX",
        "",
        "DERIV API SYMBOL STATUS",
        ""
    ]

    for k, info in INDICES.items():

        symbol = (
            resolved_symbols.get(k)
            or "NOT RESOLVED"
        )

        lines.append(
            f"{'🟢' if symbol != 'NOT RESOLVED' else '⚪'} "
            f"{info['name']}\n"
            f"API Symbol: {symbol}\n"
        )

    if symbol_loader_error:

        lines.append(
            f"\nLoader error:\n"
            f"{symbol_loader_error}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# RAW SYMBOL DIAGNOSTIC
# ============================================================

async def raw_symbols(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(
        update.effective_chat.id
    )

    uri = (
        f"wss://ws.derivws.com/websockets/v3"
        f"?app_id={APP_ID}"
    )

    try:

        async with websockets.connect(
            uri,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=15
        ) as ws:

            await ws.send(
                json.dumps({
                    "active_symbols": "brief",
                    "product_type": "basic"
                })
            )

            raw = await asyncio.wait_for(
                ws.recv(),
                timeout=15
            )

            msg = json.loads(raw)

            if "error" in msg:

                await update.message.reply_text(
                    "❌ DERIV ERROR\n\n"
                    + str(msg["error"])
                )
                return

            symbols_data = msg.get(
                "active_symbols",
                []
            )

            lines = [
                "🔎 RAW DERIV BOOM / CRASH SYMBOLS",
                ""
            ]

            for item in symbols_data:

                if not isinstance(item, dict):
                    continue

                symbol = (
                    item.get("underlying_symbol")
                    or item.get("symbol")
                    or ""
                )

                display = (
                    item.get("underlying_symbol_name")
                    or item.get("display_name")
                    or ""
                )

                s = str(symbol).upper()
                d = str(display).upper()

                if (
                    "BOOM" in s
                    or "CRASH" in s
                    or "BOOM" in d
                    or "CRASH" in d
                ):

                    lines.append(
                        f"SYMBOL: {symbol}\n"
                        f"NAME: {display}\n"
                    )

            if len(lines) == 2:

                lines.append(
                    "❌ Boom/Crash symbol олдсонгүй."
                )

            await update.message.reply_text(
                "\n".join(lines)
            )

    except Exception as e:

        await update.message.reply_text(
            "❌ DERIV API ERROR\n\n"
            f"{type(e).__name__}: {str(e)}"
        )


# ============================================================
# AUTO START
# ============================================================

async def auto_start(app):

    # Telegram-ийг block хийхгүй.
    # Symbol loader background-д ажиллана.

    asyncio.create_task(
        symbol_loader_loop()
    )

    # Symbol олдсоны дараа live connections эхлүүлнэ.

    async def start_connections():

        while not resolved_symbols:

            await asyncio.sleep(1)

        for k in INDICES:

            active.add(k)

            tasks[k] = asyncio.create_task(
                deriv_ws(
                    k,
                    app
                )
            )

    asyncio.create_task(
        start_connections()
    )


# ============================================================
# TELEGRAM APP
# ============================================================

app = (
    ApplicationBuilder()
    .token(TOKEN)
    .post_init(auto_start)
    .build()
)

app.add_handler(
    CommandHandler("start", start)
)

app.add_handler(
    CommandHandler("status", status)
)

app.add_handler(
    CommandHandler("symbols", symbols)
)

app.add_handler(
    CommandHandler("rawsymbols", raw_symbols)
)

app.add_handler(
    CallbackQueryHandler(button)
)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run_polling()
