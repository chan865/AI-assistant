import os
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / "api.env")

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

MODEL = "gemini-3.5-flash"
NOTE_FILE = BASE_DIR / "notes.txt"
HISTORY_FILE = BASE_DIR / "history.json"
MAX_SAVED_TURNS = 40   # 저장해둘 최근 대화 개수


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


def search_notes(keyword: str) -> str:
    """저장된 메모 중 특정 단어가 들어간 줄만 찾아서 알려준다."""
    if not NOTE_FILE.exists():
        return "저장된 메모가 없습니다."
    lines = NOTE_FILE.read_text(encoding="utf-8").splitlines()
    found = [line for line in lines if keyword in line]
    return "\n".join(found) if found else f"'{keyword}'가 들어간 메모가 없습니다."


# ---------- 대화 기록 저장/불러오기 ----------
def load_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return [
            types.Content(role=m["role"], parts=[types.Part(text=m["text"])])
            for m in data
        ]
    except Exception:
        return []   # 파일이 깨졌으면 새로 시작


def save_history(chat):
    items = []
    for content in chat.get_history():
        text = "".join(
            p.text for p in (content.parts or []) if p.text and not p.thought
        )
        if text.strip():
            items.append({"role": content.role, "text": text})
    items = items[-MAX_SAVED_TURNS:]
    while items and items[0]["role"] != "user":   # 항상 사용자 발언부터 시작
        items.pop(0)
    HISTORY_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------- AI 설정 ----------
def make_system_prompt():
    today = datetime.now().strftime("%Y-%m-%d %A")
    return f"""
너는 AI야. 오늘은 {today}이야.
- 친절하고 간결한 존댓말을 쓴다.
- 정확한 시각이 필요하면 get_current_time 도구를 쓴다.
- 기억하라는 요청은 save_note, 전체 확인은 read_notes,
  특정 주제를 찾을 땐 search_notes를 쓴다.
- '내일', '다음 주' 같은 말은 오늘 날짜 기준으로 실제 날짜로 바꿔서 메모에 저장한다.
- 모르는 것은 지어내지 않고 모른다고 말한다.
"""


chat = client.chats.create(
    model=MODEL,
    history=load_history(),
    config=types.GenerateContentConfig(
        system_instruction=make_system_prompt(),
        tools=[get_current_time, save_note, read_notes, search_notes],
    ),
)

print("AI입니다. (종료: '종료' / 대화기록 지우기: '기록삭제')")
while True:
    user_input = input("나: ").strip()
    if not user_input:
        continue
    if user_input in ("종료", "quit", "exit"):
        print("AI: 좋은 하루 보내세요!")
        break
    if user_input == "기록삭제":
        HISTORY_FILE.unlink(missing_ok=True)
        print("AI: 대화 기록을 지웠어요. 프로그램을 다시 실행하면 새로 시작해요.")
        continue
    try:
        response = chat.send_message(user_input)
        print("AI:", response.text)
        save_history(chat)
    except Exception as e:
        print("오류가 발생했어요:", e)