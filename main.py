import os
import json
import random
from pathlib import Path
from datetime import datetime
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

# مجلدات مؤقتة ومخرجات
OUTPUTS_DIR = Path("outputs")
OUTPUTS_DIR.mkdir(exist_ok=True)
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

# -------- Google Drive ----------
USER_DB = Path("user_folders.json")

def get_drive_service():
    if not os.path.exists("token.json"):
        raise Exception("⚠️ لم يتم العثور على token.json")
    creds = Credentials.from_authorized_user_file(
        "token.json",
        ["https://www.googleapis.com/auth/drive.file"]
    )
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
    file_metadata = {"name": Path(local_path).name, "parents": [folder_id]}
    media = MediaFileUpload(local_path)
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return uploaded["id"]

def list_drive_videos(service, folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'video/'"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get("files", [])

# -------- الوقت و الترحيب --------
def get_time_greeting():
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "☀️ صباح الخير"
    elif 12 <= hour < 16:
        return "🌤️ ظهر سعيد"
    elif 16 <= hour < 20:
        return "🌇 مساء الخير"
    else:
        return "🌙 ليلة سعيدة"

WELCOME_IMAGES = [
    "https://i.imgur.com/Asl8WjD.jpeg",
    "https://i.imgur.com/SuU7hVg.jpeg",
    "https://i.imgur.com/FSzn4tF.jpeg"
]

WELCOME_GIFS = [
    "https://i.imgur.com/4M7IWwP.gif",
    "https://i.imgur.com/TcJH4kf.gif"
]

BRAND_INTRO_VIDEOS = [
    "https://cdn.pixabay.com/vimeo/456438756/blue-tech-waves-19058.mp4",
    "https://cdn.pixabay.com/vimeo/300842798/blue-particles-motion-background-9747.mp4",
    "https://cdn.pixabay.com/vimeo/437927518/hi-tech-blue-grid-18502.mp4"
]

BOT_LOGO = "https://i.imgur.com/tgFRxU8.png"

BRAND_TEMPLATE = """
<b>🎉 مرحبًا بك يا {name}!</b>

<b>🚀 {bot_name}</b>
نظام إدارة ملفات متكامل يعمل بالذكاء الاصطناعي
ويتيح لك رفع ملفاتك وتنظيمها داخل Google Drive بكل سهولة وأمان.

<b>✨ مميزات البوت:</b>
• رفع مباشر إلى Google Drive  
• نسخ ونقل الملفات بسهولة  
• إنشاء مجلد لكل مستخدم  
• دعم الفيديوهات والصور والملفات  
• سرعة وأداء عالي  

<b>👇 اختر من القائمة للبدء:</b>
"""

def branded_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 ملفاتي", callback_data="open_folder")],
        [InlineKeyboardButton("📤 رفع ملف", callback_data="upload_help")],
        [InlineKeyboardButton("🧭 القائمة الرئيسية", callback_data="main_menu")],
        [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help_menu")]
    ])

# -------- دوال البوت ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await greet(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - بدء\n/help - المساعدة\n/myfolder - إنشاء مجلد\n/listvideos - عرض الفيديوهات\n/choosevideo - نسخ فيديو"
    )

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
    user = update.effective_user
    folder_id = get_or_create_user_folder(service, user)
    videos = list_drive_videos(service, folder_id)

    if not videos:
        await update.message.reply_text("📭 مجلدك فارغ! قم برفع فيديو أولاً.")
        return

    msg = "🎞 فيديوهاتك:\n" + "\n".join([f"{i+1}. {v['name']}" for i, v in enumerate(videos)])
    await update.message.reply_text(msg)

async def choose_video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service = get_drive_service()
    user = update.effective_user
    folder_id = get_or_create_user_folder(service, user)
    videos = list_drive_videos(service, folder_id)

    if not videos:
        await update.message.reply_text("❌ لا يوجد فيديوهات في مجلدك!")
        return

    keyboard = [[InlineKeyboardButton(v['name'], callback_data=v['id'])] for v in videos]
    await update.message.reply_text("اختر فيديو:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "open_folder":
        await query.edit_message_text("📁 جاري فتح مجلدك…")
    elif query.data == "upload_help":
        await query.edit_message_text("📤 أرسل أي ملف الآن لرفعه!")
    elif query.data == "help_menu":
        await query.edit_message_text(
            "ℹ️ قائمة المساعدة:\n/start\n/help\n/myfolder\n/listvideos\n/choosevideo"
        )
    else:
        # نسخ الفيديو إذا تم اختيار ID
        video_id = query.data
        user = query.from_user
        service = get_drive_service()
        user_folder_id = get_or_create_user_folder(service, user)
        service.files().copy(fileId=video_id, body={"parents": [user_folder_id]}).execute()
        await query.edit_message_text("✔ تم نسخ الفيديو!")

# -------- الترحيب الاحترافي الكامل --------
async def greet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "صديقي"
    bot_name = "CloudDrive Bot"   # غيّره لاسم بوتك

    caption = BRAND_TEMPLATE.format(
        name=name,
        bot_name=bot_name
    )

    # أرسل شعار أولًا
    await update.message.reply_photo(BOT_LOGO)

    # إرسال فيديو الهوية مع الأزرار
    video = random.choice(BRAND_INTRO_VIDEOS)
    await update.message.reply_video(
        video=video,
        caption=caption,
        parse_mode="HTML",
        reply_markup=branded_buttons(),
        supports_streaming=True
    )

# -------- تشغيل البوت عبر Webhook ----------
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
        webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{TOKEN}"
    )
