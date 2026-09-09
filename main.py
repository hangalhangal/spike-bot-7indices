async def status(update,context):
    chat_ids.add(update.effective_chat.id)
    # Header
    await update.message.reply_text(
        "🧠🔥 AI MANIAC V5 FIX STATUS\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Active: {len(active)}/7\n"
        "🧠 Online learning: ON\n"
        "💾 Persistent memory: ON\n"
        "🎯 Confidence filter: 40%\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    # Send each index separately
    for k,info in INDICES.items():
        ok=stats[k]["ok"]
        fail=stats[k]["fail"]
        total=ok+fail
        wr=(ok/total*100) if total else 0
        p=last_probs.get(k,[1/3,1/3,1/3])
        cls=last_class.get(k,0)
        class_names=["NO SPIKE","UP SPIKE","DOWN SPIKE"]
        d=diag[k]
        msg=(
            f"{'🟢' if k in active else '⚪'} {info['name']}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📡 WebSocket: "
            f"{'CONNECTED ✅' if d.get('connected',0) else 'OFF ❌'}\n"
            f"📚 History: {d.get('history',0)}\n"
            f"📥 Subscribed: "
            f"{'YES ✅' if d.get('subscribed',0) else 'NO ❌'}\n"
            f"🔄 Last message: {d.get('last_msg_type','-')}\n"
            f"📊 Ticks stored: "
            f"{len(ticks_data[k])}/{LIVE_HISTORY}\n"
            f"⚡ Live ticks: {d.get('ticks',0)}\n"
            f"🧩 Features: {d.get('features',0)}\n"
            f"🧠 AI Training: {models[k].get('samples',0)}\n"
            f"🏆 WIN: {ok} | LOSS: {fail}\n"
            f"📈 WinRate: {wr:.1f}%\n"
            f"⏳ Pending: {len(pending[k])}\n"
            f"🎯 Candidates: {d.get('candidates',0)}\n"
            f"🔮 Last prediction: "
            f"{class_names[cls] if 0 <= cls < 3 else 'UNKNOWN'}\n"
            f"🧠 Confidence: "
            f"{d.get('last_confidence',0)*100:.1f}%\n"
            f"📈 UP: {p[1]*100:.1f}%\n"
            f"📉 DOWN: {p[2]*100:.1f}%\n"
            f"⚪ NO SPIKE: {p[0]*100:.1f}%\n"
            f"🚨 Signals: {d.get('signals',0)}\n"
            f"❌ Errors: {d.get('errors',0)}\n"
            f"⛔ Blocked confidence: "
            f"{d.get('blocked_confidence',0)}\n"
            f"⏱️ Blocked cooldown: "
            f"{d.get('blocked_cooldown',0)}\n"
            f"🧠 Blocked training: "
            f"{d.get('blocked_training',0)}\n"
            f"⚠️ Last error: "
            f"{d.get('last_error') or '-'}\n"
            "━━━━━━━━━━━━━━━━━━"
        )
        try:
            await update.message.reply_text(msg)
        except Exception as e:
            logging.error(
                "STATUS SEND ERROR %s: %s",
                k,
                e
            )
    # Final summary
    await update.message.reply_text(
        "🧠🔥 AI MANIAC V5 FIX\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Active: {len(active)}/7\n"
        "📡 Live tick engine: ON\n"
        "🧠 Balanced warm-up: ON\n"
        "🔄 Online learning: ON\n"
        "💾 Persistent memory: ON\n"
        "🎯 Confidence filter: 40%\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "✅ /status COMPLETE"
    )
