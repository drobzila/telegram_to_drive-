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

# رابط الدعم (قناتك)
SUPPORT_CHANNEL_URL = "https://www.youtube.com/channel/UCHYJMygtSl60pThu6AUgeOw"

# Scopes
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "openid", "https://www.googleapis.com/auth/userinfo.email"]

# ==========================
# دوال Google Drive العامة
# ==========================
def get_drive_service():
    """
    يستخدم token.json (حساب البوت أو حساب مصرح به) للوصول إلى Drive.
    تأكد أن token.json موجود وفيه صلاحيات DRIVE_SCOPES.
    """
    if not os.path.exists("token.json"):
        raise Exception("⚠️ ملف token.json غير موجود! اتبع خطوات OAuth للحصول على token.json")
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
    """
    ينشئ مجلدًا على Drive لكل مستخدم (مرة واحدة) ويحفظ ID في user_folders.json
    """
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
    media = MediaFileUpload(str(local_path), resumable=True)
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return uploaded["id"]

def list_drive_videos(service, folder_id):
    """
    يعيد قائمة ملفات الفيديو داخل مجلد Drive (id, name, mimeType, appProperties)
    """
    query = f"'{folder_id}' in parents and mimeType contains 'video/' and trashed = false"
    results = service.files().list(q=query, fields="nextPageToken, files(id, name, mimeType, appProperties)").execute()
    return results.get("files", [])

def list_files_in_folder(service, folder_id):
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name, mimeType, appProperties)").execute()
    return results.get("files", [])

# ==========================
# دوال YouTube (OAuth لكل مستخدم + رفع)
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

def run_youtube_oauth_and_save(user_id: int):
    """
    يبدأ OAuth لمستخدم لربط قناته. يتطلب ملف client_secrets_youtube.json.
    سيحفظ التوكن في youtube_tokens/{user_id}_token.json
    ملاحظة: run_local_server يفتح المتصفح على الجهاز الذي يشغل البوت.
    """
    client_file = "client_secrets_youtube.json"
    if not os.path.exists(client_file):
        raise FileNotFoundError("ملف client_secrets_youtube.json غير موجود. حمّله من Google Cloud Console.")
    flow = InstalledAppFlow.from_client_secrets_file(client_file, scopes=YOUTUBE_SCOPES)
    creds = flow.run_local_server(port=0)
    token_path = YOUTUBE_TOKENS_DIR / f"{user_id}_token.json"
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return token_path

def download_drive_file_to_temp(service_drive, file_id, filename):
    """
    ينزل ملف من Drive إلى ملف مؤقت ويرجع مسار الملف المحلي.
    """
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
    """
    يرفع ملف فيديو محليًا إلى قناة المالك للتوكن المقدم.
    يعيد معرف الفيديو على يوتيوب عند النجاح.
    """
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["quran", "قرآن", "recitation"],
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": privacy
        }
    }
    media = MediaFileUpload(local_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
    return response.get("id")

# ==========================
# منطق نصف التلقائي: مزامنة ورفع منتقى
# ==========================
def sync_user_folder(service, user, main_folder_id):
    """
    ينسخ الفيديوهات الموجودة في main_folder_id وغير الموجودة في مجلد المستخدم (بالمقارنة بالاسم).
    يرجع قائمة الملفات التي نُسخت.
    """
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
# واجهة Telegram (أزرار/أوامر)
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
        "/myfolder - إنشاء/عرض مجلدك\n"
        "/listvideos - عرض فيديوهات المجلد الرئيسي\n"
        "/sync - نسخ الفيديوهات الجديدة إلى مجلدك\n"
        "/auth_youtube - ربط حساب YouTube\n"
        "/upload_to_youtube - رفع فيديو من مجلدك إلى قناتك (نصف تلقائي عبر الأزرار)"
    )

async def greet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(random.choice(RESPONSES).format(name=user.first_name or "ضيف"))

