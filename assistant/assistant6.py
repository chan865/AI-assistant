#python -m streamlit run assistant6.py
import os
import ast
import json
import time
import operator
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types

st.set_page_config(page_title="김인직", page_icon="🗓️")

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / "api.env")

# 한도 초과(429) 시 앞에서부터 차례로 넘어간다
MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]

NOTE_FILE = BASE_DIR / "notes.txt"
HISTORY_FILE = BASE_DIR / "history.json"
MAX_SAVED_TURNS = 40


# ---------- 공용 자원 (화면이 새로고침돼도 유지) ----------
@st.cache_resource
def get_client():
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


@st.cache_resource
def get_alert_queue():
    return []   # 타이머가 울리면 여기에 메시지가 쌓인다


try:
    client = get_client()
except Exception as e:
    st.error(f"API 키를 읽지 못했어요. api.env 파일을 확인해 주세요.\n\n{e}")
    st.stop()

ALERTS = get_alert_queue()

# ---------- 브라우저를 닫으면 자동 종료 ----------
AUTO_SHUTDOWN = True    # 자동 종료를 끄고 싶으면 False
GRACE_SECONDS = 15      # 새로고침(F5)은 봐주는 시간(초)


@st.cache_resource
def start_watchdog():
    from streamlit.runtime import Runtime

    def loop():
        seen = False          # 브라우저가 한 번이라도 연결됐는지
        empty_since = None
        while True:
            time.sleep(2)
            try:
                n = Runtime.instance()._session_mgr.num_active_sessions()
            except Exception:
                print("자동 종료 기능을 쓸 수 없어요. (종료 버튼은 정상 동작해요)")
                return
            if n > 0:
                seen, empty_since = True, None
            elif seen:
                if empty_since is None:
                    empty_since = time.time()
                elif time.time() - empty_since > GRACE_SECONDS:
                    os._exit(0)

    threading.Thread(target=loop, daemon=True).start()
    return True


if AUTO_SHUTDOWN:
    start_watchdog()

# ---------- 파일 도구 설정 ----------
ALLOWED_ROOTS = [
    Path.home().resolve(),      # C:\Users\user
    # Path("D:/").resolve(),  
]
BLOCKED_DIRS = {
    "venv", "__pycache__", ".git", "node_modules",
    "appdata", ".ssh", ".aws", ".gnupg", "$recycle.bin",
    "windows", "program files", "program files (x86)", "programdata",
}
BLOCKED_NAME_KEYWORDS = (
    ".env", "password", "passwd", "비밀번호", "credential",
    "secret", "id_rsa", ".pem", ".key",
)
READABLE_EXT = {".txt", ".md", ".py", ".csv", ".json", ".html", ".css", ".js"}
MAX_RESULTS = 30
SEARCH_TIME_LIMIT = 30   # 초


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
def _is_blocked(path: Path) -> bool:
    if any(part.lower() in BLOCKED_DIRS for part in path.parts):
        return True
    name = path.name.lower()
    return any(k in name for k in BLOCKED_NAME_KEYWORDS)


def _is_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        return False
    in_root = any(resolved.is_relative_to(r) for r in ALLOWED_ROOTS)
    return in_root and not _is_blocked(resolved)


def find_files(keyword: str, extension: str = "") -> str:
    """컴퓨터에서 파일 이름에 keyword가 들어간 파일을 찾아 전체 경로를 알려준다.
    extension은 선택이며 확장자로 좁힐 때 쓴다 (예: '.pdf', '.py')."""
    kw, ext = keyword.lower(), extension.lower()
    results, start, stopped = [], time.time(), ""
    for root_dir in ALLOWED_ROOTS:
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames if d.lower() not in BLOCKED_DIRS]
            for fn in filenames:
                if kw in fn.lower() and (not ext or fn.lower().endswith(ext)):
                    full = Path(dirpath) / fn
                    if not _is_blocked(full):
                        results.append(str(full))
            if len(results) >= MAX_RESULTS:
                stopped = f"\n(결과가 많아 {MAX_RESULTS}개까지만 보여드려요)"
                break
            if time.time() - start > SEARCH_TIME_LIMIT:
                stopped = f"\n({SEARCH_TIME_LIMIT}초 안에 못 끝나서 중간 결과예요)"
                break
        if stopped:
            break
    if not results:
        return f"'{keyword}'가 들어간 파일을 찾지 못했습니다.{stopped}"
    return "\n".join(results[:MAX_RESULTS]) + stopped


def read_file(path: str) -> str:
    """텍스트/코드 파일(.txt .md .py .csv .json 등) 내용을 읽는다.
    path는 find_files가 알려준 전체 경로를 그대로 쓴다."""
    target = Path(path)
    if not _is_allowed(target):
        return "허용되지 않은 위치이거나 보안상 읽을 수 없는 파일입니다."
    target = target.resolve()
    if not target.is_file():
        return "파일을 찾을 수 없습니다."
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
        ALERTS.append(message)   # 화면의 알림 상자가 이 메시지를 가져가 보여줍니다
        try:
            import winsound
            winsound.Beep(1000, 600)
        except Exception:
            pass

    t = threading.Timer(minutes * 60, fire)
    t.daemon = True
    t.start()
    return (f"{minutes}분 뒤에 '{message}'를 알려드리도록 설정했습니다. "
            "(이 프로그램이 켜져 있어야 합니다)")


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
def load_history_items():
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def items_to_contents(items):
    return [
        types.Content(role=m["role"], parts=[types.Part(text=m["text"])])
        for m in items
    ]


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


