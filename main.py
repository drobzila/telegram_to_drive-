import os
import json
import random
import asyncio
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# -------- إعدادات --------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", "8443"))

if not TOKEN or not WEBHOOK_URL:
    raise Exception("⚠️ يجب تعيين TELEGRAM_TOKEN وWEBHOOK_URL في Environment Variables")

# مجلدات
OUTPUTS_DIR = Path("outputs")
OUTPUTS_DIR.mkdir(exist_ok=True)
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

# مجلد Google Drive الرئيسي
MAIN_FOLDER_ID = "1lLKbFPovufWeEkwpCgI3cM-Je-Uee9el"

RESPONSES = ["السلام عليكم يا {name} 🌸", "أهلًا وسهلًا يا {name} 👋"]

# ---------- دوال Google Drive ----------
def get_drive_service():
    if not os.path.exists("token.json"):
        raise Exception("⚠️ لم يتم العثور على token.json")
    creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds)

USER_DB = Path("user_folders.json")

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
    file_metadata = {"name": Path(local_path).name, "parents": [folder_id]}
    media = MediaFileUpload(local_path)
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return uploaded["id"]

def list_drive_videos(service, folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'video/'"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get("files", [])

# ---------- دوال البوت ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"مرحبًا يا {user.first_name}! اكتب /help لرؤية الأوامر.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - بدء\n/help - المساعدة\n/myfolder - إنشاء مجلد\n/listvideos - عرض الفيديوهات\n/choosevideo - نسخ فيديو"
    )

async def greet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    response = random.choice(RESPONSES).format(name=user.first_name or "User")
    await update.message.reply_text(response)

async def myfolder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service = get_drive_service()
    user = update.effective_user
    folder_id = get_or_create_user_folder(service, user)
    await update.message.reply_text(f"✨ تم إنشاء مجلدك!\nID: {folder_id}")

async def upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service = get_drive_service()
    user = update.effective_user
    if update.message.document:
        doc = update.message.document
        local_path = TEMP_DIR / doc.file_name
        await doc.get_file().download_to_drive(local_path)
        file_id = upload_file_to_user_folder(service, user, str(local_path))
        await update.message.reply_text(f"✔ تم رفع الملف! ID: {file_id}")
        local_path.unlink(missing_ok=True)
    else:
        await update.message.reply_text("❌ لم أجد أي ملف للرفع!")

async def list_videos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service = get_drive_service()
    videos = list_drive_videos(service, MAIN_FOLDER_ID)
    if not videos:
        await update.message.reply_text("❌ لا يوجد فيديوهات في المجلد الرئيسي!")
        return
    msg = f"📽 عدد الفيديوهات: {len(videos)}\n" + "\n".join([f"{i+1}. {v['name']}" for i, v in enumerate(videos)])
    await update.message.reply_text(msg)

async def choose_video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service = get_drive_service()
    videos = list_drive_videos(service, MAIN_FOLDER_ID)
    if not videos:
        await update.message.reply_text("❌ لا يوجد أي فيديوهات للاختيار!")
        return
    keyboard = [[InlineKeyboardButton(v['name'], callback_data=v['id'])] for v in videos]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("اختر الفيديو لنسخه:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_id = query.data
    user = query.from_user
    service = get_drive_service()
    user_folder_id = get_or_create_user_folder(service, user)
    service.files().copy(fileId=video_id, body={"parents": [user_folder_id]}).execute()
    await query.edit_message_text("✔ تم نسخ الفيديو!")

# ---------- تشغيل البوت عبر Webhook ----------
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myfolder", myfolder))
    app.add_handler(CommandHandler("listvideos", list_videos_command))
    app.add_handler(CommandHandler("choosevideo", choose_video_command))
    app.add_handler(MessageHandler(filters.Document.ALL, upload_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, greet))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🚀 البوت جاهز للعمل مع Webhook!")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )
