#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import random
import tempfile
import io
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow

# ==========================
# إعدادات (عدل المتغيرات البيئية إن لزم)
# ==========================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # مثال: https://yourdomain.com
PORT = int(os.environ.get("PORT", "8443"))

if not TOKEN or not WEBHOOK_URL:
    raise Exception("⚠️ يجب تعيين TELEGRAM_TOKEN وWEBHOOK_URL في Environment Variables")

# مجلدات محلية
OUTPUTS_DIR = Path("outputs")
TEMP_DIR = Path("temp")
YOUTUBE_TOKENS_DIR = Path("youtube_tokens")
USER_DB = Path("user_folders.json")
OUTPUTS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)
YOUTUBE_TOKENS_DIR.mkdir(exist_ok=True)

# المجلد الرئيسي على Google Drive الذي يحتوي فيديوهات القرآن
MAIN_FOLDER_ID = "1lLKbFPovufWeEkwpCgI3cM-Je-Uee9el"

# ردود وتحسينات الوصف
RESPONSES = ["السلام عليكم يا {name} 🌸", "أهلًا وسهلًا يا {name} 👋"]
ABOUT_DESCRIPTION = (
    "أفضل صانع وناشر للقرآن الكريم — جودة عالية، سهولة، سرعة، أدوات احترافية لنشر محتوى القرآن بكل أمان واحتراف."
)
SUPPORT_CHANNEL_URL = "https://www.youtube.com/channel/UCHYJMygtSl60pThu6AUgeOw"

# Scopes
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "openid", "https://www.googleapis.com/auth/userinfo.email"]

# ==========================
# دوال Google Drive العامة
# ==========================
def get_drive_service():
    if not os.path.exists("token.json"):
        raise Exception("⚠️ ملف token.json غير موجود!")
    creds = Credentials.from_authorized_user_file("token.json", DRIVE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("drive", "v3", credentials=creds)

def load_user_db():
    if USER_DB.exists():
        return json.loads(USER_DB.read_text(encoding="utf-8"))
    return {}

def save_user_db(db):
    USER_DB.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")

def get_or_create_user_folder(service, user):
    db = load_user_db()
    uid = str(user.id)
    folder_name = f"{(user.first_name or 'User')}_{user.id}"
    if uid in db:
        return db[uid]
    folder_metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": ["root"]}
    folder = service.files().create(body=folder_metadata, fields="id").execute()
    folder_id = folder["id"]
    db[uid] = folder_id
    save_user_db(db)
    return folder_id

def upload_file_to_user_folder(service, user, local_path):
    folder_id = get_or_create_user_folder(service, user)
    file_metadata = {"name": Path(local_path).name, "parents": [folder_id]}
    media = MediaFileUpload(str(local_path), resumable=True)
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return uploaded["id"]

def list_drive_videos(service, folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'video/' and trashed = false"
    results = service.files().list(q=query, fields="nextPageToken, files(id, name, appProperties)").execute()
    return results.get("files", [])

def list_files_in_folder(service, folder_id):
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name, mimeType, appProperties)").execute()
    return results.get("files", [])

# ==========================
# دوال YouTube
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

def download_drive_file_to_temp(service_drive, file_id, filename):
    suffix = Path(filename).suffix or ".mp4"
    fd, temp_path = tempfile.mkstemp(prefix="dl_", suffix=suffix, dir=str(TEMP_DIR))
    os.close(fd)
    request = service_drive.files().get_media(fileId=file_id)
    fh = io.FileIO(temp_path, mode="wb")
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    try:
        while not done:
            status, done = downloader.next_chunk()
    finally:
        fh.close()
    return temp_path

def upload_single_file_to_youtube(creds, local_path, title, description, privacy="private"):
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {"title": title, "description": description, "tags": ["quran", "قرآن", "recitation"], "categoryId": "22"},
        "status": {"privacyStatus": privacy}
    }
    media = MediaFileUpload(local_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
    return response.get("id")

# ==========================
# نصف تلقائي
# ==========================
def sync_user_folder(service, user, main_folder_id):
    user_folder_id = get_or_create_user_folder(service, user)
    main_videos = list_drive_videos(service, main_folder_id)
    user_videos = list_drive_videos(service, user_folder_id)
    user_names = {f["name"] for f in user_videos}
    to_copy = [v for v in main_videos if v["name"] not in user_names]
    copied = []
    for v in to_copy:
        try:
            copy_body = {"parents": [user_folder_id], "name": v["name"]}
            new_file = service.files().copy(fileId=v["id"], body=copy_body, fields="id, name").execute()
            copied.append({"id": new_file["id"], "name": new_file["name"]})
        except Exception as e:
            copied.append({"id": None, "name": v["name"], "error": str(e)})
    return copied

# ==========================
# Telegram Handlers
# ==========================
def main_menu_keyboard():
    kb = [
        [InlineKeyboardButton("📂 إدارة الملفات", callback_data="ui:myfiles")],
        [InlineKeyboardButton("📤 رفع ملف إلى Drive", callback_data="ui:upload")],
        [InlineKeyboardButton("🎬 نسخ فيديو إلى ملفاتي", callback_data="ui:choosevideo")],
        [InlineKeyboardButton("🔄 تحديث مجلدي", callback_data="ui:sync")],
        [InlineKeyboardButton("ℹ️ حول البوت", callback_data="ui:about"),
         InlineKeyboardButton("🛠 الدعم الفني", callback_data="ui:support")]
    ]
    return InlineKeyboardMarkup(kb)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "ضيف"
    welcome_text = (
        f"🎉 مرحبًا بك يا <b>{name}</b>!\n\n"
        f"🚀 <b>CloudDrive Bot</b>\n{ABOUT_DESCRIPTION}\n\n"
        "👇 استخدم الأزرار التالية للبدء:"
    )
    await update.message.reply_html(welcome_text, reply_markup=main_menu_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - العودة للقائمة\n"
        "/listvideos - عرض فيديوهات المجلد الرئيسي\n"
        "/sync - نسخ الفيديوهات الجديدة إلى مجلدك\n"
        "/auth_youtube - ربط حساب YouTube\n"
        "/upload_to_youtube - رفع فيديو إلى قناتك"
    )

async def greet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(random.choice(RESPONSES).format(name=user.first_name or "ضيف"))

# ==========================
# OAuth YouTube
# ==========================
async def auth_youtube(update, context):
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            "client_secrets_youtube.json",
            scopes=YOUTUBE_SCOPES
        )
        auth_url, _ = flow.authorization_url(prompt="consent")
        await update.message.reply_text(
            "🔗 افتح الرابط التالي لتسجيل الدخول إلى YouTube:\n\n" + auth_url +
            "\n\nبعد تسجيل الدخول سيظهر لك الكود، أرسله هنا."
        )
        context.user_data["awaiting_youtube_code"] = flow
    except Exception as e:
        await update.message.reply_text("❌ خطأ OAuth: " + str(e))

