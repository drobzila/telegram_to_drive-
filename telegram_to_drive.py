import os
from telethon import TelegramClient
from moviepy.editor import VideoFileClip
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive

# إعدادات Telegram
api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")
session_file = "telegram.session"
channel_username = os.getenv("TELEGRAM_CHANNEL")

# إعدادات Google Drive
gdrive_folder_id = os.getenv("GOOGLE_FOLDER")  
log_file = "uploaded_log.txt"

# بيانات OAuth
CLIENT_ID = "553805965519-1gvas0tmcl86v76k7m9bhkmc7m76657s.apps.googleusercontent.com"
CLIENT_SECRET = "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"
REFRESH_TOKEN = "1//09SLS4A1oZYsJCgYIARAAGAkSNwF-L9IrQJneNmOVOAjihJWVMGFL2gYlLAdg0Y_0SZg4bQPjbRR-qkDKYvbSS4weE7zrPh8w4_E"

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_ID = "1oGaDAOI-_QYIUb_om8HDq-JkMuuiTIj_"

# تحميل سجل الملفات السابقة
if os.path.exists(log_file):
    with open(log_file, "r") as f:
        uploaded_files = set(f.read().splitlines())
else:
    uploaded_files = set()

# رفع الفيديو
def upload_to_drive(file_path, file_name):
    gfile = drive.CreateFile({"parents": [{"id": gdrive_folder_id}], "title": file_name})
    gfile.SetContentFile(file_path)
    gfile.Upload()
    print(f"Uploaded: {file_name}")
    uploaded_files.add(file_name)
    with open(log_file, "a") as f:
        f.write(file_name + "\n")

# تشغيل Telegram Client
client = TelegramClient(session_file, api_id, api_hash)

async def main():
    await client.start()
    channel = await client.get_entity(channel_username)

    count = 0
    async for message in client.iter_messages(channel):
        if message.video:
            file_name = message.file.name
            if file_name in uploaded_files:
                continue

            temp_path = await message.download_media(file="temp_video.mp4")
            clip = VideoFileClip(temp_path)
            if clip.duration < 60:  # أقل من دقيقة
                upload_to_drive(temp_path, file_name)
                count += 1
            clip.close()
            os.remove(temp_path)

            if count >= 20:  # توقف بعد 20 فيديو
                break

with client:
    client.loop.run_until_complete(main())
