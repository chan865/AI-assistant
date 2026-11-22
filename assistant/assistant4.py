import os
import ast
import json
import operator
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / "api.env")

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# 한도 초과(429) 시 앞에서부터 차례로 넘어간다
MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]
current = 0

NOTE_FILE = BASE_DIR / "notes.txt"
HISTORY_FILE = BASE_DIR / "history.json"
MAX_SAVED_TURNS = 40

# 파일 도구가 접근할 수 있는 폴더 (이 폴더 밖은 못 본다)
# SEARCH_ROOT = BASE_DIR.resolve()

#파일 도구가 접근할 수 있는 폴더 (이 폴더 밖도 확인 가능)
SEARCH_ROOT = BASE_DIR.parent.resolve()
SKIP_DIRS = {"venv", "__pycache__", ".git", "node_modules"}
READABLE_EXT = {".txt", ".md", ".py", ".csv", ".json", ".html", ".css", ".js"}


# ---------- 기본 도구 ----------
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


# ---------- 파일 도구 ----------
def find_files(keyword: str) -> str:
    """작업 폴더 안에서 파일 이름에 특정 단어가 들어간 파일을 찾는다."""
    kw = keyword.lower()
    results = []
    for path in SEARCH_ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and kw in path.name.lower():
            results.append(str(path.relative_to(SEARCH_ROOT)))
        if len(results) >= 20:
            break
    if not results:
        return f"'{keyword}'가 들어간 파일이 없습니다."
    return "\n".join(results)


def read_file(relative_path: str) -> str:
    """작업 폴더 안의 텍스트/코드 파일(.txt .md .py .csv .json 등) 내용을 읽는다.
    relative_path는 find_files가 알려준 상대 경로를 쓴다."""
    target = (SEARCH_ROOT / relative_path).resolve()
    if not target.is_relative_to(SEARCH_ROOT):
        return "작업 폴더 밖의 파일은 읽을 수 없습니다."
    if not target.is_file():
        return "파일을 찾을 수 없습니다."
    if target.name.lower() in ("api.env", ".env"):
        return "보안상 키 파일은 읽을 수 없습니다."
    if target.suffix.lower() not in READABLE_EXT:
        return f"{target.suffix} 형식은 읽을 수 없습니다. (한글/워드/PDF 등은 지원하지 않아요)"
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > 5000:
        return text[:5000] + "\n...(5000자까지만 읽었습니다)"
    return text


# ---------- 타이머 ----------
def set_timer(minutes: float, message: str) -> str:
    """지정한 분(minutes) 뒤에 message 내용으로 알림을 띄운다. 0.1분은 6초다."""
    if minutes <= 0 or minutes > 1440:
        return "0분 초과 1440분(24시간) 이하로만 설정할 수 있습니다."

    def fire():
        print(f"\n⏰ [알림] {message}\n나: ", end="", flush=True)
        try:
            import winsound
            winsound.Beep(1000, 600)
        except Exception:
            print("\a", end="", flush=True)

    t = threading.Timer(minutes * 60, fire)
    t.daemon = True   # 프로그램을 끄면 타이머도 사라진다
    t.start()
    return f"{minutes}분 뒤에 '{message}'를 알려드리도록 설정했습니다. (프로그램이 켜져 있어야 합니다)"


# ---------- 계산기 ----------
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("지수가 너무 큽니다")
        return _OPS[type(node.op)](left, right)
    raise ValueError("지원하지 않는 식입니다")


def calculate(expression: str) -> str:
    """수식을 정확히 계산한다. 예: '1234*5678', '(3+4)**2', '100/7'.
    사칙연산, 나머지(%), 거듭제곱(**)만 지원한다."""
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_eval(tree.body))
    except Exception as e:
        return f"계산할 수 없습니다: {e}"


