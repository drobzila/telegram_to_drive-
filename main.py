#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import tempfile
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
)

# ==========================
# Config
# ==========================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 8443))

MAIN_FOLDER_ID = "1lLKbFPovufWeEkwpCgI3cM-Je-Uee9el"       # مجلد Drive الرئيسي للفيديوهات
BOT_CRED_JSON = "bot_credentials.json"        # OAuth للبوت نفسه
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

if not TOKEN or not WEBHOOK_URL:
    raise Exception("⚠️ تأكد من ضبط TELEGRAM_TOKEN وWEBHOOK_URL في Environment Variables")

# ==========================
# Helpers
# ==========================
def download_drive_file(service_drive, file_id, filename):
    suffix = Path(filename).suffix or ".mp4"
    fd, temp_path = tempfile.mkstemp(prefix="dl_", suffix=suffix, dir=str(TEMP_DIR))
    os.close(fd)
    request = service_drive.files().get_media(fileId=file_id)
    with open(temp_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return temp_path

def upload_file_to_youtube(youtube_service, local_path, title, description="Uploaded via Bot"):
    media = MediaFileUpload(local_path, chunksize=-1, resumable=True)
    body = {
        "snippet": {"title": title, "description": description, "categoryId": "22"},
        "status": {"privacyStatus": "private"}
    }
    req = youtube_service.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = req.next_chunk()
    return response.get("id")

# ==========================
# Drive-stored uploaded DB
# ==========================
# سيتم إنشاء ملف JSON داخل Drive لتتبع الفيديوهات المرفوعة
UPLOADED_DB_NAME = "uploaded_videos.json"

def get_drive_uploaded_db(drive_service):
    # تحقق من وجود الملف على Drive
    res = drive_service.files().list(q=f"name='{UPLOADED_DB_NAME}' and trashed=false",
                                     fields="files(id, name)").execute()
    files = res.get("files", [])
    if files:
        file_id = files[0]["id"]
        # تنزيل الملف مؤقتًا
        tmp_path = download_drive_file(drive_service, file_id, UPLOADED_DB_NAME)
        with open(tmp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        os.remove(tmp_path)
        return files[0]["id"], data
    else:
        # إنشاء قاعدة جديدة إذا لم توجد
        data = {"uploaded_ids": []}
        return None, data

def save_drive_uploaded_db(drive_service, db_id, data):
    tmp_path = TEMP_DIR / UPLOADED_DB_NAME
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    media = MediaFileUpload(str(tmp_path), resumable=True)
    if db_id:
        drive_service.files().update(fileId=db_id, media_body=media).execute()
    else:
        drive_service.files().create(body={"name": UPLOADED_DB_NAME}, media_body=media).execute()
    tmp_path.unlink(missing_ok=True)

# ==========================
# Command: Upload 3 videos
# ==========================
async def upload3_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id, "⏳ جاري التحقق من الفيديوهات...")

    # Credentials للبوت نفسه
    creds = Credentials.from_authorized_user_file(BOT_CRED_JSON, ["https://www.googleapis.com/auth/drive","https://www.googleapis.com/auth/youtube.upload"])
    drive_service = build("drive", "v3", credentials=creds)
    youtube_service = build("youtube", "v3", credentials=creds)

    # جلب الفيديوهات من المجلد الرئيسي
    res = drive_service.files().list(
        q=f"'{MAIN_FOLDER_ID}' in parents and mimeType contains 'video/mp4' and trashed=false",
        fields="files(id, name)"
    ).execute()
    files = res.get("files", [])

    # تحميل قاعدة الفيديوهات المرفوعة من Drive
    db_id, uploaded_db = get_drive_uploaded_db(drive_service)
    uploaded_ids = uploaded_db.get("uploaded_ids", [])

    to_upload = [f for f in files if f["id"] not in uploaded_ids][:3]
    if not to_upload:
        await context.bot.send_message(chat_id, "❌ لا توجد فيديوهات جديدة للرفع.")
        return

    for f in to_upload:
        await context.bot.send_message(chat_id, f"⬇️ جاري تنزيل الفيديو: {f['name']}")
        try:
            tmp_file = download_drive_file(drive_service, f["id"], f["name"])
            await context.bot.send_message(chat_id, f"⬆️ جاري رفع الفيديو إلى YouTube: {f['name']}")
            video_id = upload_file_to_youtube(youtube_service, tmp_file, f["name"])
            await context.bot.send_message(chat_id, f"✅ تم رفع الفيديو: https://youtu.be/{video_id}")
            uploaded_ids.append(f["id"])
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ حدث خطأ مع الفيديو {f['name']}: {e}")
        finally:
            if tmp_file and Path(tmp_file).exists():
                Path(tmp_file).unlink(missing_ok=True)

    # تحديث قاعدة الفيديوهات على Drive
    uploaded_db["uploaded_ids"] = uploaded_ids
    save_drive_uploaded_db(drive_service, db_id, uploaded_db)

# ==========================
# Start Command
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("⬆️ رفع 3 فيديوهات", callback_data="upload3")]]
    await update.message.reply_text("مرحبا! استخدم الزر أدناه لرفع 3 فيديوهات جديدة:", reply_markup=InlineKeyboardMarkup(kb))

# ==========================
# Callback Handler
# ==========================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "upload3":
        await upload3_command(update, context)

# ==========================
# Main
# ==========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("upload3", upload3_command))
    app.add_handler(CallbackQueryHandler(callback_router))

    print("🚀 Bot is running (Webhook mode)...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
