# V5.5 FIX - Input validation failed: end - ЗАСВАР
async def deriv_ws(k,app):
    diag[k]["stage"]="CONNECTING"
    uri=f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        # 1. HISTORY - end-гүй
        async with websockets.connect(uri,ping_interval=20,ping_timeout=20,close_timeout=10) as ws:
            diag[k]["connected"]=1
            diag[k]["stage"]="HISTORY"
            symbol = KNOWN_DERIV_SYMBOLS[k]
            # FIX: end болон adjust_start_time хасав
            await ws.send(json.dumps({
                "ticks_history":symbol,
                "count":WARMUP_HISTORY,
                "style":"ticks"
            }))
            while k in active:
                raw=await asyncio.wait_for(ws.recv(), timeout=20)
                msg=json.loads(raw)
                if msg.get("msg_type")=="error" or "error" in msg:
                    raise RuntimeError(msg.get("error",{}).get("message","history error"))
                if msg.get("msg_type")=="history" and msg.get("history") is not None:
                    h=msg["history"]
                    ps=h.get("prices",[]); ts=h.get("times",[])
                    history_data[k]=[(float(ts[i]),float(x)) for i,x in enumerate(ps) if i<len(ts)]
                    diag[k]["history"]=len(history_data[k])
                    ticks_data[k].clear()
                    for item in history_data[k][-LIVE_HISTORY:]:
                        ticks_data[k].append(item)
                    await asyncio.to_thread(warmup,k)
                    break

        # 2. LIVE - зөвхөн ticks
        async with websockets.connect(uri,ping_interval=20,ping_timeout=20,close_timeout=10) as ws:
            diag[k]["connected"]=1
            diag[k]["stage"]="LIVE"
            symbol = KNOWN_DERIV_SYMBOLS[k]
            # FIX: LIVE-д зөвхөн ticks, end байхгүй
            await ws.send(json.dumps({
                "ticks": symbol,
                "subscribe": 1
            }))
            logging.info(f"{k} LIVE SUBSCRIBED: {symbol}")
            while k in active:
                raw=await ws.recv()
                msg=json.loads(raw)
                if "error" in msg:
                    err_msg = msg["error"].get("message","")
                    if "invalid" in err_msg.lower():
                        # BOOM600/900 заримдаа invalid - тэгвэл history subscribe
                        await ws.send(json.dumps({
                            "ticks_history": symbol,
                            "style": "ticks",
                            "subscribe": 1
                        }))
                        continue
                    raise RuntimeError(err_msg)
                tick_data = msg.get("tick")
                if tick_data:
                    price=float(tick_data["quote"])
                    timestamp=float(tick_data.get("epoch",time.time()))
                    diag[k]["subscribed"]=1
                    diag[k]["stage"]="LIVE"
                    diag[k]["ticks"]+=1
                    diag[k]["last_tick"]=timestamp
                    diag[k]["last_msg_type"]="LIVE_TICK"
                    ticks_data[k].append((timestamp,price))
                    ps=[x[1] for x in ticks_data[k]]
                    if len(ps)<210: continue
                    await evaluate(k,timestamp)
                    try: f=features(ps)
                    except: continue
                    if f is None: continue
                    diag[k]["features"]+=1
                    c,conf,probs=predict(k,f)
                    last_probs[k]=probs; last_class[k]=c
                    diag[k]["last_confidence"]=conf
                    if c!=0: diag[k]["candidates"]+=1
                    if models[k]["samples"]<MIN_TRAINING_SAMPLES: continue
                    if c==0: continue
                    if conf<SIGNAL_CONFIDENCE: diag[k]["blocked_confidence"]+=1; continue
                    if time.time()-last_signal[k]<COOLDOWN_SECONDS: diag[k]["blocked_cooldown"]+=1; continue
                    direction="BUY" if c==1 else "SELL"
                    pending[k].append({"time":timestamp,"price":price,"direction":direction,"features":f,"confidence":conf})
                    last_signal[k]=time.time()
                    diag[k]["signals"]+=1
                    await send_signal(app,k,direction,conf,probs)
    except asyncio.CancelledError:
        diag[k]["stage"]="STOPPED"; raise
    except Exception as e:
        diag[k]["connected"]=0; diag[k]["subscribed"]=0; diag[k]["errors"]+=1
        diag[k]["last_error"]=f"{type(e).__name__}: {e}"; diag[k]["stage"]="ERROR"
        raise
    finally:
        diag[k]["connected"]=0; diag[k]["subscribed"]=0
