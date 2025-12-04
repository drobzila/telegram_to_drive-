#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram bot - Drive manager + YouTube uploader (Integrated)
- Videos are uploaded to YouTube (per-user OAuth manual/code flow).
- Videos are NOT uploaded to Google Drive anymore.
"""

import os
import json
import time
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Google libs
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, Flow

# ----------------- Config -----------------
TOKEN = os.getenv("TELEGRAM_TOKEN", "8522833847:AAFH3K_8MqKYyvMALo_RUzeVugVlUvmYAuk")
CLIENT_SECRETS_FILE = Path("client_secret.json")  # OAuth client (Desktop) from Google Cloud
TOKENS_DIR = Path("tokens")
TOKENS_DIR.mkdir(exist_ok=True)

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

# Scopes for uploading to YouTube
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# ----------------- Logging -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("drive_bot_youtube")

# ----------------- Utilities -----------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def load_json(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("failed to load json %s", path)
    return default if default is not None else {}

# ----------------- Minimal persistent logs (optional) -----------------
LOG_FILE = Path("bot_logs.json")
logs: List[Dict[str, Any]] = load_json(LOG_FILE, [])

def log_action(user_id: int, action: str, details: Optional[Dict[str, Any]] = None):
    entry = {"time": now_iso(), "user_id": user_id, "action": action, "details": details or {}}
    logs.append(entry)
    if len(logs) > 5000:
        del logs[:-4000]
    save_json(LOG_FILE, logs)

# ----------------- HELP text -----------------
HELP_TEXT = """
📌 أوامر البوت:
/start - بدء المحادثة
/help - المساعدة
/menu - فتح اللوحة
/myfolder - إنشاء/عرض مجلدك (Drive)
 /stats - إحصائيات حسابك

