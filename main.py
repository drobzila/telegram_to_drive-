#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified Telegram bot:
- per-user OAuth (Drive + YouTube)
- personal Drive folder per user (inside MAIN_FOLDER_ID)
- list user's Drive files, choose a file and upload it to the user's YouTube channel
Usage:
- /auth -> gives OAuth URL (copy code and send back)
- /mydrive -> shows folder link and list files (with buttons)
- choose a file -> upload to YouTube (confirmation)
"""

import os
import json
import tempfile
import io
from pathlib import Path
from typing import Optional

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google_auth_oauthlib.flow import Flow

# ----------------------
# Config / ENV
# ----------------------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # not used for OAuth callback (we use OOB)
PORT = int(os.environ.get("PORT", "8443"))

if not TOKEN:
    raise Exception("⚠️ يجب تعيين TELEGRAM_TOKEN في Environment Variables")

# Local folders / files
DATA_DIR = Path(".")
TEMP_DIR = DATA_DIR / "temp"
TOKENS_DIR = DATA_DIR / "user_tokens"    # per-user token files: user_tokens/{user_id}.json
USER_DB = DATA_DIR / "user_folders.json"  # map user_id -> folder_id

TEMP_DIR.mkdir(exist_ok=True)
TOKENS_DIR.mkdir(exist_ok=True)
if not USER_DB.exists():
    USER_DB.write_text(json.dumps({}), encoding="utf-8")

# Google Drive main folder where personal folders will be created
MAIN_FOLDER_ID = os.environ.get("MAIN_FOLDER_ID", "root")  # set to your folder id (e.g. "1lLK...") or "root"

# Combined scopes (Drive + YouTube)
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/youtube.upload",
]

# Client secrets file (download from Google Cloud Console)
CLIENT_SECRETS = "client_secrets_youtube.json"
if not Path(CLIENT_SECRETS).exists():
    # We don't raise here, because user might add it later; but warn in logs when needed.
    pass

# ----------------------
# Utilities: user data
# ----------------------
def load_user_db():
    return json.loads(USER_DB.read_text(encoding="utf-8"))

def save_user_db(db):
    USER_DB.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")

def get_user_folder_id(user_id: int) -> Optional[str]:
    db = load_user_db()
    return db.get(str(user_id))

def set_user_folder_id(user_id: int, folder_id: str):
    db = load_user_db()
    db[str(user_id)] = folder_id
    save_user_db(db)

def token_path_for_user(user_id: int) -> Path:
    return TOKENS_DIR / f"{user_id}_token.json"

# ----------------------
# OAuth helpers (per-user)
# ----------------------
def credentials_for_user(user_id: int) -> Optional[Credentials]:
    token_path = token_path_for_user(user_id)
    if not token_path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    # refresh if needed
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            return None
    return creds

async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start OAuth flow for the user. Use OOB redirect so user copies code."""
    user = update.effective_user
    if not Path(CLIENT_SECRETS).exists():
        await update.message.reply_text("⚠️ ملف client_secrets_youtube.json غير موجود في المجلد. أضفه ثم حاول مرة أخرى.")
        return

    try:
        # Use OOB to make user copy-paste code (no redirect endpoint required)
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS,
            scopes=SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob"
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        # store flow in user_data (keeps client secrets in memory briefly)
        context.user_data["flow"] = flow
        await update.message.reply_text(
            "🔐 اضغط الرابط التالي لربط حساب Google الخاص بك (Drive + YouTube):\n\n"
            f"{auth_url}\n\n"
            "بعد تسجيل الدخول انسخ الكود الظاهر والصقه هنا في رسالة للبوت."
        )
    except Exception as e:
        await update.message.reply_text("❌ فشل بدء عملية الربط: " + str(e))

