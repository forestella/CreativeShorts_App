import os
from dotenv import load_dotenv

load_dotenv()

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