# ---------- 비서 설정 ----------
def make_system_prompt():
    today = datetime.now().strftime("%Y-%m-%d %A")
    return f"""
너는 사용자의  '김인직'야. 오늘은 {today}이야.
- 유튜버 감스트의 말투를 쓴다.
- 정확한 시각이 필요하면 get_current_time 도구를 쓴다.
- 기억하라는 요청은 save_note, 전체 확인은 read_notes, 특정 주제 검색은 search_notes를 쓴다.
- '내일', '다음 주' 같은 말은 오늘 날짜 기준으로 실제 날짜로 바꿔서 메모에 저장한다.
- 파일 관련 요청은 먼저 find_files로 전체 경로를 찾고, 필요하면 그 경로를 그대로 read_file에 넣어 읽는다.
- 타이머 요청은 set_timer를 쓴다. 계산은 직접 하지 말고 calculate를 쓴다.
- 날씨는 get_weather로 조회한다. 도시를 말하지 않았으면 어느 도시인지 되묻는다.
- 도구가 실패하면 이유를 솔직히 알리고, 모르는 것은 지어내지 않는다.
"""


def make_chat(model_name):
    return client.chats.create(
        model=model_name,
        history=items_to_contents(load_history_items()),
        config=types.GenerateContentConfig(
            system_instruction=make_system_prompt(),
            tools=TOOLS,
        ),
    )


def safe_md(text: str) -> str:
    """화면 출력 때 ~ 와 $ 가 서식으로 바뀌는 것을 막는다."""
    return text.replace("~", "\\~").replace("$", "\\$")


def ask(prompt: str) -> str:
    """질문을 보내고 답을 돌려줍니다. 한도 초과(429)면 다음 모델로 넘어간다."""
    while True:
        try:
            response = st.session_state.chat.send_message(prompt)
            save_history(st.session_state.chat)
            return response.text or "(응답이 비어 있어요. 다시 말씀해 주세요.)"
        except Exception as e:
            idx = st.session_state.model_idx
            if "429" in str(e) and idx < len(MODELS) - 1:
                st.session_state.model_idx = idx + 1
                st.session_state.chat = make_chat(MODELS[idx + 1])
                st.toast(f"사용량 한도 초과 → {MODELS[idx + 1]} 모델로 바꿨어요")
                continue
            return f"⚠️ 오류가 발생했어요: {e}"


# ---------- 화면 상태 초기화 ----------
if "chat" not in st.session_state:
    st.session_state.model_idx = 0
    st.session_state.fired = []
    st.session_state.confirm_delete = False
    st.session_state.messages = load_history_items()
    st.session_state.chat = make_chat(MODELS[0])


# ---------- 타이머 알림 상자 (2초마다 확인) ----------
@st.fragment(run_every=2)
def alert_box():
    while ALERTS:
        msg = ALERTS.pop(0)
        st.session_state.fired.append(msg)
        st.toast(f"⏰ {msg}", icon="⏰")
    for msg in st.session_state.fired:
        st.warning(f"⏰ {msg}")
    if st.session_state.fired and st.button("알림 확인", key="clear_alerts"):
        st.session_state.fired.clear()
        st.rerun(scope="fragment")


# ---------- 사이드바 ----------
with st.sidebar:
    st.header("🗓️ 김인직")
    st.caption(f"사용 중인 모델: {MODELS[st.session_state.model_idx]}")
    alert_box()
    st.divider()

    if st.button("💬 대화 초기화", use_container_width=True):
        HISTORY_FILE.unlink(missing_ok=True)
        st.session_state.messages = []
        st.session_state.chat = make_chat(MODELS[st.session_state.model_idx])
        st.rerun()

    with st.expander("📝 저장된 메모 보기"):
        if NOTE_FILE.exists() and NOTE_FILE.read_text(encoding="utf-8").strip():
            st.text(NOTE_FILE.read_text(encoding="utf-8"))
        else:
            st.write("저장된 메모가 없어요.")

    if not st.session_state.confirm_delete:
        if st.button("🗑️ 메모 전체 삭제", use_container_width=True):
            st.session_state.confirm_delete = True
            st.rerun()
    else:
        st.warning("정말 모든 메모를 지울까요?")
        c1, c2 = st.columns(2)
        if c1.button("지우기", use_container_width=True):
            NOTE_FILE.unlink(missing_ok=True)
            st.session_state.confirm_delete = False
            st.rerun()
        if c2.button("취소", use_container_width=True):
            st.session_state.confirm_delete = False
            st.rerun()

# ---------- 종료 버튼 ----------
with st.sidebar:
    st.divider()
    if st.button("🔴 비서 종료", type="primary", use_container_width=True):
        st.info("비서를 종료했어요. 이 탭은 닫으셔도 돼요.")
        threading.Timer(1.0, lambda: os._exit(0)).start()
        st.stop()
        
# ---------- 메인 채팅 화면 ----------
st.title("김인직")

for m in st.session_state.messages:
    role = "user" if m["role"] == "user" else "assistant"
    with st.chat_message(role):
        st.markdown(safe_md(m["text"]))

if prompt := st.chat_input("김인직에게 말해보세요"):
    st.session_state.messages.append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(safe_md(prompt))
    with st.chat_message("assistant"):
        with st.spinner("생각 중..."):
            reply = ask(prompt)
        st.markdown(safe_md(reply))
    st.session_state.messages.append({"role": "model", "text": reply})