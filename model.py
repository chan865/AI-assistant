import os
from dotenv import load_dotenv
from google import genai

load_dotenv("api.env")
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# 사용 가능한 모델 목록 출력
for m in client.models.list():
    print(m.name)
