# ============================================================
# AI MANIAC V5 FIX
# DERIV SYMBOL AUTO-DETECT PATCH
# ============================================================

import asyncio
import json
import time
import websockets

DERIV_WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"

# ------------------------------------------------------------
# 7 TARGET INDICES
# ------------------------------------------------------------

TARGETS = {
    "BOOM1000": "Boom 1000 Index",
    "BOOM500": "Boom 500 Index",
    "BOOM600": "Boom 600 Index",
    "BOOM900": "Boom 900 Index",

    "CRASH1000": "Crash 1000 Index",
    "CRASH500": "Crash 500 Index",
    "CRASH900": "Crash 900 Index",
}

# API symbol-ууд энд автоматаар орно
DERIV_SYMBOLS = {}

# ------------------------------------------------------------
# DIAGNOSTIC
# ------------------------------------------------------------

for k in TARGETS:
    if k not in diag:
        diag[k] = {}

    diag[k].update({
        "connected": 0,
        "history": 0,
        "subscribed": 0,
        "last_msg_type": "",
        "last_error": "",
        "ticks": 0,
        "features": 0,
        "candidates": 0,
        "signals": 0,
        "blocked_confidence": 0,
        "blocked_cooldown": 0,
        "blocked_training": 0,
        "last_confidence": 0.0,
    })


# ============================================================
# FIND REAL DERIV SYMBOLS
# ============================================================

async def get_active_symbols(ws):

    DERIV_SYMBOLS.clear()

    req = {
        "active_symbols": "brief",
        "req_id": 9001
    }

    await ws.send(json.dumps(req))

    while True:

        raw = await ws.recv()

        try:
            msg = json.loads(raw)
        except Exception:
            continue

        if msg.get("msg_type") == "error":

            print("ACTIVE SYMBOL ERROR:", msg)

            for k in TARGETS:
                diag[k]["last_error"] = str(
                    msg.get("error", {}).get("message", "active_symbols error")
                )
                diag[k]["last_msg_type"] = "ACTIVE_SYMBOL_ERROR"

            return False

        if msg.get("msg_type") != "active_symbols":
            continue

        symbols = msg.get("active_symbols", [])

        print("\n========== DERIV ACTIVE SYMBOLS ==========")

        for item in symbols:

            # NEW API
            symbol = item.get("underlying_symbol")
            name = item.get("underlying_symbol_name")

            # fallback for legacy response
            if not symbol:
                symbol = item.get("symbol")

            if not name:
                name = item.get("display_name")

            if not symbol or not name:
                continue

            name_clean = str(name).strip().lower()

            for key, wanted_name in TARGETS.items():

                if name_clean == wanted_name.lower():

                    DERIV_SYMBOLS[key] = symbol

                    diag[key]["last_error"] = ""
                    diag[key]["last_msg_type"] = "SYMBOL_FOUND"

                    print(
                        f"FOUND: {key} -> {symbol} -> {name}"
                    )

        print("==========================================\n")

        # check all 7
        missing = []

        for key in TARGETS:
            if key not in DERIV_SYMBOLS:
                missing.append(key)
                diag[key]["last_error"] = "ACTIVE SYMBOL NOT FOUND"
                diag[key]["last_msg_type"] = "SYMBOL_LOOKUP_ERROR"

        if missing:

            print("MISSING SYMBOLS:", missing)

            return False

        print("ALL 7 SYMBOLS FOUND:")
        print(json.dumps(DERIV_SYMBOLS, indent=2))

        return True


# ============================================================
# SUBSCRIBE ONE INDEX
# ============================================================

