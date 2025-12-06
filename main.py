#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
from pathlib import Path
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import Flow

# ==========================
# إعدادات البيئة
# ==========================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # https://yourdomain.com
PORT = int(os.environ.get("PORT", "8443"))

if not TOKEN or not WEBHOOK_URL:
    raise Exception("⚠️ يجب تعيين TELEGRAM_TOKEN وWEBHOOK_URL في Environment Variables")

TEMP_DIR = Path("temp")
YOUTUBE_TOKENS_DIR = Path("youtube_tokens")
USER_DATA_FILE = Path("user_data.json")

TEMP_DIR.mkdir(exist_ok=True)
YOUTUBE_TOKENS_DIR.mkdir(exist_ok=True)
if not USER_DATA_FILE.exists():
    USER_DATA_FILE.write_text(json.dumps({}), encoding="utf-8")

# ==========================
# Scopes
# ==========================
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# ==========================
# إدارة بيانات المستخدم
# ==========================
def load_user_data():
    return json.loads(USER_DATA_FILE.read_text(encoding="utf-8"))

def save_user_data(data):
    USER_DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

def increment_videos_uploaded(user_id: int):
    data = load_user_data()
    user_str = str(user_id)
    data[user_str] = data.get(user_str, 0) + 1
    save_user_data(data)
    return data[user_str]

def get_uploaded_count(user_id: int):
    data = load_user_data()
    return data.get(str(user_id), 0)

# ==========================
# دوال YouTube OAuth
# ==========================
def get_youtube_credentials_for_user(user_id: int):
    token_path = YOUTUBE_TOKENS_DIR / f"{user_id}_token.json"
    if not token_path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds

async def auth_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        flow = Flow.from_client_secrets_file(
            "client_secrets_youtube.json",
            scopes=YOUTUBE_SCOPES,
            redirect_uri=f"{WEBHOOK_URL.rstrip('/')}/{TOKEN}/oauth2callback"
        )
        auth_url, _ = flow.authorization_url(prompt="consent")
        context.user_data["flow"] = flow
        await update.message.reply_text(
            f"🔗 افتح الرابط التالي على جهازك وسجل الدخول إلى YouTube:\n\n{auth_url}\n\n"
            "بعد تسجيل الدخول انسخ الكود وأرسله هنا."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ OAuth: {e}")

async def receive_oauth_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "flow" not in context.user_data:
        return
    code = update.message.text.strip()
    flow: Flow = context.user_data["flow"]
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_path = YOUTUBE_TOKENS_DIR / f"{update.effective_user.id}_token.json"
        token_path.write_text(creds.to_json(), encoding="utf-8")
        context.user_data.pop("flow", None)
        await update.message.reply_text("✅ تم ربط حساب YouTube بنجاح! يمكنك الآن رفع الفيديوهات.")
    except Exception as e:
        await update.message.reply_text(f"❌ رمز OAuth غير صحيح:\n{e}")

# ==========================
# رفع فيديو إلى YouTube
# ==========================
async def upload_to_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    creds = get_youtube_credentials_for_user(user.id)
    if not creds:
        await update.message.reply_text("⚠️ لم تقم بربط حساب YouTube بعد. استخدم /auth_youtube أولاً.")
        return

    uploaded_count = get_uploaded_count(user.id)
    if uploaded_count >= 5:
        await update.message.reply_text("🚫 لقد وصلت إلى الحد الأقصى للرفع (5 فيديوهات).")
        return

    context.user_data["awaiting_upload"] = True
    await update.message.reply_text("📤 أرسل الآن الفيديو الذي تريد رفعه على قناتك (Document أو Video).")

async def upload_file_by_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.user_data.get("awaiting_upload"):
        return

    creds = get_youtube_credentials_for_user(user.id)
    if not creds:
        await update.message.reply_text("⚠️ لم تقم بربط حساب YouTube بعد. استخدم /auth_youtube أولاً.")
        return

    file_name = None
    local_path = None

    if update.message.document:
        doc = update.message.document
        file_name = doc.file_name or f"video_{user.id}.mp4"
        local_path = TEMP_DIR / file_name
        await doc.get_file().download_to_drive(str(local_path))
    elif update.message.video:
        vid = update.message.video
        file_name = vid.file_name or f"video_{user.id}.mp4"
        local_path = TEMP_DIR / file_name
        await vid.get_file().download_to_drive(str(local_path))
    else:
        await update.message.reply_text("❌ لم أجد ملف فيديو. أرسل ملف فيديو (Document أو Video).")
        return

    if local_path.stat().st_size == 0:
        await update.message.reply_text("❌ الملف فارغ!")
        local_path.unlink(missing_ok=True)
        return

    try:
        youtube = build("youtube", "v3", credentials=creds)
        media = MediaFileUpload(str(local_path), chunksize=-1, resumable=True)
        body = {
            "snippet": {
                "title": file_name,
                "description": "رفع بواسطة Telegram Bot",
                "tags": ["Quran", "Islam", "Recitation"],
                "categoryId": "22"
            },
            "status": {"privacyStatus": "private"}
        }
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()

        video_id = response["id"]
        increment_videos_uploaded(user.id)
        await update.message.reply_text(f"✅ تم رفع الفيديو بنجاح! الرابط: https://youtu.be/{video_id}")

    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ أثناء الرفع: {e}")
    finally:
        local_path.unlink(missing_ok=True)
        context.user_data.pop("awaiting_upload", None)

# ==========================
# أوامر البوت الأساسية
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 أهلاً بك!\n"
        "• /auth_youtube - ربط حسابك على YouTube\n"
        "• /upload_to_youtube - رفع فيديو إلى قناتك (حد أقصى 5 فيديوهات)\n"
    )

# ==========================
# تشغيل البوت (Webhook)
# ==========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("auth_youtube", auth_youtube))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_oauth_code))
    app.add_handler(CommandHandler("upload_to_youtube", upload_to_youtube))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, upload_file_by_message))

    print("🚀 Bot is running with Webhook...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
