import os
from dotenv import load_dotenv

load_dotenv()

class Env:
   TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
   DATABASE_URL = os.getenv("DATABASE_URL")
   ID_USER = os.getenv("ID_USER")
