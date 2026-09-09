# V5.3 FIX - LIVE TICK FIX (History + Ticks салгасан)
async def deriv_ws(k,app):
    uri=f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        # 1. HISTORY (5000) - ticks_history
        async with websockets.connect(uri,ping_interval=20,ping_timeout=20) as ws:
            symbol = KNOWN_DERIV_SYMBOLS[k]
            await ws.send(json.dumps({
                "ticks_history":symbol,
                "adjust_start_time":1,
                "count":WARMUP_HISTORY,
                "end":"latest",
                "style":"ticks"
            }))
            async for raw in ws:
                msg=json.loads(raw)
                if "error" in msg: raise RuntimeError(msg["error"]["message"])
                if msg.get("history"):
                    h=msg["history"]; ps=h.get("prices",[]); ts=h.get("times",[])
                    history_data[k]=[(float(ts[i]),float(x)) for i,x in enumerate(ps) if i<len(ts)]
                    ticks_data[k].clear()
                    for item in history_data[k][-LIVE_HISTORY:]: ticks_data[k].append(item)
                    await asyncio.to_thread(warmup,k)
                    break

        # 2. LIVE - ЗӨВХӨН ticks ашиглана (ЭНЭ НЬ ГОЛ ЗАСВАР)
        async with websockets.connect(uri,ping_interval=20,ping_timeout=20) as ws:
            symbol = KNOWN_DERIV_SYMBOLS[k]
            # LIVE-д ticks_history биш, ticks ашиглана
            await ws.send(json.dumps({
                "ticks": symbol,
                "subscribe": 1
            }))
            diag[k]["stage"]="LIVE"
            diag[k]["connected"]=1
            async for raw in ws:
                msg=json.loads(raw)
                if "error" in msg:
                    # Хэрэв BOOM600/900 invalid байвал алгас
                    if "invalid" in msg["error"]["message"].lower():
                        logging.warning(f"{k} {symbol} invalid for live, trying history subscribe")
                        # Fallback: ticks_history subscribe
                        await ws.send(json.dumps({
                            "ticks_history":symbol,
                            "style":"ticks",
                            "subscribe":1
                        }))
                        continue
                    raise RuntimeError(msg["error"]["message"])
                tick=msg.get("tick")
                if tick:
                    price=float(tick["quote"]); ts=float(tick.get("epoch",time.time()))
                    diag[k]["subscribed"]=1; diag[k]["ticks"]+=1; diag[k]["last_tick"]=ts
                    ticks_data[k].append((ts,price))
                    ps=[x[1] for x in ticks_data[k]]
                    if len(ps)<210: continue
                    await evaluate(k,ts)
                    f=features(ps)
                    if not f: continue
                    c,conf,probs=predict(k,f)
                    last_probs[k]=probs
                    if not allowed(k,c,conf): continue
                    direction="BUY" if c==1 else "SELL"
                    pending[k].append({"time":ts,"price":price,"direction":direction,"features":f,"confidence":conf})
                    last_signal[k]=time.time()
                    await send_signal(app,k,direction,conf,probs)
    except Exception as e:
        diag[k]["last_error"]=str(e); raise
