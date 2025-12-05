import os
from pathlib import Path
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# ==========================
# الإعدادات
# ==========================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", "8443"))

if not TOKEN or not WEBHOOK_URL:
    raise Exception("⚠️ يجب تعيين TELEGRAM_TOKEN وWEBHOOK_URL")

# مسار ملفات مؤقتة (مطلوب للبوت حتى لو لم نستخدمه الآن)
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)


# ==========================
# رسالة الترحيب الرئيسية
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "ضيف"

    welcome_text = (
        f"🎉 مرحبًا بك يا <b>{name}</b>!\n\n"
        "🚀 <b>CloudDrive Bot</b>\n"
        "نظام إدارة ملفات احترافي يعتمد على الذكاء الاصطناعي.\n\n"
        "✨ <b>مميزات البوت:</b>\n"
        "• تنظيم احترافي للملفات\n"
        "• إنشاء مجلد خاص لكل مستخدم\n"
        "• سرعة – دقة – أمان\n\n"
        "👇 اختر من القائمة للبدء:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("ℹ️ حول البوت", callback_data="about")],
        [InlineKeyboardButton("🛠 الدعم", callback_data="support")],
    ])

    await update.message.reply_html(welcome_text, reply_markup=keyboard)


# ==========================
# معالجة الأزرار
# ==========================
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # زر حول البوت
    if q.data == "about":
        await q.edit_message_text(
            "ℹ️ <b>حول البوت</b>\n\n"
            "CloudDrive Bot هو نظام متطور يعتمد على الذكاء الاصطناعي.\n"
            "صُمّم لتسهيل رفع الملفات، تنظيمها، وإدارتها باحترافية.\n\n"
            "🎨 أفضل منشئ للفيديوهات القرآنية\n"
            "⚡ سرعة – بساطة – احترافية\n"
            "💾 يدعم الملفات، الصور، الفيديوهات، والمستندات.\n\n"
            "👇 اختر ما تريد:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ]),
            parse_mode="HTML"
        )

    # زر الدعم
    elif q.data == "support":
        await q.edit_message_text(
            "📩 <b>الدعم والمساعدة</b>\n\n"
            "إذا واجهت أي مشكلة أو لديك اقتراح لتحسين البوت، تواصل معنا:\n\n"
            "📧 <b>البريد الإلكتروني:</b>\n"
            "lesquatrefreresazri@gmail.com\n\n"
            "▶️ <b>قناتنا على اليوتيوب:</b>\n"
            "Qurani Studio – دروس، تحديثات، وفيديوهات جاهزة\n"
            "https://www.youtube.com/channel/UCHYJMygtSl60pThu6AUgeOw\n\n"
            "🤝 فريق Qurani Studio دائمًا في خدمتك.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ]),
            parse_mode="HTML"
        )

    # زر الرجوع
    elif q.data == "back":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("ℹ️ حول البوت", callback_data="about")],
            [InlineKeyboardButton("🛠 الدعم", callback_data="support")],
        ])

        await q.edit_message_text(
            "🏠 <b>القائمة الرئيسية</b>\n\n"
            "اختر من الأسفل:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )


# ==========================
# تشغيل البوت باستخدام Webhook
# ==========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_buttons))

    print("🚀 Bot is running with Webhook...")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{TOKEN}"
    )


if __name__ == "__main__":
    main()
