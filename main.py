#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import tempfile
from pathlib import Path
import asyncio

from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

# ==========================
# Config
# ==========================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # https://your-app.onrender.com
PORT = int(os.environ.get("PORT", 10000))

MAIN_FOLDER_ID = "1lLKbFPovufWeEkwpCgI3cM-Je-Uee9el"
CLIENT_SECRET = "client_secret.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/drive.readonly"
]

CREDS_DIR = Path("creds")
TEMP_DIR = Path("temp")
CREDS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

if not TOKEN or not WEBHOOK_URL:
    raise Exception("❌ TELEGRAM_TOKEN أو WEBHOOK_URL غير مضبوط")

# ==========================
# Helpers
# ==========================
def get_creds_path(user_id):
    return CREDS_DIR / f"{user_id}.json"

def generate_oauth_url(user_id):
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET,
        scopes=SCOPES,
        redirect_uri=f"{WEBHOOK_URL}/oauth/callback"
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=str(user_id)
    )
    return auth_url

def download_drive_file(drive, file_id, name):
    fd, path = tempfile.mkstemp(suffix=Path(name).suffix, dir=TEMP_DIR)
    os.close(fd)
    request = drive.files().get_media(fileId=file_id)
    with open(path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return path

def upload_to_youtube(youtube, path, title):
    media = MediaFileUpload(path, resumable=True)
    body = {
        "snippet": {"title": title, "description": "Uploaded via Telegram Bot", "categoryId": "22"},
        "status": {"privacyStatus": "public"}
    }
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = req.next_chunk()
    return response["id"]

# ==========================
# Telegram Commands
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not get_creds_path(user_id).exists():
        url = generate_oauth_url(user_id)
        await update.message.reply_text(
            "🔐 لربط قناتك ورفع الفيديوهات إلى قناتك الخاصة، اضغط على الرابط التالي:\n\n"
            f"{url}"
        )
        return
    kb = [[InlineKeyboardButton("⬆️ رفع 3 فيديوهات", callback_data="upload3")]]
    await update.message.reply_text(
        "✅ تم ربط قناتك بنجاح",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def upload3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    creds_path = get_creds_path(user_id)
    if not creds_path.exists():
        await context.bot.send_message(chat_id, "❌ لم تقم بربط قناتك بعد.")
        return
    creds = Credentials.from_authorized_user_file(creds_path, SCOPES)
    drive = build("drive", "v3", credentials=creds)
    youtube = build("youtube", "v3", credentials=creds)
    res = drive.files().list(
        q=f"'{MAIN_FOLDER_ID}' in parents and mimeType contains 'video' and trashed=false",
        fields="files(id,name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = res.get("files", [])[:3]
    if not files:
        await context.bot.send_message(chat_id, "❌ لا توجد فيديوهات.")
        return
    for f in files:
        tmp = None
        try:
            await context.bot.send_message(chat_id, f"⬇️ {f['name']}")
            tmp = download_drive_file(drive, f["id"], f["name"])
            await context.bot.send_message(chat_id, f"⬆️ رفع إلى قناتك...")
            vid = upload_to_youtube(youtube, tmp, f["name"])
            await context.bot.send_message(chat_id, f"✅ https://youtu.be/{vid}")
        finally:
            if tmp and Path(tmp).exists():
                Path(tmp).unlink(missing_ok=True)

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "upload3":
        await upload3(update, context)

# ==========================
# OAuth callback aiohttp
# ==========================
async def oauth_callback(request):
    params = request.rel_url.query
    user_id = params.get("state")
    code = params.get("code")
    if not user_id or not code:
        return web.Response(text="❌ OAuth failed", status=400)
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET,
        scopes=SCOPES,
        redirect_uri=f"{WEBHOOK_URL}/oauth/callback"
    )
    flow.fetch_token(code=code)
    with open(get_creds_path(user_id), "w") as f:
        f.write(flow.credentials.to_json())
    return web.Response(text="✅ تم ربط قناتك بنجاح، يمكنك العودة إلى Telegram.")

# ==========================
# aiohttp server لتلقي OAuth
# ==========================
async def start_aiohttp():
    app = web.Application()
    app.router.add_get("/oauth/callback", oauth_callback)
    app.router.add_get("/", lambda request: web.Response(text="Bot Running"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 aiohttp server running on port {PORT}")

# ==========================
# Main
# ==========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("upload3", upload3))
    app.add_handler(CallbackQueryHandler(callback_router))

    # تشغيل aiohttp server كـ background task
    loop = asyncio.get_event_loop()
    loop.create_task(start_aiohttp())

    # شغل Webhook على نفس المنفذ
    print("🚀 Telegram Bot running on Render")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
