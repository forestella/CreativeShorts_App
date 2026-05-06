import os
import sys
from dotenv import load_dotenv

# PyInstaller 지원을 위한 APP_ROOT 설정
if getattr(sys, 'frozen', False):
    APP_ROOT = sys._MEIPASS
else:
    APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# .env 파일의 절대 경로 지정 로드
dotenv_path = os.path.join(APP_ROOT, ".env")
load_dotenv(dotenv_path)

# Google Gemini API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# OpenAI API Key (Optional)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Pexels API Key (Optional for stock photos)
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

# Model Settings
GEMINI_MODEL_PRO = "gemini-2.5-pro"
GEMINI_MODEL_FLASH_LITE = "gemini-2.5-flash-lite"
GEMINI_MODEL = GEMINI_MODEL_PRO  # 기본 권장 모델 설정