async def receive_oauth_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive the pasted OAuth code and exchange for credentials."""
    user = update.effective_user
    if "flow" not in context.user_data:
        # not in OAuth process
        return
    code = update.message.text.strip()
    flow: Flow = context.user_data["flow"]
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_path = token_path_for_user(user.id)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        context.user_data.pop("flow", None)
        # After successful auth, create user's personal folder (if not exists)
        try:
            service = build("drive", "v3", credentials=creds)
            folder_id = get_user_folder_id(user.id)
            if not folder_id:
                folder_id = create_personal_folder(service, user)
                set_user_folder_id(user.id, folder_id)
        except Exception:
            # Not fatal: user can still use /mydrive which will create folder later
            pass

        await update.message.reply_text("✅ تم ربط حساب Google بنجاح. يمكنك الآن استخدام أوامر Drive + YouTube.")
    except Exception as e:
        await update.message.reply_text("❌ رمز OAuth غير صالح أو فشل تبادل التوكن: " + str(e))
        context.user_data.pop("flow", None)

# ----------------------
# Drive helpers (per-user)
# ----------------------
def create_personal_folder(service, user) -> str:
    """Create a folder named <first>_<id> inside MAIN_FOLDER_ID and return id."""
    folder_name = f"{(user.first_name or 'user')}_{user.id}"
    body = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [MAIN_FOLDER_ID] if MAIN_FOLDER_ID and MAIN_FOLDER_ID != "root" else []
    }
    folder = service.files().create(body=body, fields="id").execute()
    return folder["id"]

def ensure_user_folder(creds: Credentials, user) -> str:
    """Return folder id; create if missing."""
    folder_id = get_user_folder_id(user.id)
    service = build("drive", "v3", credentials=creds)
    if folder_id:
        # verify exists
        try:
            service.files().get(fileId=folder_id, fields="id").execute()
            return folder_id
        except Exception:
            # fallthrough to create
            pass
    folder_id = create_personal_folder(service, user)
    set_user_folder_id(user.id, folder_id)
    return folder_id

def list_files_in_folder_with_types(service, folder_id, mime_prefix=None):
    q = f"'{folder_id}' in parents and trashed = false"
    if mime_prefix:
        q += f" and mimeType contains '{mime_prefix}'"
    res = service.files().list(q=q, fields="files(id, name, mimeType)", pageSize=500).execute()
    return res.get("files", [])

# ----------------------
# Commands / UI
# ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎉 أهلاً!\n\n"
        "• /auth - ربط حساب Google (Drive + YouTube)\n"
        "• /mydrive - عرض مجلدك الشخصي في Drive وقائمة الملفات\n"
        "• /upload_to_youtube - ابدأ رفع ملف من مجلدك إلى YouTube\n"
    )
    await update.message.reply_text(text)

async def mydrive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    creds = credentials_for_user(user.id)
    if not creds:
        await update.message.reply_text("⚠️ لم تربط حسابك بعد. استخدم /auth أولاً.")
        return
    try:
        folder_id = ensure_user_folder(creds, user)
        service = build("drive", "v3", credentials=creds)
        files = list_files_in_folder_with_types(service, folder_id)
        drive_link = f"https://drive.google.com/drive/folders/{folder_id}"
        if not files:
            text = f"📁 مجلدك الشخصي: <code>{folder_id}</code>\n\n🔗 <a href='{drive_link}'>فتح المجلد في Drive</a>\n\nلا توجد ملفات بعد."
            await update.message.reply_html(text)
            return
        # build inline keyboard with file buttons (ids)
        kb = []
        for f in files:
            kb.append([InlineKeyboardButton(f["name"], callback_data=f"selectfile:{f['id']}")])
        kb.append([InlineKeyboardButton("🔙 إغلاق", callback_data="close")])
        text = f"📁 مجلدك الشخصي: <code>{folder_id}</code>\n\n🔗 <a href='{drive_link}'>فتح المجلد في Drive</a>\n\nاختر ملفاً (اضغط الاسم):"
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)
    except Exception as e:
        await update.message.reply_text("❌ خطأ أثناء الوصول إلى Drive: " + str(e))

async def upload_to_youtube_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the flow: ask user to pick file from their Drive (by listing)."""
    user = update.effective_user
    creds = credentials_for_user(user.id)
    if not creds:
        await update.message.reply_text("⚠️ لم تربط حسابك بعد. استخدم /auth أولاً.")
        return
    try:
        folder_id = ensure_user_folder(creds, user)
        service = build("drive", "v3", credentials=creds)
        # list video mime types only
        videos = list_files_in_folder_with_types(service, folder_id, mime_prefix="video")
        if not videos:
            await update.message.reply_text("❌ لا توجد فيديوهات في مجلدك الشخصي للرفع.")
            return
        kb = [[InlineKeyboardButton(v["name"], callback_data=f"uploadvideo:{v['id']}")] for v in videos]
        kb.append([InlineKeyboardButton("🔙 إلغاء", callback_data="close")])
        await update.message.reply_text("🎬 اختر الفيديو الذي تريد رفعه إلى YouTube:", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text("❌ خطأ أثناء جلب الفيديوهات: " + str(e))

# ----------------------
# Callback handlers
# ----------------------
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data == "close":
        await q.message.delete()
        return
    if data.startswith("selectfile:"):
        file_id = data.split(":", 1)[1]
        await q.message.reply_text(f"✳ تم تحديد الملف (ID: {file_id}). استعمل /upload_to_youtube ثم اختر الملف لرفعه.")
        return
    if data.startswith("uploadvideo:"):
        file_id = data.split(":", 1)[1]
        await q.edit_message_text("⏳ جاري تجهيز الملف للرفع إلى YouTube. الرجاء الانتظار...")
        await handle_drive_to_youtube(q.from_user.id, file_id, context, reply_target=q)
        return

# ----------------------
# Core: download from Drive -> upload to YouTube
# ----------------------
async def handle_drive_to_youtube(user_id: int, drive_file_id: str, context: ContextTypes.DEFAULT_TYPE, reply_target=None):
    """Download a Drive file and upload it to the user's YouTube channel."""
    # reply_target can be either an Update.message or callback query object to send progress
    # decide how to reply
    async def send(text):
        if reply_target is None:
            # fallback to chat via user id
            await context.bot.send_message(chat_id=user_id, text=text)
        else:
            # callback query object: reply_target is CallbackQuery
            try:
                await reply_target.message.reply_text(text)
            except Exception:
                try:
                    await reply_target.edit_message_text(text)
                except Exception:
                    await context.bot.send_message(chat_id=user_id, text=text)

    creds = credentials_for_user(user_id)
    if not creds:
        await send("⚠️ لم تربط حسابك بعد. استخدم /auth أولاً.")
        return

    # build Drive service
    try:
        drive_service = build("drive", "v3", credentials=creds)
    except Exception as e:
        await send("❌ خطأ بإنشاء خدمة Drive: " + str(e))
        return

    # get file metadata
    try:
        meta = drive_service.files().get(fileId=drive_file_id, fields="id,name,mimeType,size").execute()
        file_name = meta.get("name") or f"{drive_file_id}"
    except Exception as e:
        await send("❌ تعذّر جلب معلومات الملف: " + str(e))
        return

    # download file to temp
    tmp_file = TEMP_DIR / f"{user_id}_{drive_file_id}_{file_name}"
    try:
        request = drive_service.files().get_media(fileId=drive_file_id)
        fh = io.FileIO(str(tmp_file), mode="wb")
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            # optional: could send progress
        fh.close()
    except Exception as e:
        await send("❌ فشل تنزيل الملف من Drive: " + str(e))
        try:
            tmp_file.unlink(missing_ok=True)
        except Exception:
            pass
        return

    # build YouTube service and upload
    try:
        youtube = build("youtube", "v3", credentials=creds)
    except Exception as e:
        await send("❌ فشل إنشاء خدمة YouTube: " + str(e))
        tmp_file.unlink(missing_ok=True)
        return

    await send("⬆️ جاري رفع الملف إلى YouTube... (قد يستغرق وقتًا حسب حجم الملف)")

    try:
        media = MediaFileUpload(str(tmp_file), chunksize=-1, resumable=True)
        body = {
            "snippet": {
                "title": file_name,
                "description": f"Uploaded by Telegram bot for user {user_id}",
                "tags": ["telegram", "drive", "upload"],
                "categoryId": "22"
            },
            "status": {"privacyStatus": "private"}
        }
        req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = req.next_chunk()
            if status:
                # status.progress() not always available; show approximate
                pct = int(status.progress() * 100) if hasattr(status, "progress") and status.progress() is not None else None
                if pct:
                    try:
                        await send(f"🔄 رفع {pct}% ...")
                    except Exception:
                        pass
        video_id = response.get("id")
        await send(f"✅ تم رفع الفيديو إلى YouTube بنجاح! https://youtu.be/{video_id}")
    except Exception as e:
        await send("❌ حدث خطأ أثناء رفع الفيديو إلى YouTube: " + str(e))
    finally:
        try:
            tmp_file.unlink(missing_ok=True)
        except Exception:
            pass

# ----------------------
# Message handler for files (optional: allow uploading new files to user's Drive by sending them)
# ----------------------
async def upload_file_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If user sends a file (document/photo/video), upload it into their personal Drive folder."""
    user = update.effective_user
    creds = credentials_for_user(user.id)
    if not creds:
        await update.message.reply_text("⚠️ لم تربط حسابك بعد. استخدم /auth أولاً.")
        return

    service = build("drive", "v3", credentials=creds)
    folder_id = ensure_user_folder(creds, user)

    # download the file locally
    local_path = None
    try:
        if update.message.document:
            doc = update.message.document
            fname = doc.file_name or f"file_{user.id}"
            local_path = TEMP_DIR / fname
            await doc.get_file().download_to_drive(str(local_path))
        elif update.message.photo:
            photo = update.message.photo[-1]
            fname = f"photo_{user.id}.jpg"
            local_path = TEMP_DIR / fname
            await photo.get_file().download_to_drive(str(local_path))
        elif update.message.video:
            vid = update.message.video
            fname = vid.file_name or f"video_{user.id}.mp4"
            local_path = TEMP_DIR / fname
            await vid.get_file().download_to_drive(str(local_path))
        else:
            await update.message.reply_text("❌ أرسل ملفًا (مستند/صورة/فيديو).")
            return

        # upload to Drive inside user's folder
        file_metadata = {"name": local_path.name, "parents": [folder_id]}
        media = MediaFileUpload(str(local_path))
        uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        await update.message.reply_text(f"✔ تم رفع الملف إلى مجلدك في Drive. ID: {uploaded.get('id')}")
    except Exception as e:
        await update.message.reply_text("❌ خطأ أثناء رفع الملف: " + str(e))
    finally:
        try:
            if local_path and local_path.exists():
                local_path.unlink(missing_ok=True)
        except Exception:
            pass

# ----------------------
# Main
# ----------------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("auth", auth_command))
    app.add_handler(CommandHandler("mydrive", mydrive_command))
    app.add_handler(CommandHandler("upload_to_youtube", upload_to_youtube_command))

    # callback router for inline buttons
    app.add_handler(CallbackQueryHandler(callback_router))

    # receive pasted oauth code (text messages while flow in user_data)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_oauth_code))

    # handle uploaded files from user -> upload to user's Drive
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, upload_file_message_handler))

    print("🚀 Bot is running (Webhook mode)...")
    # if you host on Render or similar and use webhooks, configure WEBHOOK_URL and port.
    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{TOKEN}"
        )
    else:
        # fallback: polling (useful for local testing)
        app.run_polling()

if __name__ == "__main__":
    main()
