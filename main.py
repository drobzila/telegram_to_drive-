import os
import json
import random
import asyncio
from pathlib import Path
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

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==========================
# الإعدادات العامة
# ==========================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", "8443"))

if not TOKEN or not WEBHOOK_URL:
    raise Exception("⚠️ يجب تعيين TELEGRAM_TOKEN وWEBHOOK_URL في Environment Variables")

OUTPUTS_DIR = Path("outputs")
TEMP_DIR = Path("temp")
OUTPUTS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

MAIN_FOLDER_ID = "1lLKbFPovufWeEkwpCgI3cM-Je-Uee9el"
USER_DB = Path("user_folders.json")

RESPONSES = ["السلام عليكم يا {name} 🌸", "أهلًا وسهلًا يا {name} 👋"]

# ==========================
# Google Drive Helpers
# ==========================
def get_drive_service():
    if not os.path.exists("token.json"):
        raise Exception("⚠️ ملف token.json غير موجود!")
    creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/drive"])
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
    folder_name = f"{user.first_name or 'User'}_{user.id}"

    if uid in db:
        return db[uid]

    folder_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["root"]
    }

    folder = service.files().create(body=folder_metadata, fields="id").execute()
    folder_id = folder["id"]
    db[uid] = folder_id
    save_user_db(db)
    return folder_id


def upload_file_to_user_folder(service, user, local_path):
    folder_id = get_or_create_user_folder(service, user)
    metadata = {"name": Path(local_path).name, "parents": [folder_id]}
    media = MediaFileUpload(local_path)
    uploaded = service.files().create(body=metadata, media_body=media, fields="id").execute()
    return uploaded["id"]


def list_drive_videos(service, folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'video/'"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get("files", [])


# ==========================
# واجهة الترحيب الرئيسية
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "ضيف"

    text = (
        f"🎉 مرحبًا بك يا <b>{name}</b>!\n\n"
        "🚀 <b>CloudDrive Bot</b>\n"
        "نظام إدارة ملفات احترافي يعتمد على الذكاء الاصطناعي.\n\n"
        "👇 اختر من القائمة:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("ℹ️ حول البوت", callback_data="about")],
        [InlineKeyboardButton("🛠 الدعم", callback_data="support")],
    ])

    await update.message.reply_html(text, reply_markup=keyboard)


# ==========================
# أزرار الترحيب
# ==========================
async def ui_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "about":
        await q.edit_message_text(
            "ℹ️ <b>حول البوت</b>\n"
            "CloudDrive Bot - أفضل نظام إدارة ملفات على Google Drive.\n\n"
            "👇 رجوع:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back")]]),
            parse_mode="HTML"
        )

    elif q.data == "support":
        await q.edit_message_text(
            "📩 <b>الدعم الفني</b>\n"
            "للمساعدة تواصل معنا:\n\n"
            "📧 البريد: lesquatrefreresazri@gmail.com\n"
            "▶️ قناة اليوتيوب: Qurani Studio\n\n"
            "👇 رجوع:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back")]]),
            parse_mode="HTML"
        )

    elif q.data == "back":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("ℹ️ حول البوت", callback_data="about")],
            [InlineKeyboardButton("🛠 الدعم", callback_data="support")],
        ])
        await q.edit_message_text("🏠 <b>القائمة الرئيسية</b>", reply_markup=keyboard, parse_mode="HTML")


# ==========================
# أوامر Google Drive
# ==========================
async def help_command(update: Update, context):
    await update.message.reply_text(
        "/myfolder - مجلدك\n"
        "/listvideos - عرض فيديوهات المجلد الرئيسي\n"
        "/choosevideo - نسخ فيديو إلى مجلدك\n"
    )

async def greet(update: Update, context):
    user = update.effective_user
    await update.message.reply_text(random.choice(RESPONSES).format(name=user.first_name))

async def myfolder(update: Update, context):
    service = get_drive_service()
    user = update.effective_user
    folder = get_or_create_user_folder(service, user)
    await update.message.reply_text(f"✨ مجلدك الخاص:\n{folder}")

async def upload_file(update: Update, context):
    if not update.message.document:
        return await update.message.reply_text("❌ لم يتم إرسال ملف!")
    
    service = get_drive_service()
    user = update.effective_user
    doc = update.message.document

    local_path = TEMP_DIR / doc.file_name
    await doc.get_file().download_to_drive(local_path)

    file_id = upload_file_to_user_folder(service, user, local_path)
    local_path.unlink(missing_ok=True)

    await update.message.reply_text(f"✔ رفع الملف! ID: {file_id}")

async def list_videos_command(update: Update, context):
    service = get_drive_service()
    videos = list_drive_videos(service, MAIN_FOLDER_ID)

    if not videos:
        return await update.message.reply_text("❌ لا يوجد فيديوهات!")

    msg = "📽 الفيديوهات:\n" + "\n".join([v["name"] for v in videos])
    await update.message.reply_text(msg)

async def choose_video_command(update: Update, context):
    service = get_drive_service()
    videos = list_drive_videos(service, MAIN_FOLDER_ID)

    if not videos:
        return await update.message.reply_text("❌ لا يوجد فيديوهات للنسخ!")

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(v["name"], callback_data=f"copy:{v['id']}")] for v in videos])

    await update.message.reply_text("اختر الفيديو:", reply_markup=keyboard)

async def drive_copy_handler(update: Update, context):
    q = update.callback_query
    await q.answer()

    if not q.data.startswith("copy:"):
        return

    video_id = q.data.split(":", 1)[1]
    user = q.from_user
    service = get_drive_service()
    folder = get_or_create_user_folder(service, user)

    service.files().copy(fileId=video_id, body={"parents": [folder]}).execute()
    await q.edit_message_text("✔ تم نسخ الفيديو إلى مجلدك!")


# ==========================
# تشغيل Webhook
# ==========================
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    # UI Buttons
    app.add_handler(CallbackQueryHandler(ui_buttons, pattern="^(about|support|back)$"))

    # Drive copy buttons
    app.add_handler(CallbackQueryHandler(drive_copy_handler, pattern="^copy:"))

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myfolder", myfolder))
    app.add_handler(CommandHandler("listvideos", list_videos_command))
    app.add_handler(CommandHandler("choosevideo", choose_video_command))

    # Messages
    app.add_handler(MessageHandler(filters.Document.ALL, upload_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, greet))

    print("🚀 Bot running with Webhook...")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )
