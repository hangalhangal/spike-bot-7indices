import os, requests
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is Live - Telegram Test"

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg})
        print("SENT!")
    except Exception as e:
        print(e)

# Асаахдаа шууд илгээнэ
send("✅ TEST - Bot connected to Render! If you see this, token is OK")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