# التعامل مع أزرار الواجهة
async def ui_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    # عرض مجلد المستخدم
    if data == "ui:myfiles":
        service = get_drive_service()
        user = q.from_user
        folder_id = get_or_create_user_folder(service, user)
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

    # ينتظر رفع ملف من المستخدم
    elif data == "ui:upload":
        context.user_data["awaiting_upload"] = True
        await q.edit_message_text("📤 قم الآن بإرسال الملف (مستند - صورة - فيديو). سأقوم برفعه إلى مجلدك في Google Drive.\n\n🔙 لإلغاء اضغط /start")

    # عرض فيديوهات مجلد الرئيسي للاختيار والنسخ
    elif data == "ui:choosevideo":
        service = get_drive_service()
        videos = list_drive_videos(service, MAIN_FOLDER_ID)
        if not videos:
            await q.edit_message_text("❌ لا توجد فيديوهات جاهزة في المجلد الرئيسي.")
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(v["name"], callback_data=f"copy:{v['id']}")] for v in videos])
        await q.edit_message_text("🎬 اختر الفيديو لنسخه إلى مجلدك:", reply_markup=kb)

    # عن البوت
    elif data == "ui:about":
        about_text = (
            f"ℹ️ <b>حول CloudDrive Bot</b>\n\n{ABOUT_DESCRIPTION}\n\n"
            "• إنشاء مجلد خاص لكل مستخدم\n"
            "• رفع الملفات مباشرة من Telegram\n"
            "• نسخ فيديوهات جديدة تلقائياً من المجلد الرئيسي\n"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="ui:back")]])
        await q.edit_message_text(about_text, reply_markup=kb, parse_mode="HTML")

    # الدعم الفني مع رابط القناة
    elif data == "ui:support":
        support_text = (
            "🛠 <b>الدعم والمساعدة</b>\n\n"
            f"قناة الدعم: Qurani Studio\n{SUPPORT_CHANNEL_URL}\n\n"
            "📧 للتواصل عبر الإيميل: lesquatrefreresazri@gmail.com"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="ui:back")]])
        await q.edit_message_text(support_text, reply_markup=kb, parse_mode="HTML")

    elif data == "ui:back":
        await q.edit_message_text("🏠 <b>القائمة الرئيسية</b>\nاختر ما تريد:", reply_markup=main_menu_keyboard(), parse_mode="HTML")

    # مزامنة (نسخ فيديوهات جديدة إلى مجلد المستخدم)
    elif data == "ui:sync":
        service = get_drive_service()
        user = q.from_user
        await q.edit_message_text("🔄 جارٍ البحث عن الفيديوهات الجديدة ونسخها إلى مجلدك...")
        copied = sync_user_folder(service, user, MAIN_FOLDER_ID)
        if not copied:
            await q.edit_message_text("✅ لا توجد فيديوهات جديدة للنسخ — مجلدك محدث.")
            return
        success = [c for c in copied if c.get("id")]
        failed = [c for c in copied if not c.get("id")]
        msg = f"✅ تم نسخ {len(success)} فيديو جديد.\n"
        if failed:
            msg += f"⚠️ فشل نسخ {len(failed)} ملفات.\n"
        # بعد النسخ نعرض للمستخدم قائمة الفيديوهات في مجلده التي لم تُرفع بعد لنصف التلقائي
        user_folder_id = get_or_create_user_folder(service, user)
        files = list_drive_videos(service, user_folder_id)
        # نعرض كل فيديو زر: رفع إلى اليوتيوب (upload:<fileid>) إن لم يعلم أنه مرفوع
        kb_buttons = []
        for f in files:
            appp = f.get("appProperties") or {}
            label = f["name"]
            if appp.get("uploaded_to_youtube") == "true":
                label = f"✅ {label}"
                kb_buttons.append([InlineKeyboardButton(label, callback_data="noop")])
            else:
                kb_buttons.append([InlineKeyboardButton(label, callback_data=f"upload:{f['id']}")])
        kb_buttons.append([InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="ui:back")])
        await q.edit_message_text(msg + "\nاختر أي فيديو لرفعه الآن (نصف تلقائي):", reply_markup=InlineKeyboardMarkup(kb_buttons))

    else:
        await q.answer()

# نسخ فيديو مفرد
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

