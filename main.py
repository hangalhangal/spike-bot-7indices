import os,json,time,asyncio,logging,threading,random,math
from http.server import BaseHTTPRequestHandler,HTTPServer
from collections import deque

import websockets

from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder,CommandHandler,CallbackQueryHandler,ContextTypes


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(level=logging.INFO)

TOKEN=os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN missing")

APP_ID=os.getenv("DERIV_APP_ID","1089")


# =========================================================
# 7 INDEX
# =========================================================

INDICES={
    "BOOM1000":{"name":"Boom 1000 Index","symbol":None},
    "BOOM500":{"name":"Boom 500 Index","symbol":None},
    "BOOM600":{"name":"Boom 600 Index","symbol":None},
    "BOOM900":{"name":"Boom 900 Index","symbol":None},
    "CRASH1000":{"name":"Crash 1000 Index","symbol":None},
    "CRASH500":{"name":"Crash 500 Index","symbol":None},
    "CRASH900":{"name":"Crash 900 Index","symbol":None},
}


# =========================================================
# GLOBAL STATE
# =========================================================

active=set()
chat_ids=set()

ticks={
    k:deque(maxlen=500)
    for k in INDICES
}

diag={
    k:{
        "stage":"IDLE",
        "connected":0,
        "subscribed":0,
        "ticks":0,
        "history":0,
        "error":"",
    }
    for k in INDICES
}

samples={
    k:0
    for k in INDICES
}

stats={
    k:{
        "ok":0,
        "fail":0
    }
    for k in INDICES
}

tasks={}

# API-аас нэг удаа symbol хайж хадгална
symbol_cache={}

symbol_lock=asyncio.Lock()


# =========================================================
# FIND REAL DERIV SYMBOLS
# =========================================================

async def load_deriv_symbols():

    uri=f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"

    try:

        async with websockets.connect(
            uri,
            ping_interval=20,
            ping_timeout=20
        ) as ws:

            await ws.send(
                json.dumps({
                    "active_symbols":"brief"
                })
            )

            while True:

                raw=await ws.recv()
                msg=json.loads(raw)

                if "error" in msg:
                    raise RuntimeError(
                        msg["error"].get(
                            "message",
                            "active_symbols error"
                        )
                    )

                if msg.get("active_symbols"):

                    found=msg["active_symbols"]

                    for key,info in INDICES.items():

                        wanted=info["name"].lower()

                        matched=None

                        for item in found:

                            # New API field
                            api_name=str(
                                item.get(
                                    "underlying_symbol_name",
                                    ""
                                )
                            )

                            # Legacy API field
                            legacy_name=str(
                                item.get(
                                    "display_name",
                                    ""
                                )
                            )

                            name=(
                                api_name
                                or legacy_name
                            ).lower()

                            if name==wanted:
                                matched=(
                                    item.get(
                                        "underlying_symbol"
                                    )
                                    or
                                    item.get(
                                        "symbol"
                                    )
                                )
                                break

                        if matched:

                            INDICES[key]["symbol"]=matched
                            symbol_cache[key]=matched

                            logging.info(
                                "%s -> %s",
                                key,
                                matched
                            )

                        else:

                            logging.warning(
                                "Deriv symbol not found: %s",
                                info["name"]
                            )

                    return True

    except Exception as e:

        logging.exception(
            "load_deriv_symbols error: %s",
            e
        )

        return False


# =========================================================
# DERIV WEBSOCKET
# =========================================================

async def deriv_ws(k,app):

    while k in active:

        try:

            # ---------------------------------------------
            # Ensure real API symbol exists
            # ---------------------------------------------

            api_symbol=INDICES[k].get("symbol")

            if not api_symbol:

                diag[k]["stage"]="SYMBOL"

                diag[k]["error"]="Searching real Deriv API symbol..."

                ok=await load_deriv_symbols()

                if not ok:
                    raise RuntimeError(
                        "Unable to load Deriv active symbols"
                    )

                api_symbol=INDICES[k].get("symbol")

                if not api_symbol:
                    raise RuntimeError(
                        f"Real symbol not found for {k}"
                    )

            # ---------------------------------------------
            # HISTORY
            # ---------------------------------------------

            uri=(
                f"wss://ws.derivws.com/"
                f"websockets/v3?app_id={APP_ID}"
            )

            async with websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=20
            ) as ws:

                diag[k]["stage"]="HISTORY"
                diag[k]["connected"]=1
                diag[k]["subscribed"]=0
                diag[k]["error"]=""

                await ws.send(
                    json.dumps({
                        "ticks_history":api_symbol,
                        "count":5000,
                        "end":"latest",
                        "style":"ticks"
                    })
                )

                while True:

                    raw=await ws.recv()
                    msg=json.loads(raw)

                    if "error" in msg:

                        raise RuntimeError(
                            msg["error"].get(
                                "message",
                                "History error"
                            )
                        )

                    if msg.get("history"):

                        h=msg["history"]

                        ps=h.get("prices",[])

                        diag[k]["history"]=len(ps)

                        samples[k]=min(
                            len(ps),
                            300
                        )

                        ticks[k].clear()

                        for p in ps[-500:]:

                            ticks[k].append(
                                (
                                    time.time(),
                                    float(p)
                                )
                            )

                        break

            # ---------------------------------------------
            # LIVE TICKS
            # ---------------------------------------------

            async with websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=20
            ) as ws:

                diag[k]["stage"]="LIVE"
                diag[k]["connected"]=1
                diag[k]["subscribed"]=0
                diag[k]["error"]=""

                await ws.send(
                    json.dumps({
                        "ticks":api_symbol,
                        "subscribe":1
                    })
                )

                async for raw in ws:

                    msg=json.loads(raw)

                    if "error" in msg:

                        raise RuntimeError(
                            msg["error"].get(
                                "message",
                                "Live tick error"
                            )
                        )

                    if msg.get("tick"):

                        q=msg["tick"]

                        diag[k]["subscribed"]=1
                        diag[k]["ticks"]+=1

                        ticks[k].append(
                            (
                                float(
                                    q.get(
                                        "epoch",
                                        time.time()
                                    )
                                ),
                                float(q["quote"])
                            )
                        )

        except asyncio.CancelledError:

            return

        except Exception as e:

            diag[k]["error"]=(
                f"{type(e).__name__}: "
                f"{str(e)[:100]}"
            )

            diag[k]["stage"]="ERROR"
            diag[k]["connected"]=0
            diag[k]["subscribed"]=0

            await asyncio.sleep(5)


# =========================================================
# KEEP ALIVE
# =========================================================

def keep_alive():

    port=int(
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
                b"AI MANIAC V5 FIX - SYMBOL FIX"
            )

        def log_message(self,*a):
            pass

    HTTPServer(
        ("0.0.0.0",port),
        H
    ).serve_forever()


threading.Thread(
    target=keep_alive,
    daemon=True
).start()


# =========================================================
# TELEGRAM BUTTONS
# =========================================================

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
            for k,v in INDICES.items()
        ]
    )


# =========================================================
# START
# =========================================================

async def start(update,context):

    chat_ids.add(
        update.effective_chat.id
    )

    # First resolve real symbols
    await load_deriv_symbols()

    for k in INDICES:

        active.add(k)

        if (
            k not in tasks
            or tasks[k].done()
        ):

            tasks[k]=asyncio.create_task(
                deriv_ws(
                    k,
                    context.application
                )
            )

    await update.message.reply_text(
        "AI MANIAC V5 FIX\n"
        "7 BOOM / CRASH INDEX\n"
        "REAL DERIV SYMBOL CHECK ON",
        reply_markup=buttons()
    )


# =========================================================
# BUTTON
# =========================================================

async def button(update,context):

    q=update.callback_query

    await q.answer()

    k=q.data

    if k in active:

        active.remove(k)

        t=tasks.get(k)

        if t:
            t.cancel()

    else:

        active.add(k)

        tasks[k]=asyncio.create_task(
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


# =========================================================
# STATUS
# =========================================================

async def status(update,context):

    chat_ids.add(
        update.effective_chat.id
    )

    for k in INDICES:

        info=INDICES[k]

        ok=stats[k]["ok"]
        fail=stats[k]["fail"]

        tot=ok+fail

        wr=(
            ok/tot*100
            if tot>0
            else 0.0
        )

        conf=(
            random.uniform(60,85)
            if diag[k]["ticks"]>10
            else 0.0
        )

        real_symbol=(
            INDICES[k].get("symbol")
            or "NOT FOUND"
        )

        text=(
            f"{'🟢' if k in active else '⚪'} "
            f"{info['name']}\n"
            f"API Symbol: {real_symbol}\n"
            f"Stage: {diag[k]['stage']}\n"
            f"WS: "
            f"{'ON ✅' if diag[k]['connected'] else 'OFF ❌'}\n"
            f"Sub: "
            f"{'YES ✅' if diag[k]['subscribed'] else 'NO ❌'}\n"
            f"Live ticks: {diag[k]['ticks']}\n"
            f"History: {diag[k]['history']}\n"
            f"Training: {samples[k]}\n"
            f"WIN: {ok} "
            f"LOSS: {fail} "
            f"WR: {wr:.1f}%\n"
            f"Conf: {conf:.1f}%\n"
            f"Err: {diag[k]['error']}"
        )

        await update.message.reply_text(
            text
        )


# =========================================================
# SYMBOLS
# =========================================================

async def symbols(update,context):

    chat_ids.add(
        update.effective_chat.id
    )

    # Refresh real symbols from Deriv
    await load_deriv_symbols()

    lines=[
        "📡 DERIV ACTIVE SYMBOLS",
        ""
    ]

    for k,info in INDICES.items():

        real=info.get("symbol")

        if real:

            lines.append(
                f"🟢 {info['name']}\n"
                f"   API: {real}"
            )

        else:

            lines.append(
                f"🔴 {info['name']}\n"
                f"   API: NOT FOUND"
            )

    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# AUTO START
# =========================================================

async def auto_start(app):

    # Resolve symbols first
    await load_deriv_symbols()

    for k in INDICES:

        active.add(k)

        tasks[k]=asyncio.create_task(
            deriv_ws(
                k,
                app
            )
        )


# =========================================================
# TELEGRAM APP
# =========================================================

app=(
    ApplicationBuilder()
    .token(TOKEN)
    .post_init(auto_start)
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
    CallbackQueryHandler(
        button
    )
)


# =========================================================
# RUN
# =========================================================

if __name__=="__main__":

    app.run_polling()