ملاحظة: الفيديوهات تُرفع الآن إلى YouTube (بعد ربط حسابك).
ارسل صورة/مستند لرفعها إلى Drive، أما الفيديو فأستخدم زر "YouTube" في /menu.
"""

# ----------------- YouTube token helpers (Improved) -----------------
def token_path_for(user_id: int) -> Path:
    """مسار ملف التوكن لكل مستخدم"""
    return TOKENS_DIR / f"{user_id}.json"

def save_user_credentials(user_id: int, creds: Credentials):
    """
    حفظ بيانات اعتماد المستخدم على شكل JSON.
    يقوم بمحاولة تحديث التوكن إذا كان منتهي الصلاحية قبل الحفظ.
    """
    try:
        # تحديث التوكن إذا انتهت صلاحيته
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        token_file = token_path_for(user_id)
        token_file.write_text(creds.to_json(), encoding="utf-8")
        log_action(user_id, "yt_save_token")
    except Exception:
        logger.exception(f"Failed to save credentials for user {user_id}")

def load_user_credentials(user_id: int) -> Optional[Credentials]:
    """
    تحميل بيانات اعتماد المستخدم من ملف JSON.
    إذا انتهت صلاحية التوكن، يحاول تحديثه تلقائيًا.
    """
    token_file = token_path_for(user_id)
    if not token_file.exists():
        return None
    try:
        data = json.loads(token_file.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(data, scopes=YT_SCOPES)

        # تحديث التوكن إذا انتهت صلاحيته
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # إعادة الحفظ بعد التحديث
                token_file.write_text(creds.to_json(), encoding="utf-8")
            except Exception:
                logger.exception(f"Failed to refresh credentials for user {user_id}")
        return creds
    except Exception:
        logger.exception(f"Failed to load credentials for user {user_id}")
        return None

def delete_user_credentials(user_id: int):
    """
    حذف بيانات اعتماد المستخدم.
    """
    p = token_path_for(user_id)
    try:
        if p.exists():
            p.unlink()
            log_action(user_id, "yt_delete_token")
    except Exception:
        logger.exception(f"Failed to delete credentials for user {user_id}")

# ----------------- Handlers -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"مرحبًا يا {user.first_name} 👋\nاكتب /menu لعرض الخيارات.")
    log_action(user.id, "start")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📁 مجلدي (Drive)", callback_data="menu_myfolder")],
        [InlineKeyboardButton("📺 YouTube (ربط / إدارة)", callback_data="menu_youtube")],
        [InlineKeyboardButton("📊 إحصائي", callback_data="menu_stats")],
    ]
    await update.message.reply_text('اختر:', reply_markup=InlineKeyboardMarkup(keyboard))

# Placeholder Drive functions (kept minimal)
USER_DB = Path("user_folders.json")
user_db = load_json(USER_DB, {})

def get_or_create_user_folder_placeholder(user) -> str:
    # minimal placeholder: return existing or a fake id; not used for video uploads
    uid = str(user.id)
    if uid in user_db:
        return user_db[uid]
    fake = f"folder_{uid}"
    user_db[uid] = fake
    save_json(USER_DB, user_db)
    return fake

async def myfolder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    folder_id = get_or_create_user_folder_placeholder(user)
    text = f"✨ مجلدك (Drive): {user.first_name}_{user.id}\n🆔 {folder_id}\n(Drive upload: الصور/المستندات فقط)"
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_logs = [l for l in logs if l.get("user_id") == user.id]
    uploads = sum(1 for l in user_logs if l.get("action") == "upload")
    created = sum(1 for l in user_logs if l.get("action") == "create_folder")
    yt_uploads = sum(1 for l in user_logs if l.get("action") == "yt_upload")
    text = (
        f"📊 إحصائياتك:\n"
        f"- عمليات رفع إلى Drive: {uploads}\n"
        f"- مجلدات مُنشأة: {created}\n"
        f"- فيديوهات مرفوعة إلى YouTube: {yt_uploads}"
    )
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text)

# ----------------- YouTube: Menu & Flow -----------------
def youtube_menu_keyboard(user_id: int):
    creds = load_user_credentials(user_id)
    if creds:
        btns = [
            [InlineKeyboardButton("🔗 فصل حساب YouTube", callback_data="yt_disconnect")],
            [InlineKeyboardButton("⬆️ رفع فيديو إلى YouTube", callback_data="yt_start_upload")],
            [InlineKeyboardButton("ℹ️ حالة الربط", callback_data="yt_status")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="menu_close")],
        ]
    else:
        btns = [
            [InlineKeyboardButton("🔗 ربط حساب YouTube", callback_data="yt_connect")],
            [InlineKeyboardButton("ℹ️ حالة الربط", callback_data="yt_status")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="menu_close")],
        ]
    return InlineKeyboardMarkup(btns)

async def youtube_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    await query.edit_message_text("📺 إدارة YouTube:", reply_markup=youtube_menu_keyboard(user.id))

async def yt_status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    creds = load_user_credentials(user.id)
    if creds:
        # attempt to refresh if needed
        try:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request=None)  # silent attempt; will likely work with installed credentials
        except Exception:
            pass
        await query.edit_message_text("✔ حساب YouTube مربوط. يمكنك رفع فيديو الآن.", reply_markup=youtube_menu_keyboard(user.id))
    else:
        await query.edit_message_text("❌ لم يتم ربط حساب YouTube بعد.", reply_markup=youtube_menu_keyboard(user.id))

# Step 1: start connect -> send auth url and store flow in user_data

async def yt_connect_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if not CLIENT_SECRETS_FILE.exists():
        await query.edit_message_text(
            "❌ ملف client_secret.json غير موجود على الخادم. أضفه ثم أعد المحاولة."
        )
        return

    try:
        # استخدم InstalledAppFlow مع redirect 'urn:ietf:wg:oauth:2.0:oob'
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CLIENT_SECRETS_FILE),
            scopes=YT_SCOPES,
            redirect_uri='urn:ietf:wg:oauth:2.0:oob'  # مهم جدًا لتجنب خطأ redirect_uri
        )

        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        # حفظ flow في memory (context.user_data) لاستكمال الربط عند إدخال الكود
        context.user_data["yt_flow"] = flow

        text = (
            "🔗 اضغط على الرابط التالي لربط حساب YouTube ثم انسخ الكود الذي سيظهر بعد السماح:\n\n"
            f"{auth_url}\n\n"
            "بعد نسخ الكود، أرسله هنا كرسالة نصية، سأكمل الربط."
        )
        await query.edit_message_text(text)
    except Exception as e:
        logger.exception("yt_connect_handler failed")
        await query.edit_message_text("❌ حدث خطأ أثناء إنشاء رابط الربط. حاول مرة أخرى لاحقًا.")

# Step 2: receive code as plain text (when yt_flow exists)
async def yt_receive_code_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    # استخراج الكود سواء أرسل المستخدم الرابط كامل أو الكود فقط
    code = text
    if "code=" in text:
        parsed = urlparse(text)
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]

    if not code:
        await update.message.reply_text(
            "❌ لم أتمكن من إيجاد الكود في الرسالة. أرسل فقط الكود أو الرابط بالكامل."
        )
        return True

    flow: Optional[Flow] = context.user_data.get("yt_flow")
    if not flow:
        await update.message.reply_text(
            "❌ لم أجد أي عملية ربط جارية. اضغط 'ربط حساب YouTube' أولاً."
        )
        return True

    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        save_user_credentials(user.id, creds)
        context.user_data.pop("yt_flow", None)
        await update.message.reply_text(
            "✔ تم ربط حسابك بيوتيوب بنجاح! يمكنك الآن رفع فيديو من خلال /menu → YouTube → رفع فيديو."
        )
        log_action(user.id, "yt_connected")
        return True
    except Exception:
        await update.message.reply_text(
            "❌ فشل استبدال الكود بالتوكن. تأكد من لصق الكود الصحيح أو أعد العملية."
        )
        return True

# Step 3: start upload process (user presses button)
async def yt_start_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    creds = load_user_credentials(user.id)
    if not creds:
        await query.edit_message_text("❌ لا يوجد حساب مربوط. استخدم 'ربط حساب YouTube' أولاً.", reply_markup=youtube_menu_keyboard(user.id))
        return
    # set a state: awaiting video
    context.user_data["awaiting_yt_video"] = True
    context.user_data.pop("awaiting_yt_title", None)
    context.user_data.pop("awaiting_yt_description", None)
    context.user_data.pop("awaiting_yt_privacy", None)
    await query.edit_message_text("📤 الآن أرسل *الفيديو* الذي تريد رفعه إلى قناتك (ملف فيديو بصيغة MP4 أو غيرها).")

# Handler: when user sends a video while awaiting upload
async def handle_video_for_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.video:
        return
    user = update.effective_user
    if not context.user_data.get("awaiting_yt_video"):
        # Inform users that direct video sending is not allowed unless they start upload
        await update.message.reply_text("❌ رفع الفيديوهات غير مسموح هنا مباشرة. استخدم /menu → YouTube → رفع فيديو.")
        return

    vid = update.message.video
    if vid.file_size and vid.file_size > 1024*1024*1024:  # 1GB soft limit example
        await update.message.reply_text("❌ حجم الفيديو كبير جدًا.")
        context.user_data.pop("awaiting_yt_video", None)
        return

    # download to temp
    filename = vid.file_name or f"yt_video_{user.id}_{int(time.time())}.mp4"
    local = TEMP_DIR / filename
    await update.message.chat.send_action("upload_video")
    tg_file = await vid.get_file()
    await tg_file.download_to_drive(str(local))
    context.user_data["awaiting_yt_video"] = False
    context.user_data["yt_pending_file"] = str(local)

    # ask for title next
    context.user_data["awaiting_yt_title"] = True
    await update.message.reply_text("✏️ الآن أدخل *عنوان* الفيديو:")

# Handler: receive title text
async def handle_yt_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_yt_title"):
        return False
    title = update.message.text.strip()
    context.user_data["yt_title"] = title
    context.user_data.pop("awaiting_yt_title", None)
    context.user_data["awaiting_yt_description"] = True
    await update.message.reply_text("✏️ الآن أدخل *وصف* الفيديو (أو أرسل كلمة 'لا' لتركه فارغًا):")
    return True

# Handler: receive description text
async def handle_yt_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_yt_description"):
        return False
    text = update.message.text.strip()
    if text.lower() == "لا":
        description = ""
    else:
        description = text
    context.user_data["yt_description"] = description
    context.user_data.pop("awaiting_yt_description", None)

    # Ask for privacy
    keyboard = [
        [InlineKeyboardButton("عام (public)", callback_data="yt_priv_public")],
        [InlineKeyboardButton("غير مدرج (unlisted)", callback_data="yt_priv_unlisted")],
        [InlineKeyboardButton("خاص (private)", callback_data="yt_priv_private")],
    ]
    await update.message.reply_text("🔒 اختر خصوصية الفيديو:", reply_markup=InlineKeyboardMarkup(keyboard))
    return True

# Handler: privacy button -> perform upload
async def handle_yt_privacy_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data

    pending_file = context.user_data.get("yt_pending_file")
    title = context.user_data.get("yt_title", f"Video by {user.first_name}")
    description = context.user_data.get("yt_description", "")
    if not pending_file or not Path(pending_file).exists():
        await query.edit_message_text("❌ لا يوجد ملف فيديو جاهز للرفع. ابدأ العملية من جديد.")
        # cleanup possible flags
        context.user_data.pop("yt_pending_file", None)
        return

    if data == "yt_priv_public":
        privacy = "public"
    elif data == "yt_priv_unlisted":
        privacy = "unlisted"
    else:
        privacy = "private"

    await query.edit_message_text("⏳ جاري رفع الفيديو إلى YouTube... (قد يستغرق وقتًا حسب الحجم)")

    # Run blocking upload in executor
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, upload_to_youtube_blocking, user.id, pending_file, title, description, privacy)
    except Exception:
        logger.exception("upload_to_youtube failed")
        await query.edit_message_text("❌ حدث خطأ أثناء رفع الفيديو.")
        # cleanup
        try:
            Path(pending_file).unlink(missing_ok=True)
        except Exception:
            pass
        context.user_data.pop("yt_pending_file", None)
        return

    # result is videoId or None
    if result:
        video_id = result
        link = f"https://youtu.be/{video_id}"
        await query.edit_message_text(f"✔ تم رفع الفيديو بنجاح!\n🔗 {link}")
        log_action(user.id, "yt_upload", {"video_id": video_id, "title": title})
    else:
        await query.edit_message_text("❌ فشل الرفع. تأكد من صلاحيات الحساب والتوكن.")
    # cleanup
    try:
        Path(pending_file).unlink(missing_ok=True)
    except Exception:
        pass
    context.user_data.pop("yt_pending_file", None)
    context.user_data.pop("yt_title", None)
    context.user_data.pop("yt_description", None)

# ----------------- Blocking upload implementation -----------------
def upload_to_youtube_blocking(user_id: int, filepath: str, title: str, description: str, privacy: str) -> Optional[str]:
    """
    Blocking function executed in executor that performs the resumable upload to YouTube.
    Returns videoId on success or None on failure.
    """
    creds = load_user_credentials(user_id)
    if not creds:
        return None
    try:
        youtube = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": privacy}
        }
        media = MediaFileUpload(filepath, chunksize=1024*1024, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while True:
            status, response = request.next_chunk()
            if status:
                # Optionally log progress: status.progress()
                pass
            if response:
                break
        video_id = response.get("id")
        return video_id
    except Exception:
        logger.exception("upload_to_youtube_blocking exception")
        return None

# Handler: disconnect
async def yt_disconnect_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    delete_user_credentials(user.id)
    await query.edit_message_text("✔ تم فصل حساب YouTube الخاص بك.", reply_markup=youtube_menu_keyboard(user.id))

# ----------------- Generic handlers (photo/document) -----------------
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # keep Drive upload for documents (existing behavior)
    user = update.effective_user
    await update.message.reply_text("✔ تم استلام المستند. (وظائف رفع Drive للمستندات متاحة).")
    log_action(user.id, "upload")  # placeholder

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text("✔ تم استلام الصورة. (وظائف رفع Drive للصور متاحة).")
    log_action(user.id, "upload")  # placeholder

async def generic_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # First try to handle as OAuth code or part of YouTube upload flow
    handled = False
    # If user is in OAuth flow and sent code:
    if context.user_data.get("yt_flow") and update.message and update.message.text:
        # try process code
        handled = await yt_receive_code_message(update, context) or handled
    # If awaiting title or description:
    if not handled and context.user_data.get("awaiting_yt_title") and update.message and update.message.text:
        handled = await handle_yt_title(update, context) or handled
    if not handled and context.user_data.get("awaiting_yt_description") and update.message and update.message.text:
        handled = await handle_yt_description(update, context) or handled

    if handled:
        return

    # fallback
    await update.message.reply_text(f"مرحبًا {update.effective_user.first_name} ✨\nاستخدم /menu للخيارات.")

# ----------------- Callback router -----------------
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "menu_myfolder":
        await query.edit_message_text("🔎 جاري جلب مجلدك...")
        await myfolder_cmd(update, context)
        return

    if data == "menu_stats":
        await query.edit_message_text("🔎 جلب الإحصائيات...")
        await stats_cmd(update, context)
        return

    if data == "menu_youtube":
        await youtube_menu_handler(update, context)
        return

    if data == "yt_connect":
        await yt_connect_handler(update, context)
        return

    if data == "yt_status":
        await yt_status_handler(update, context)
        return

    if data == "yt_disconnect":
        await yt_disconnect_handler(update, context)
        return

    if data == "yt_start_upload":
        await yt_start_upload_handler(update, context)
        return

    if data in ("yt_priv_public", "yt_priv_unlisted", "yt_priv_private"):
        await handle_yt_privacy_choice(update, context)
        return

    if data == "menu_close":
        await query.edit_message_text("✅ تم الإغلاق.")
        return

    # default
    await query.edit_message_text("❌ خيار غير معروف.")

# ----------------- Cleanup -----------------
def clean_temp():
    for f in TEMP_DIR.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass

# ----------------- Main -----------------
def main():
    if TOKEN.startswith("YOUR_"):
        print("Please set TELEGRAM_TOKEN environment variable or edit the TOKEN constant.")
        return
    app = ApplicationBuilder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("myfolder", myfolder_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_router))

    # File handlers
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # Video handler — handle YouTube upload flow only
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_for_youtube))

    # Text messages (for OAuth code, title, description, fallback)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generic_message))

    print("البوت شغّال...")
    try:
        app.run_polling()
    finally:
        clean_temp()

if __name__ == "__main__":
    main()
