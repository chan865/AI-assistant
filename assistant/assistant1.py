import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

# api.env를 이 파일과 같은 폴더에서 읽기
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / "api.env")

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

MODEL = "gemini-3.5-flash"
NOTE_FILE = BASE_DIR / "notes.txt"


# ---------- 도구들 ----------
def get_current_time() -> str:
    """현재 날짜와 시간을 알려준다."""
    return datetime.now().strftime("%Y-%m-%d %A %H:%M:%S")


def save_note(content: str) -> str:
    """사용자가 기억해두라고 한 메모나 일정을 파일에 저장한다."""
    with NOTE_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M}] {content}\n")
    return "저장 완료"


def read_notes() -> str:
    """저장된 메모와 일정을 모두 읽어온다."""
    if not NOTE_FILE.exists():
        return "저장된 메모가 없습니다."
    return NOTE_FILE.read_text(encoding="utf-8")


# ---------- AI 설정 ----------
SYSTEM_PROMPT = """
너는 'AI'야.
- 친절하고 간결한 존댓말을 쓴다.
- 시간이나 날짜가 필요하면 get_current_time 도구를 쓴다.
- 기억하라는 요청은 save_note, 저장된 걸 물으면 read_notes를 쓴다.
- 모르는 것은 지어내지 않고 모른다고 말한다.
"""

chat = client.chats.create(
    model=MODEL,
    config=types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[get_current_time, save_note, read_notes],
    ),
)

print("AI입니다. 종료하려면 '종료'를 입력하세요.")
while True:
    user_input = input("나: ").strip()
    if not user_input:
        continue
    if user_input in ("종료", "quit", "exit"):
        print("소지훈: 잘가")
        break
    try:
        response = chat.send_message(user_input)
        print("소지훈:", response.text)
    except Exception as e:
        print("오류가 발생했어요:", e)