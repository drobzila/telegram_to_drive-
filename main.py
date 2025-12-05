import os
import json
import random
from pathlib import Path
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ---------------- إعدادات ----------------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", "8443"))

if not TOKEN or not WEBHOOK_URL:
    raise Exception("⚠️ يجب تعيين TELEGRAM_TOKEN وWEBHOOK_URL")

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

USER_DB = Path("user_folders.json")


# ---------------- Google Drive ----------------
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
        return json.loads(USER_DB.read_text("utf-8"))
    return {}

def save_user_db(db):
    USER_DB.write_text(json.dumps(db, indent=2, ensure_ascii=False), "utf-8")

def create_user_folder(service, user):
    db = load_user_db()
    uid = str(user.id)

    if uid in db:   # موجود مسبقا
        return db[uid]

    folder_metadata = {
        "name": f"QuraniUser_{user.id}",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["root"]
    }

    folder = service.files().create(body=folder_metadata, fields="id").execute()
    folder_id = folder["id"]

    db[uid] = folder_id
    save_user_db(db)

    return folder_id


# ---------------- رسالة الترحيب ----------------
WELCOME_MEDIA = [
    "https://i.imgur.com/4M7IWwP.gif",
    "https://i.imgur.com/TcJH4kf.gif",
    "https://i.imgur.com/Asl8WjD.jpeg"
]

BRAND_TEMPLATE = """
<b>🎉 أهلاً {name}!</b>

مرحبًا بك في <b>{bot_name}</b> 👋  
أفضل منشئ للفيديوهات القرآنية باستخدام الذكاء الاصطناعي.

<b>✨ مميزات Qurani Studio:</b>
• تصميم فيديوهات احترافية للآيات  
• دمج صوت القارئ مع خلفيات هادئة  
• تأثيرات جميلة وجودة عالية  
• إنشاء مجلد خاص بك لحفظ أعمالك  
• سرعة وسهولة ودقة في الإخراج  

<b>👇 اضغط ابدأ الآن لإنشاء مجلدك وبدء العمل:</b>
"""

def main_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 ابدأ الآن", callback_data="create_folder")],
        [InlineKeyboardButton("ℹ️ حول البوت", callback_data="about_bot")],
        [InlineKeyboardButton("🛠 الدعم", callback_data="support")]
    ])


# ---------------- start ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    caption = BRAND_TEMPLATE.format(
        name=user.first_name,
        bot_name="Qurani Studio Bot"
    )

    await update.message.reply_animation(
        animation=random.choice(WELCOME_MEDIA),
        caption=caption,
        parse_mode="HTML",
        reply_markup=main_buttons()
    )


# ---------------- buttons ----------------
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "create_folder":
        user = q.from_user
        try:
            service = get_drive_service()
            folder_id = create_user_folder(service, user)

            await q.edit_message_text(
                f"📁 تم إنشاء مجلدك بنجاح!\n\n"
                f"<b>ID:</b> <code>{folder_id}</code>\n\n"
                "يمكننا الآن إضافة ميزات أخرى مثل رفع الملفات أو إنشاء فيديوهات قرآنية.",
                parse_mode="HTML"
            )
        except Exception as e:
            await q.edit_message_text(f"❌ خطأ أثناء إنشاء المجلد:\n{e}")

    elif q.data == "about_bot":
        await q.edit_message_text(
            "🤖 <b>Qurani Studio</b>\n"
            "أفضل نظام لإنشاء الفيديوهات القرآنية بجودة عالية وتأثيرات احترافية.",
            parse_mode="HTML"
        )

    elif q.data == "support":
        await q.edit_message_text("🛠 سيتم إضافة الدعم قريبًا…")


# ---------------- تشغيل Webhook ----------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_buttons))

    print("🚀 البوت يعمل الآن!")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{TOKEN}"
    )