# زر رفع فيديو محدد إلى يوتيوب (نصف تلقائي)
async def upload_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if not data.startswith("upload:"):
        return
    file_id = data.split(":", 1)[1]
    service = get_drive_service()
    user = q.from_user

    # تأكد أن لدى المستخدم توكن لليوتيوب
    creds = get_youtube_credentials_for_user(user.id)
    if not creds:
        await q.edit_message_text("⚠️ لم تقم بربط حساب YouTube بعد. شغّل /auth_youtube ثم حاول مجددًا.")
        return

    # جلب معلومات الملف من Drive
    try:
        fmeta = service.files().get(fileId=file_id, fields="id,name,mimeType,appProperties").execute()
    except Exception as e:
        await q.edit_message_text("❌ خطأ في الحصول على معلومات الملف من Drive: " + str(e))
        return

    if not fmeta.get("mimeType", "").startswith("video/"):
        await q.edit_message_text("⚠️ هذا الملف ليس فيديو.")
        return

    await q.edit_message_text(f"⬇️ جارٍ تنزيل {fmeta['name']} ثم رفعه إلى قناتك على YouTube...")

    # تنزيل مؤقت
    try:
        temp_path = download_drive_file_to_temp(service, file_id, fmeta["name"])
    except Exception as e:
        await q.edit_message_text("❌ فشل تنزيل الملف من Drive: " + str(e))
        return

    # وصف الفيديو الافتراضي (حسب طلبك)
    description = "أفضل صانع وناشر للقرآن الكريم — جودة عالية، سهولة، سرعة. Qurani Studio."

    # رفع الفيديو
    try:
        yt_id = upload_single_file_to_youtube(creds, temp_path, title=fmeta["name"], description=description, privacy="private")
        # تعليم ملف Drive أنه مرفوع (appProperties)
        service.files().update(fileId=file_id, body={"appProperties": {"uploaded_to_youtube": "true"}}).execute()
        await q.edit_message_text(f"✅ تم رفع الفيديو بنجاح إلى قناتك! (YouTube ID: {yt_id})\nرابط: https://youtu.be/{yt_id}")
    except Exception as e:
        await q.edit_message_text("❌ فشل الرفع إلى يوتيوب: " + str(e))
    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass

# رفع الملفات عبر الرسائل (Document / Photo / Video)
async def upload_file_by_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        service = get_drive_service()
    except Exception as e:
        await update.message.reply_text("❌ خطأ في الوصول إلى Google Drive: " + str(e))
        return

    file_name = None
    local_path = None

    if update.message.document:
        doc = update.message.document
        file_name = doc.file_name or f"file_{user.id}"
        local_path = TEMP_DIR / file_name
        await doc.get_file().download_to_drive(str(local_path))

    elif update.message.photo:
        photo = update.message.photo[-1]
        file_name = f"photo_{user.id}.jpg"
        local_path = TEMP_DIR / file_name
        await photo.get_file().download_to_drive(str(local_path))

    elif update.message.video:
        vid = update.message.video
        file_name = vid.file_name or f"video_{user.id}.mp4"
        local_path = TEMP_DIR / file_name
        await vid.get_file().download_to_drive(str(local_path))
    else:
        await update.message.reply_text("❌ لم أجد ملفًا مرفوعًا. أرسل ملفًا (مستند/صورة/فيديو).")
        return

    try:
        file_id = upload_file_to_user_folder(service, user, local_path)
        try:
            local_path.unlink(missing_ok=True)
        except Exception:
            pass
        await update.message.reply_text(f"✔ تم رفع الملف إلى مجلدك!\nاسم الملف: {file_name}\nDrive ID: <code>{file_id}</code>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text("❌ فشل رفع الملف إلى Drive: " + str(e))

    context.user_data.pop("awaiting_upload", None)

# أمر لعرض فيديوهات المجلد الرئيسي
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

# أمر ربط يوتيوب (OAuth للمستخدم)
async def auth_youtube(update, context):
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            "client_secrets_youtube.json",
            scopes=["https://www.googleapis.com/auth/youtube.upload"]
        )

        auth_url, _ = flow.authorization_url(prompt="consent")

        await update.message.reply_text(
            "🔗 افتح الرابط التالي لتسجيل الدخول إلى YouTube:\n\n" + auth_url +
            "\n\nبعد تسجيل الدخول ستظهر لك كود، أرسله لي هنا."
        )

        context.user_data["awaiting_youtube_code"] = flow

    except Exception as e:
        await update.message.reply_text("❌ خطأ OAuth: " + str(e))
        
# ==========================
# تسجيل وبدء البوت
# ==========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("listvideos", list_videos_command))
    app.add_handler(CommandHandler("auth_youtube", auth_youtube))
    app.add_handler(CommandHandler("upload_to_youtube", upload_to_youtube_command))

    app.add_handler(CallbackQueryHandler(ui_callback_handler, pattern="^ui:"))
    app.add_handler(CallbackQueryHandler(copy_video_handler, pattern="^copy:"))
    app.add_handler(CallbackQueryHandler(upload_callback_handler, pattern="^upload:"))
    # noop handler to avoid errors for already-uploaded items
    app.add_handler(CallbackQueryHandler(lambda u, c: u.answer(), pattern="^noop$"))

    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, upload_file_by_message))
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


