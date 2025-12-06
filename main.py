import os
import json
import random
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

# مجلدات محلية
OUTPUTS_DIR = Path("outputs")
TEMP_DIR = Path("temp")
OUTPUTS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# معرّف مجلد الفيديوهات الرئيسي على Google Drive (غيّر إذا لزم)
MAIN_FOLDER_ID = "1lLKbFPovufWeEkwpCgI3cM-Je-Uee9el"

# قاعدة لتخزين معرفات مجلدات المستخدمين محلياً
USER_DB = Path("user_folders.json")

# ردود ترحيب عشوائية
RESPONSES = ["السلام عليكم يا {name} 🌸", "أهلًا وسهلًا يا {name} 👋"]

# ==========================
# دوال Google Drive
# ==========================
def get_drive_service():
    if not os.path.exists("token.json"):
        raise Exception("⚠️ ملف token.json غير موجود! اتبع خطوات OAuth للحصول على token.json")
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
    folder_name = f"{(user.first_name or 'User')}_{user.id}"
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
    media = MediaFileUpload(str(local_path))
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return uploaded["id"]

def list_drive_videos(service, folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'video/'"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get("files", [])

def list_files_in_folder(service, folder_id):
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    return results.get("files", [])

# ==========================
# الواجهة (مودرن) - أزرار
# ==========================
def main_menu_keyboard():
    kb = [
        [InlineKeyboardButton("📂 إدارة الملفات", callback_data="ui:myfiles")],
        [InlineKeyboardButton("📤 رفع ملف إلى Drive", callback_data="ui:upload")],
        [InlineKeyboardButton("🎬 نسخ فيديو إلى ملفاتي", callback_data="ui:choosevideo")],
        [InlineKeyboardButton("ℹ️ حول البوت", callback_data="ui:about"),
         InlineKeyboardButton("🛠 الدعم الفني", callback_data="ui:support")]
    ]
    return InlineKeyboardMarkup(kb)

# ==========================
# أوامر وبداية
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "ضيف"
    welcome_text = (
        f"🎉 مرحبًا بك يا <b>{name}</b>!\n\n"
        "🚀 <b>CloudDrive Bot</b>\n"
        "نظام إدارة ملفات احترافي على Google Drive — سريع وآمن.\n\n"
        "👇 استخدم الأزرار التالية للبدء:"
    )
    await update.message.reply_html(welcome_text, reply_markup=main_menu_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - العودة للقائمة\n"
        "/myfolder - إنشاء/عرض مجلدك\n"
        "/listvideos - عرض فيديوهات المجلد الرئيسي\n"
        "/choosevideo - بدء نسخ فيديو"
    )

# تبسيط ردود التحية للنصوص العادية
async def greet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(random.choice(RESPONSES).format(name=user.first_name or "ضيف"))

# ==========================
# معالجة CallbackQuery العامة (الواجهة المودرن)
# ==========================
async def ui_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    # ----- ملفاتي -----
    if data == "ui:myfiles":
        service = get_drive_service()
        user = q.from_user
        folder_id = get_or_create_user_folder(service, user)
        # رابط فتح المجلد على Drive
        drive_link = f"https://drive.google.com/drive/folders/{folder_id}"
        files = list_files_in_folder(service, folder_id)
        text = f"📁 <b>مجلدك الشخصي</b>\nID: <code>{folder_id}</code>\n\n"
        text += f"🔗 <a href='{drive_link}'>فتح المجلد في Drive</a>\n\n"
        if not files:
            text += "📭 لا توجد ملفات حالياً في مجلدك."
        else:
            text += "📄 ملفاتك:\n" + "\n".join([f"- {f['name']}" for f in files])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 رفع ملف", callback_data="ui:upload")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="ui:back")]
        ])
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=False)

    # ----- رفع ملف (يطلب من المستخدم إرسال ملف) -----
    elif data == "ui:upload":
        # نعلم المستخدم أن يرسل الملف الآن
        context.user_data["awaiting_upload"] = True
        await q.edit_message_text("📤 قم الآن بإرسال الملف (مستند - صورة - فيديو). سأقوم برفعه إلى مجلدك في Google Drive.\n\n🔙 لإلغاء اضغط /start")

    # ----- اختيار فيديو للنسخ -----
    elif data == "ui:choosevideo":
        service = get_drive_service()
        videos = list_drive_videos(service, MAIN_FOLDER_ID)
        if not videos:
            await q.edit_message_text("❌ لا توجد فيديوهات جاهزة في المجلد الرئيسي.")
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(v["name"], callback_data=f"copy:{v['id']}")] for v in videos])
        await q.edit_message_text("🎬 اختر الفيديو لنسخه إلى مجلدك:", reply_markup=kb)

    # ----- حول البوت -----
    elif data == "ui:about":
        about_text = (
            "ℹ️ <b>حول CloudDrive Bot</b>\n\n"
            "CloudDrive Bot هو بوت لإدارة الملفات على Google Drive.\n"
            "• إنشاء مجلد خاص لكل مستخدم\n"
            "• رفع الملفات مباشرة من Telegram\n"
            "• نسخ فيديوهات جاهزة إلى مجلدك\n\n"
            "✨ سرعة – بساطة – أمان"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="ui:back")]])
        await q.edit_message_text(about_text, reply_markup=kb, parse_mode="HTML")

    # ----- الدعم -----
    elif data == "ui:support":
        support_text = (
            "🛠 <b>الدعم والمساعدة</b>\n\n"
            "للدعم تواصل عبر:\n"
            "📧 lesquatrefreresazri@gmail.com\n"
            "▶️ قناة: Qurani Studio"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="ui:back")]])
        await q.edit_message_text(support_text, reply_markup=kb, parse_mode="HTML")

    # ----- رجوع للقائمة -----
    elif data == "ui:back":
        await q.edit_message_text("🏠 <b>القائمة الرئيسية</b>\nاختر ما تريد:", reply_markup=main_menu_keyboard(), parse_mode="HTML")

    # ----- نسخ الفيديو إلى مجلد المستخدم (callback data "copy:<id>") handled below by separate handler -----
    else:
        # لا تفعل شيئًا هنا إن كان غير معروف (قد يتم معالجته في handler آخر)
        await q.answer()

