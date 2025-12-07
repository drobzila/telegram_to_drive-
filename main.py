#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified Telegram Bot:
- per-user OAuth (Drive + YouTube)
- personal Drive folder per user (inside MAIN_FOLDER_ID)
- list user's Drive files, choose a file and upload it to YouTube
- main folder with Quran videos that can be synced to user folders
- upload files from Telegram messages to user's Drive
"""

import os
import json
import random
import tempfile
import io
from pathlib import Path
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google_auth_oauthlib.flow import Flow, InstalledAppFlow

# ==========================
# Config / ENV
# ==========================
TOKEN = os.environ.get("TELEGRAM_TOKEN")

WEBHOOK_URL = os.environ.get(
    "WEBHOOK_URL",
    "https://telegram-to-drive.onrender.com"
)

PORT = int(os.environ.get("PORT", "8443"))

# المجلد الرئيسي للفيديوهات في Google Drive
MAIN_FOLDER_ID = "1lLKbFPovufWeEkwpCgI3cM-Je-Uee9el"

CLIENT_SECRETS = "client_secrets_youtube.json"

if not TOKEN:
    raise Exception("⚠️ TELEGRAM_TOKEN غير مضبوط في Environment Variables")

# Local directories
# Local directories
DATA_DIR = Path(".")
TEMP_DIR = DATA_DIR / "temp"
TOKENS_DIR = DATA_DIR / "user_tokens"
USER_DB = DATA_DIR / "user_folders.json"
TEMP_DIR.mkdir(exist_ok=True)
TOKENS_DIR.mkdir(exist_ok=True)
if not USER_DB.exists():
    USER_DB.write_text(json.dumps({}), encoding="utf-8")

# OAuth scopes
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/youtube.upload",
]

# Misc
RESPONSES = ["السلام عليكم يا {name} 🌸", "أهلًا وسهلًا يا {name} 👋"]
ABOUT_DESCRIPTION = "أفضل صانع وناشر للقرآن الكريم — جودة عالية، سهولة، سرعة."
SUPPORT_CHANNEL_URL = "https://www.youtube.com/channel/UCHYJMygtSl60pThu6AUgeOw"

# ==========================
# User DB helpers
# ==========================
def load_user_db():
    return json.loads(USER_DB.read_text(encoding="utf-8"))

def save_user_db(db):
    USER_DB.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")

def get_user_folder_id(user_id: int) -> Optional[str]:
    return load_user_db().get(str(user_id))

def set_user_folder_id(user_id: int, folder_id: str):
    db = load_user_db()
    db[str(user_id)] = folder_id
    save_user_db(db)

def token_path_for_user(user_id: int) -> Path:
    return TOKENS_DIR / f"{user_id}_token.json"

def credentials_for_user(user_id: int) -> Optional[Credentials]:
    token_path = token_path_for_user(user_id)
    if not token_path.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except Exception:
        return None
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            return None
    return creds

# ==========================
# Google Drive helpers
# ==========================
def create_personal_folder(service, user) -> str:
    folder_name = f"{(user.first_name or 'user')}_{user.id}"
    body = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [MAIN_FOLDER_ID] if MAIN_FOLDER_ID and MAIN_FOLDER_ID != "root" else []
    }
    folder = service.files().create(body=body, fields="id").execute()
    return folder["id"]

def ensure_user_folder(creds: Credentials, user) -> str:
    folder_id = get_user_folder_id(user.id)
    service = build("drive", "v3", credentials=creds)
    if folder_id:
        try:
            service.files().get(fileId=folder_id, fields="id").execute()
            return folder_id
        except Exception:
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

def list_drive_videos(service, folder_id):
    return list_files_in_folder_with_types(service, folder_id, mime_prefix="video")

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
    return temp_path  # string path

def sync_user_folder(service, user, main_folder_id):
    # requires that user's credentials exist
    creds = credentials_for_user(user.id)
    if not creds:
        return []
    user_folder_id = ensure_user_folder(creds, user)
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
# YouTube helpers
# ==========================
def upload_single_file_to_youtube(creds, local_path, title, description, privacy="private"):
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {"title": title, "description": description, "tags": ["quran", "قرآن"], "categoryId": "22"},
        "status": {"privacyStatus": privacy}
    }
    media = MediaFileUpload(local_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
    return response.get("id")

# ==========================
# Telegram UI
# ==========================
def main_menu_keyboard():
    kb = [
        [InlineKeyboardButton("📂 عرض ملفاتي", callback_data="ui:myfiles")],
        [InlineKeyboardButton("📤 رفع ملف إلى Drive", callback_data="ui:upload")],
        [InlineKeyboardButton("🎬 اختيار فيديو للرفع إلى YouTube", callback_data="ui:choosevideo")],
        [InlineKeyboardButton("🔄 مزامنة مجلدي مع الرئيسي", callback_data="ui:sync")],
        [
            InlineKeyboardButton("ℹ️ حول البوت", callback_data="ui:about"),
            InlineKeyboardButton("🛠 الدعم الفني", callback_data="ui:support")
        ]
    ]
    return InlineKeyboardMarkup(kb)

# ==========================
# Telegram Handlers
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "ضيف"
    welcome_text = (
        f"🎉 مرحبًا بك يا <b>{name}</b>!\n\n"
        f"🚀 <b>CloudDrive Bot</b>\n{ABOUT_DESCRIPTION}\n\n"
        "👇 استخدم الأزرار التالية للبدء:"
    )
    await update.message.reply_html(welcome_text, reply_markup=main_menu_keyboard())

async def greet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(random.choice(RESPONSES).format(name=user.first_name or "ضيف"))

# ----------------------
# OAuth Handlers
# ----------------------
async def auth_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS,
            scopes=SCOPES,
            redirect_uri=f"{WEBHOOK_URL}/oauth2callback"
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", include_granted_scopes="true")
        # store the flow object into user_data so we can finish later
        context.user_data["flow"] = flow
        await update.message.reply_text(f"🔗 افتح الرابط لتسجيل الدخول:\n{auth_url}\n\nانسخ الكود وأرسله هنا.")
    except Exception as e:
        await update.message.reply_text("❌ خطأ OAuth: " + str(e))

async def receive_oauth_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if "flow" not in context.user_data:
        # not in oauth flow; ignore (or you may want to treat as normal message)
        return
    flow: Flow = context.user_data["flow"]
    code = update.message.text.strip()
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_path_for_user(user.id).write_text(creds.to_json(), encoding="utf-8")
        context.user_data.pop("flow", None)
        # create user folder
        try:
            service = build("drive", "v3", credentials=creds)
            ensure_user_folder(creds, user)
        except Exception:
            pass
        await update.message.reply_text("✅ تم ربط حساب Google بنجاح.")
    except Exception as e:
        await update.message.reply_text("❌ رمز OAuth غير صالح أو فشل التبادل: " + str(e))
        context.user_data.pop("flow", None)

# ----------------------
# Upload file messages
# ----------------------
async def upload_file_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    creds = credentials_for_user(user.id)
    if not creds:
        await update.message.reply_text("⚠️ لم تربط حسابك بعد. استخدم /auth أولاً.")
        return
    service = build("drive", "v3", credentials=creds)
    folder_id = ensure_user_folder(creds, user)
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
# List user's Drive files (command)
# ----------------------
async def mydrive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    creds = credentials_for_user(user.id)
    if not creds:
        await update.message.reply_text("⚠️ لم تربط حسابك بعد. استخدم /auth أولاً.")
        return
    try:
        folder_id = ensure_user_folder(creds, user)
        service = build("drive", "v3", credentials=creds)
        files = list_drive_videos(service, folder_id)
        drive_link = f"https://drive.google.com/drive/folders/{folder_id}"
        if not files:
            await update.message.reply_html(f"📁 مجلدك: <code>{folder_id}</code>\n🔗 <a href='{drive_link}'>فتح المجلد</a>\nلا توجد ملفات بعد.")
            return
        kb = [[InlineKeyboardButton(f["name"], callback_data=f"uploadvideo:{f['id']}")] for f in files]
        kb.append([InlineKeyboardButton("🔙 إلغاء", callback_data="home")])
        await update.message.reply_html(f"📁 مجلدك: <code>{folder_id}</code>\n🔗 <a href='{drive_link}'>فتح المجلد</a>\nاختر ملفًا:", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text("❌ خطأ أثناء الوصول إلى Drive: " + str(e))

# ----------------------
# Callback Router (handles all button clicks)
# ----------------------
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    user = q.from_user
    creds = credentials_for_user(user.id)

    # عرض ملفاتي (قائمة ملفات الفيديو)
    if data == "ui:myfiles":
        if not creds:
            await q.edit_message_text("⚠️ يرجى ربط الحساب عبر /auth")
            return
        folder_id = ensure_user_folder(creds, user)
        service = build("drive", "v3", credentials=creds)
        files = list_drive_videos(service, folder_id)

        if not files:
            await q.edit_message_text("📁 لا توجد ملفات في مجلدك.")
            return

        kb = [[InlineKeyboardButton(f["name"], callback_data=f"uploadvideo:{f['id']}")] for f in files]
        kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="home")])

        msg = f"📂 ملفاتك ({len(files)}):"
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        return

    # رفع ملف الى Drive: نطلب من المستخدم إرسال الملف بعد الضغط
    if data == "ui:upload":
        await q.edit_message_text("📤 أرسل الآن أي ملف (مستند/صورة/فيديو) وسيتم رفعه إلى مجلدك في Drive.\n\nملاحظة: تأكد أنّك قمت بربط حسابك عبر /auth أولاً.")
        return

    # اختيار فيديو للرفع إلى YouTube
    if data == "ui:choosevideo":
        if not creds:
            await q.edit_message_text("⚠️ لم تربط حسابك بعد. استخدم /auth")
            return

        folder_id = ensure_user_folder(creds, user)
        service = build("drive", "v3", credentials=creds)
        files = list_drive_videos(service, folder_id)

        if not files:
            await q.edit_message_text("📁 لا توجد فيديوهات في مجلدك.")
            return

        kb = [[InlineKeyboardButton(f["name"], callback_data=f"uploadvideo:{f['id']}")] for f in files]
        kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="home")])

        await q.edit_message_text("🎬 اختر الفيديو ليتم رفعه إلى YouTube:", reply_markup=InlineKeyboardMarkup(kb))
        return

    # مزامنة المجلد مع MAIN_FOLDER_ID
    if data == "ui:sync":
        if not creds:
            await q.edit_message_text("⚠️ لم تربط حسابك بعد. استخدم /auth")
            return
        try:
            service = build("drive", "v3", credentials=creds)
            result = sync_user_folder(service, user, MAIN_FOLDER_ID)
            if not result:
                await q.edit_message_text("✔ مجلدك محدث بالفعل أو لا توجد ملفات جديدة للنسخ.")
            else:
                names = "\n".join(f"- {x['name']}" for x in result)
                await q.edit_message_text(f"🔄 تم نسخ {len(result)} ملف/فيديو:\n{names}")
        except Exception as e:
            await q.edit_message_text("❌ خطأ أثناء المزامنة: " + str(e))
        return

    # حول البوت
    if data == "ui:about":
        await q.edit_message_text(
            f"ℹ️ حول البوت:\n{ABOUT_DESCRIPTION}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="home")]])
        )
        return

    # الدعم الفني
    if data == "ui:support":
        await q.edit_message_text(
            f"🛠 الدعم الفني:\n{SUPPORT_CHANNEL_URL}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="home")]])
        )
        return

    # زر الرجوع للصفحة الرئيسية
    if data == "home":
        try:
            await q.edit_message_text("👇 القائمة الرئيسية:", reply_markup=main_menu_keyboard())
        except Exception:
            # fallback: send a new message if edit failed
            await context.bot.send_message(chat_id=user.id, text="👇 القائمة الرئيسية:", reply_markup=main_menu_keyboard())
        return

    # رفع فيديو إلى يوتيوب من ملف Drive
    if data.startswith("uploadvideo:"):
        file_id = data.split(":", 1)[1]
        try:
            await q.edit_message_text("⏳ جاري تجهيز الفيديو للرفع...")
        except Exception:
            pass
        # call the upload worker (it will send messages back)
        await handle_drive_to_youtube(user.id, file_id, context, reply_target=q)
        return

    # Unknown action fallback
    await q.edit_message_text("❓ حدث خطأ: إجراء غير معروف.")

# ----------------------
# Handle Drive -> YouTube (worker)
# ----------------------
async def handle_drive_to_youtube(user_id: int, drive_file_id: str, context: ContextTypes.DEFAULT_TYPE, reply_target=None):
    async def send(text):
        try:
            # try replying to the callback query message
            if reply_target and hasattr(reply_target, "message"):
                await reply_target.message.reply_text(text)
                return
        except Exception:
            pass
        try:
            if reply_target:
                await reply_target.edit_message_text(text)
                return
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text=text)

    creds = credentials_for_user(user_id)
    if not creds:
        await send("⚠️ لم تربط حسابك بعد. استخدم /auth أولاً.")
        return

    try:
        drive_service = build("drive", "v3", credentials=creds)
        meta = drive_service.files().get(fileId=drive_file_id, fields="id,name,mimeType,size").execute()
        file_name = meta.get("name") or f"{drive_file_id}"
    except Exception as e:
        await send("❌ لا يمكن الوصول إلى ملف Drive: " + str(e))
        return

    # download to temp
    try:
        tmp_file = download_drive_file_to_temp(drive_service, drive_file_id, file_name)
    except Exception as e:
        await send("❌ فشل تنزيل الملف من Drive: " + str(e))
        return

    await send("⬆️ جاري رفع الملف إلى YouTube... (قد يستغرق بعض الوقت)")

    try:
        youtube = build("youtube", "v3", credentials=creds)
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
        video_id = response.get("id")
        await send(f"✅ تم رفع الفيديو إلى YouTube بنجاح! https://youtu.be/{video_id}")
    except Exception as e:
        await send("❌ خطأ أثناء رفع الفيديو إلى YouTube: " + str(e))
    finally:
        try:
            if tmp_file and os.path.exists(tmp_file):
                os.remove(tmp_file)
        except Exception:
            pass

# ==========================
# Main
# ==========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("auth", auth_youtube))
    app.add_handler(CommandHandler("mydrive", mydrive_command))

    # Messages: OAuth code (text) and file uploads
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_oauth_code))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, upload_file_message_handler))

    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_router))

    print("🚀 Bot is running (Webhook mode)...")
    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{TOKEN}"
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()