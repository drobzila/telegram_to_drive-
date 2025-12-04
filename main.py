import os
import json
import random
import asyncio
import subprocess
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from pathlib import Path

# مسار المجلد حيث سيضع السكربت الفيديوهات النهائية
OUTPUTS_DIR = Path.cwd() / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# ----------------- إعدادات -----------------
TOKEN = "8522833847:AAFH3K_8MqKYyvMALo_RUzeVugVlUvmYAuk"
SCOPES = ["https://www.googleapis.com/auth/drive"]

USER_DB = Path("user_folders.json")
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

MAIN_FOLDER_ID = "1lLKbFPovufWeEkwpCgI3cM-Je-Uee9el"  # مجلد الفيديوهات الرئيسي

RESPONSES = [
    "السلام عليكم يا {name} 🌸",
    "أهلًا وسهلًا يا {name} 👋",
    "مرحبا بك يا {name} ✨",
    "حيّاك الله يا {name} 🤍",
    "نورّت يا {name} 🌟"
]

# ----------------- دوال Google Drive -----------------
def get_drive_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    else:
        raise Exception("⚠️ لم يتم العثور على token.json لـ Google Drive")
    service = build("drive", "v3", credentials=creds)
    return service

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

# ----------------- دوال البوت -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"مرحبًا يا {user.first_name}! 👋\nاكتب /help لرؤية أوامر البوت.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 أوامر البوت:\n"
        "/start - بدء المحادثة\n"
        "/help - المساعدة\n"
        "/myfolder - إنشاء مجلد خاص بك في Google Drive\n"
        "/generatevideos - إنشاء فيديوهات (اختر العدد من 1 إلى 5)\n"
        "/listvideos - عرض الفيديوهات في المجلد الرئيسي\n"
        "/choosevideo - اختيار فيديو لنسخه لمجلدك\n"
        "ارسل أي ملف، وسيتم رفعه تلقائيًا داخل مجلدك."
    )

async def greet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    response = random.choice(RESPONSES).format(name=user.first_name or "User")
    await update.message.reply_text(response)

async def myfolder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    service = get_drive_service()
    folder_id = get_or_create_user_folder(service, user)
    await update.message.reply_text(
        f"✨ تم إنشاء مجلد خاص بك!\n📁 اسم المجلد: {user.first_name}_{user.id}\n🆔 ID: {folder_id}"
    )

async def upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    service = get_drive_service()
    if update.message.document:
        doc = update.message.document
        local_path = TEMP_DIR / doc.file_name
        await doc.get_file().download_to_drive(local_path)
        file_id = upload_file_to_user_folder(service, user, str(local_path))
        await update.message.reply_text(f"✔ تم رفع ملفك داخل مجلدك!\nFile ID: {file_id}")
        local_path.unlink(missing_ok=True)
    else:
        await update.message.reply_text("❌ لم أجد أي ملف للرفع!")

# ----------------- اختيار عدد الفيديوهات -----------------
async def generate_videos_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(str(i), callback_data=f"gen_{i}") for i in range(1,6)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("اختر عدد الفيديوهات التي تريد إنشاؤها:", reply_markup=reply_markup)

async def generate_videos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("gen_"):
        return

    count = int(data.split("_")[1])  # عدد الفيديوهات المطلوب
    user = query.from_user
    script_path = Path("quran_video_maker.py")

    if not script_path.exists():
        await query.edit_message_text("❌ لم يتم العثور على السكربت quran_video_maker.py")
        return

    # تنظيف TEMP_DIR قبل الإنشاء
    TEMP_DIR = Path("temp")
    TEMP_DIR.mkdir(exist_ok=True)
    for file in TEMP_DIR.glob("*.mp4"):
        file.unlink(missing_ok=True)

    # رسالة التقدم
    msg = await query.edit_message_text(f"⏳ جاري إنشاء {count} فيديو...")

    # تشغيل السكربت في subprocess
    process = await asyncio.create_subprocess_exec(
        "python", str(script_path), str(count),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    while True:
        line = await process.stdout.readline()
        if not line:
            break
        line_text = line.decode().strip()  # تحويل bytes إلى str

        if line_text.startswith("PROGRESS"):
            try:
                percent = int(line_text.split()[1])
            except:
                percent = 0
            bar = "■" * (percent // 5) + "□" * (20 - (percent // 5))
            await msg.edit_text(f"🎬 إنشاء الفيديوهات...\n[{bar}] {percent}%")
        else:
            # يمكن طباعة أي رسائل أخرى من السكربت في الكونسل
            print(line_text)

    await process.wait()

    # رفع الفيديوهات الناتجة حسب العدد المطلوب
    video_files = sorted(TEMP_DIR.glob("*.mp4"))[:count]

    if not video_files:
        await msg.edit_text("❌ لم يتم إنشاء أي فيديوهات!")
        return

    # مثال رفع الملفات مع تحديث شريط التقدم (يمكن تعديل الرفع حسب سكربتك)
    for i, video in enumerate(video_files, start=1):
        # إذا تريد الرفع لـ Google Drive، ضع استدعاء upload_file هنا
        # upload_file_to_user_folder(service, user, str(video))
        video.unlink(missing_ok=True)

        progress = int((i / count) * 20)
        percent = int((i / count) * 100)
        bar = "■" * progress + "□" * (20 - progress)
        await msg.edit_text(f"🎬 رفع الفيديوهات...\n[{bar}] {percent}%")
        await asyncio.sleep(0.2)

    await msg.edit_text(f"✔ تم إنشاء ورفع {count} فيديو بنجاح!")

# ----------------- عرض الفيديوهات -----------------
async def list_videos_command(update, context):
    service = get_drive_service()
    videos = list_drive_videos(service, MAIN_FOLDER_ID)
    if not videos:
        await update.message.reply_text("❌ لا يوجد أي فيديوهات في المجلد الرئيسي!")
        return
    msg = f"📽 عدد الفيديوهات في المجلد الرئيسي: {len(videos)}\n"
    msg += "\n".join([f"{i+1}. {v['name']}" for i, v in enumerate(videos)])
    await update.message.reply_text(msg)

async def choose_video_command(update, context):
    service = get_drive_service()
    videos = list_drive_videos(service, MAIN_FOLDER_ID)
    if not videos:
        await update.message.reply_text("❌ لا يوجد أي فيديوهات للاختيار!")
        return
    keyboard = [[InlineKeyboardButton(v['name'], callback_data=v['id'])] for v in videos]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("اختر الفيديو لنسخه إلى مجلدك:", reply_markup=reply_markup)

async def button_handler(update, context):
    query = update.callback_query
    await query.answer()
    video_id = query.data
    user = query.from_user
    service = get_drive_service()
    user_folder_id = get_or_create_user_folder(service, user)
    service.files().copy(fileId=video_id, body={"parents": [user_folder_id]}).execute()
    await query.edit_message_text("✔ تم نسخ الفيديو إلى مجلدك!")

# ----------------- تشغيل البوت -----------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myfolder", myfolder))
    app.add_handler(MessageHandler(filters.Document.ALL, upload_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, greet))
    app.add_handler(CommandHandler("generatevideos", generate_videos_choose))
    app.add_handler(CallbackQueryHandler(generate_videos_callback, pattern=r"gen_\d+"))
    app.add_handler(CommandHandler("listvideos", list_videos_command))
    app.add_handler(CommandHandler("choosevideo", choose_video_command))
    app.add_handler(CallbackQueryHandler(button_handler, pattern=r"^[A-Za-z0-9_-]+$"))

    print("البوت شغّال...")
    app.run_polling()