async def subscribe_index(ws, key):

    api_symbol = DERIV_SYMBOLS.get(key)

    if not api_symbol:

        diag[key]["last_error"] = "NO API SYMBOL"
        diag[key]["last_msg_type"] = "SYMBOL_ERROR"

        return


    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    history_req = {
        "ticks_history": api_symbol,
        "end": "latest",
        "style": "ticks",
        "count": WARMUP_HISTORY,
        "subscribe": 0
    }

    await ws.send(json.dumps(history_req))

    print(
        f"[HISTORY REQUEST] {key} -> {api_symbol}"
    )


    # --------------------------------------------------------
    # LIVE TICKS
    # --------------------------------------------------------

    tick_req = {
        "ticks": api_symbol,
        "subscribe": 1
    }

    await ws.send(json.dumps(tick_req))

    diag[key]["subscribed"] = 1

    print(
        f"[SUBSCRIBED] {key} -> {api_symbol}"
    )


# ============================================================
# START WEBSOCKET
# ============================================================

async def deriv_ws():

    while True:

        try:

            print("\n====================================")
            print("CONNECTING TO DERIV...")
            print("====================================\n")

            async with websockets.connect(
                DERIV_WS_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10
            ) as ws:

                # ------------------------------------------------
                # CONNECTED
                # ------------------------------------------------

                for k in TARGETS:

                    diag[k]["connected"] = 1
                    diag[k]["last_error"] = ""
                    diag[k]["last_msg_type"] = "CONNECTED"

                print("DERIV WEBSOCKET CONNECTED")


                # ------------------------------------------------
                # GET REAL SYMBOLS
                # ------------------------------------------------

                found = await get_active_symbols(ws)

                if not found:

                    print(
                        "ERROR: Could not find all 7 target symbols."
                    )

                    await asyncio.sleep(5)
                    continue


                # ------------------------------------------------
                # SUBSCRIBE ALL 7
                # ------------------------------------------------

                for key in TARGETS:

                    try:

                        await subscribe_index(
                            ws,
                            key
                        )

                    except Exception as e:

                        diag[key]["last_error"] = str(e)
                        diag[key]["last_msg_type"] = "SUBSCRIBE_ERROR"
                        diag[key]["errors"] = (
                            diag[key].get("errors", 0) + 1
                        )

                        print(
                            f"[SUBSCRIBE ERROR] {key}: {e}"
                        )


                # ------------------------------------------------
                # RECEIVE DATA
                # ------------------------------------------------

                async for raw in ws:

                    try:
                        msg = json.loads(raw)

                    except Exception:
                        continue


                    msg_type = msg.get("msg_type")


                    # ====================================================
                    # ERROR
                    # ====================================================

                    if msg_type == "error":

                        error = msg.get("error", {})

                        message = error.get(
                            "message",
                            "Unknown Deriv error"
                        )

                        echo = msg.get("echo_req", {})

                        bad_symbol = (
                            echo.get("ticks")
                            or echo.get("ticks_history")
                            or ""
                        )

                        print(
                            "\nDERIV ERROR:",
                            message,
                            "SYMBOL:",
                            bad_symbol
                        )


                        # find matching key
                        for key, symbol in DERIV_SYMBOLS.items():

                            if symbol == bad_symbol:

                                diag[key]["errors"] = (
                                    diag[key].get("errors", 0) + 1
                                )

                                diag[key]["last_error"] = message
                                diag[key]["last_msg_type"] = "ERROR"

                        continue


                    # ====================================================
                    # HISTORY
                    # ====================================================

                    if msg_type in (
                        "history",
                        "candles"
                    ):

                        echo = msg.get(
                            "echo_req",
                            {}
                        )

                        api_symbol = (
                            echo.get("ticks_history")
                            or ""
                        )

                        key = None

                        for k, symbol in DERIV_SYMBOLS.items():

                            if symbol == api_symbol:
                                key = k
                                break

                        if key is None:
                            continue


                        history = msg.get(
                            "history",
                            {}
                        )

                        prices = history.get(
                            "prices",
                            []
                        )

                        if prices:

                            history_data[key].clear()

                            history_data[key].extend(
                                float(x)
                                for x in prices
                            )

                            diag[key]["history"] = len(
                                history_data[key]
                            )

                            diag[key]["last_msg_type"] = "HISTORY"


                            print(
                                f"[HISTORY] {key}: "
                                f"{len(prices)} ticks"
                            )

                        continue


                    # ====================================================
                    # LIVE TICK
                    # ====================================================

                    if msg_type == "tick":

                        tick = msg.get(
                            "tick",
                            {}
                        )

                        api_symbol = tick.get(
                            "symbol"
                        )

                        quote = tick.get(
                            "quote"
                        )

                        if api_symbol is None:
                            continue

                        if quote is None:
                            continue


                        # find key
                        key = None

                        for k, symbol in DERIV_SYMBOLS.items():

                            if symbol == api_symbol:
                                key = k
                                break

                        if key is None:
                            continue


                        price = float(quote)


                        # ------------------------------------------------
                        # STORE TICK
                        # ------------------------------------------------

                        history_data[key].append(
                            price
                        )

                        if len(history_data[key]) > LIVE_HISTORY:

                            del history_data[key][
                                :-LIVE_HISTORY
                            ]


                        diag[key]["ticks"] += 1
                        diag[key]["last_tick"] = time.time()
                        diag[key]["history"] = len(
                            history_data[key]
                        )
                        diag[key]["last_msg_type"] = "TICK"


                        # ------------------------------------------------
                        # EXISTING V5 AI PIPELINE
                        # ------------------------------------------------
                        #
                        # ЭНДЭЭС ДООШИХ ХЭСЭГТ
                        # ЧИНИЙ ОДООГИЙН V5 КОДНЫ
                        #
                        # features()
                        # predict()
                        # allowed()
                        # pending
                        # send_signal()
                        #
                        # ЛОГИК ХЭВЭЭР ҮЛДЭНЭ.
                        #
                        # ------------------------------------------------

                        if len(history_data[key]) < 50:
                            continue


                        try:

                            X = features(
                                history_data[key]
                            )

                            if X is None:
                                continue

                            diag[key]["features"] += 1


                            # Existing V5 prediction
                            probs = predict(
                                models[key],
                                X
                            )

                            c = int(
                                max(
                                    range(
                                        len(probs)
                                    ),
                                    key=lambda i:
                                    probs[i]
                                )
                            )

                            conf = float(
                                probs[c]
                            )


                            last_probs[key] = probs
                            last_class[key] = c

                            diag[key][
                                "last_confidence"
                            ] = conf


                            # ------------------------------------------------
                            # KEEP EXISTING V5 DECISION LOGIC
                            # ------------------------------------------------

                            if allowed(
                                key,
                                c,
                                conf
                            ):

                                diag[key][
                                    "candidates"
                                ] += 1

                                # ЭНД ЧИНИЙ ОДООГИЙН V5
                                # PENDING / SIGNAL ЛОГИК
                                # ҮРГЭЛЖИЛНЭ.

                                # ЖИШЭЭ:
                                #
                                # pending[key] = {
                                #     "class": c,
                                #     "confidence": conf,
                                #     "time": time.time()
                                # }
                                #
                                # send_signal(...)
                                #
                                # Энэ хэсгийг одоогийн V5
                                # кодноосоо хэвээр нь үлдээнэ.

                        except Exception as e:

                            diag[key]["errors"] = (
                                diag[key].get(
                                    "errors",
                                    0
                                ) + 1
                            )

                            diag[key][
                                "last_error"
                            ] = str(e)

                            diag[key][
                                "last_msg_type"
                            ] = "AI_ERROR"


        except Exception as e:

            print(
                "\nWEBSOCKET CONNECTION ERROR:",
                e
            )

            for key in TARGETS:

                diag[key]["connected"] = 0
                diag[key]["subscribed"] = 0
                diag[key]["last_msg_type"] = "DISCONNECTED"
                diag[key]["last_error"] = str(e)

            await asyncio.sleep(5)