async def receive_oauth_code(update, context):
    if "awaiting_youtube_code" not in context.user_data:
        return
    flow = context.user_data["awaiting_youtube_code"]
    code = update.message.text.strip()
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        context.user_data["youtube_credentials"] = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes
        }
        # حفظ كملف
        token_path = YOUTUBE_TOKENS_DIR / f"{update.effective_user.id}_token.json"
        token_path.write_text(json.dumps(context.user_data["youtube_credentials"]), encoding="utf-8")
        del context.user_data["awaiting_youtube_code"]
        await update.message.reply_text("✅ تم ربط حساب YouTube بنجاح!")
    except Exception as e:
        await update.message.reply_text("❌ رمز OAuth غير صحيح:\n" + str(e))

# ==========================
# رفع فيديو محدد
# ==========================
async def upload_to_youtube(update, context):
    user = update.effective_user
    if "youtube_credentials" not in context.user_data:
        await update.message.reply_text("❌ لم تربط حساب YouTube بعد. استخدم /auth_youtube أولاً.")
        return
    creds_data = context.user_data["youtube_credentials"]
    creds = Credentials(**creds_data)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = get_drive_service()
    user_folder_id = get_or_create_user_folder(service, user)
    files = list_drive_videos(service, user_folder_id)

    if not files:
        await update.message.reply_text("❌ لا توجد فيديوهات في مجلدك.")
        return

    # رفع حتى 5 فيديوهات
    videos_uploaded = 0
    for f in files:
        if f.get("appProperties", {}).get("uploaded_to_youtube") == "true":
            continue
        if videos_uploaded >= 5:
            break
        temp_path = download_drive_file_to_temp(service, f["id"], f["name"])
        description = (
            "أفضل صانع وناشر للقرآن الكريم — جودة عالية، سهولة، سرعة. Qurani Studio.\n"
            f"🔗 دعم فني: {SUPPORT_CHANNEL_URL}"
        )
        yt_id = upload_single_file_to_youtube(creds, temp_path, title=f["name"], description=description)
        service.files().update(fileId=f["id"], body={"appProperties": {"uploaded_to_youtube": "true"}}).execute()
        os.remove(temp_path)
        videos_uploaded += 1

    await update.message.reply_text(f"✅ تم رفع {videos_uploaded} فيديو بنجاح إلى قناتك!")

# ==========================
# Main
# ==========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("listvideos", list_videos_command))
    app.add_handler(CommandHandler("auth_youtube", auth_youtube))
    app.add_handler(CommandHandler("upload_to_youtube", upload_to_youtube))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_oauth_code))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(upload_callback_handler, pattern="^upload:"))
    
    print("🚀 Bot is running with Webhook...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