# ==========================
# معالجة نسخ الفيديو (callback data startswith copy:)
# ==========================
async def copy_video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if not data.startswith("copy:"):
        return
    video_id = data.split(":", 1)[1]
    service = get_drive_service()
    user = q.from_user
    user_folder_id = get_or_create_user_folder(service, user)
    service.files().copy(fileId=video_id, body={"parents": [user_folder_id]}).execute()
    await q.edit_message_text("✔ تم نسخ الفيديو إلى مجلدك بنجاح!")

# ==========================
# رفع الملفات عبر الرسائل (Document / Photo / Video)
# ==========================
async def upload_file_by_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    service = None
    try:
        service = get_drive_service()
    except Exception as e:
        await update.message.reply_text("❌ خطأ في الوصول إلى Google Drive: " + str(e))
        return

    # يدعم المستندات والملفات المرسلة كـ document، وأيضًا الفيديو/صورة كـ document أو كـ photo
    file_name = None
    local_path = None

    # إذا أرسل المستعمل مستند
    if update.message.document:
        doc = update.message.document
        file_name = doc.file_name or f"file_{user.id}"
        local_path = TEMP_DIR / file_name
        await doc.get_file().download_to_drive(str(local_path))

    # إذا أرسل صورة (نأخذ النسخة الأخيرة الأكبر)
    elif update.message.photo:
        photo = update.message.photo[-1]
        file_name = f"photo_{user.id}.jpg"
        local_path = TEMP_DIR / file_name
        await photo.get_file().download_to_drive(str(local_path))

    # إذا أرسل فيديو كـ video
    elif update.message.video:
        vid = update.message.video
        file_name = vid.file_name or f"video_{user.id}.mp4"
        local_path = TEMP_DIR / file_name
        await vid.get_file().download_to_drive(str(local_path))
    else:
        await update.message.reply_text("❌ لم أجد ملفًا مرفوعًا. أرسل ملفًا (مستند/صورة/فيديو).")
        return

    # تنفيذ الرفع إلى مجلد المستخدم
    try:
        file_id = upload_file_to_user_folder(service, user, local_path)
        # إزالة الملف المحلي
        try:
            local_path.unlink(missing_ok=True)
        except Exception:
            pass
        await update.message.reply_text(f"✔ تم رفع الملف إلى مجلدك!\nاسم الملف: {file_name}\nDrive ID: <code>{file_id}</code>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text("❌ فشل رفع الملف إلى Drive: " + str(e))

    # إزالة حالة الانتظار إن كانت موجودة
    context.user_data.pop("awaiting_upload", None)

# ==========================
# قائمة الفيديوهات العامة (أمر نصي بديل)
# ==========================
async def list_videos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        service = get_drive_service()
    except Exception as e:
        await update.message.reply_text("❌ خطأ في الوصول إلى Google Drive: " + str(e))
        return
    videos = list_drive_videos(service, MAIN_FOLDER_ID)
    if not videos:
        await update.message.reply_text("❌ لا يوجد فيديوهات جاهزة في المجلد الرئيسي.")
        return
    text = "📽 الفيديوهات المتاحة:\n" + "\n".join([f"{i+1}. {v['name']}" for i, v in enumerate(videos)])
    await update.message.reply_text(text)

# ==========================
# تشغيل التطبيق (Webhook)
# ==========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("listvideos", list_videos_command))

    # callback handlers (UI)
    app.add_handler(CallbackQueryHandler(ui_callback_handler, pattern="^ui:"))
    app.add_handler(CallbackQueryHandler(copy_video_handler, pattern="^copy:"))

    # رسالة (رفع ملف) - أي وثيقة / صورة / فيديو يتم رفعه تلقائياً
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, upload_file_by_message))

    # رسالة نصية عادية (تحية)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, greet))

    print("🚀 Bot is running with Webhook...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{TOKEN}"
    )

if __name__ == "__main__":
    main()

