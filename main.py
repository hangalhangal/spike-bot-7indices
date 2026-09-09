import os,json,time,math,asyncio,logging,threading
from http.server import BaseHTTPRequestHandler,HTTPServer
from collections import deque
import websockets
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder,CommandHandler,CallbackQueryHandler,ContextTypes

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s")
TOKEN=os.getenv("TELEGRAM_TOKEN")
if not TOKEN: raise RuntimeError("TELEGRAM_TOKEN missing")

INDICES={
"BOOM1000":{"name":"Boom 1000 Index"},
"BOOM500":{"