# ---------- 날씨 ----------
WEATHER_CODES = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "안개", 51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 71: "약한 눈", 73: "눈", 75: "강한 눈",
    80: "소나기", 81: "소나기", 82: "강한 소나기", 95: "천둥번개",
}


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def get_weather(city: str) -> str:
    """도시 이름(예: 인천, 서울, 평택)을 받아 현재 날씨와 오늘/내일 예보를 알려준다."""
    try:
        q = urllib.parse.quote(city)
        geo = _get_json(
            f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language=ko"
        )
        if not geo.get("results"):
            return f"'{city}'를 찾을 수 없습니다."
        g = geo["results"][0]
        w = _get_json(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={g['latitude']}&longitude={g['longitude']}"
            "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            "&timezone=auto&forecast_days=2"
        )
        c, d = w["current"], w["daily"]
        desc = WEATHER_CODES.get(c["weather_code"], "알 수 없음")
        return (
            f"{g['name']} 현재: {desc}, {c['temperature_2m']}°C "
            f"(체감 {c['apparent_temperature']}°C), 바람 {c['wind_speed_10m']}km/h\n"
            f"오늘: {d['temperature_2m_min'][0]}~{d['temperature_2m_max'][0]}°C, "
            f"강수확률 {d['precipitation_probability_max'][0]}%\n"
            f"내일: {d['temperature_2m_min'][1]}~{d['temperature_2m_max'][1]}°C, "
            f"강수확률 {d['precipitation_probability_max'][1]}%"
        )
    except Exception as e:
        return f"날씨를 가져오지 못했습니다: {e}"


TOOLS = [
    get_current_time, save_note, read_notes, search_notes,
    find_files, read_file, set_timer, calculate, get_weather,
]


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
        return []


def save_history(chat):
    items = []
    for content in chat.get_history():
        text = "".join(
            p.text for p in (content.parts or []) if p.text and not p.thought
        )
        if text.strip():
            items.append({"role": content.role, "text": text})
    items = items[-MAX_SAVED_TURNS:]
    while items and items[0]["role"] != "user":
        items.pop(0)
    HISTORY_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------- AI 설정 ----------
def make_system_prompt():
    today = datetime.now().strftime("%Y-%m-%d %A")
    return f"""
너는 'AI'야. 오늘은 {today}이야.
- 친절하고 간결한 존댓말을 쓴다.
- 정확한 시각이 필요하면 get_current_time 도구를 쓴다.
- 기억하라는 요청은 save_note, 전체 확인은 read_notes, 특정 주제 검색은 search_notes를 쓴다.
- '내일', '다음 주' 같은 말은 오늘 날짜 기준으로 실제 날짜로 바꿔서 메모에 저장한다.
- 파일 관련 요청은 먼저 find_files로 위치를 찾고, 필요하면 read_file로 내용을 읽는다.
- 타이머 요청은 set_timer를 쓴다. 계산은 직접 하지 말고 calculate를 쓴다.
- 날씨는 get_weather로 조회한다. 도시를 말하지 않았으면 어느 도시인지 되묻는다.
- 도구가 실패하면 이유를 솔직히 알리고, 모르는 것은 지어내지 않는다.
"""


def make_chat(model_name):
    return client.chats.create(
        model=model_name,
        history=load_history(),
        config=types.GenerateContentConfig(
            system_instruction=make_system_prompt(),
            tools=TOOLS,
        ),
    )


chat = make_chat(MODELS[current])

print("AI입니다.")
print("(종료: '종료' / 대화 초기화: '기록삭제' / 메모 전체 삭제: '메모삭제')")
while True:
    user_input = input("나: ").strip()
    if not user_input:
        continue

    if user_input in ("종료", "quit", "exit"):
        print("AI: 좋은 AI 보내세요!")
        break

    if user_input == "기록삭제":
        HISTORY_FILE.unlink(missing_ok=True)
        chat = make_chat(MODELS[current])
        print("AI: 대화 기록을 지우고 새로 시작했어요. (메모는 그대로예요)")
        continue

    if user_input == "메모삭제":
        confirm = input("정말 notes.txt의 모든 메모를 지울까요? (y/n): ").strip().lower()
        if confirm == "y":
            NOTE_FILE.unlink(missing_ok=True)
            print("AI: 메모를 모두 지웠어요.")
        else:
            print("AI: 취소했어요.")
        continue

    while True:
        try:
            response = chat.send_message(user_input)
            print("AI:", response.text)
            save_history(chat)
            break
        except Exception as e:
            if "429" in str(e) and current < len(MODELS) - 1:
                current += 1
                print(f"(사용량 한도 초과 → {MODELS[current]} 모델로 바꿔서 다시 시도해요)")
                chat = make_chat(MODELS[current])
                continue
            print("오류가 발생했어요:", e)
            break