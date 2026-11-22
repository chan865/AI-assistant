import os
import sys
import ast
import re
import json
import time
import queue
import operator
import threading
import traceback
import webbrowser
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime
from pathlib import Path

# 창만 있는 프로그램(.exe)에서는 stdout/stderr가 없을 수 있어서 안전하게 막아준다
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from google import genai
from google.genai import types

APP_NAME = "AI"

# 한도 초과(429) 시 앞에서부터 차례로 넘어간다
MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]

# ---------- 데이터 폴더 (exe로 만들어도 사라지지 않는 위치) ----------
DATA_DIR = Path(os.getenv("HARU_DATA_DIR") or (Path(os.getenv("APPDATA") or Path.home()) / "Haru"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILE = DATA_DIR / "api.env"
NOTE_FILE = DATA_DIR / "notes.txt"
HISTORY_FILE = DATA_DIR / "history.json"
LOG_FILE = DATA_DIR / "error.log"
MAX_SAVED_TURNS = 40

# ---------- 파일 도구 설정 ----------
ALLOWED_ROOTS = [
    Path.home().resolve(),      # C:\Users\사용자이름
    # Path("D:/").resolve(),    # D 드라이브도 허용하려면 앞의 #을 지우기
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

# 작업 스레드(타이머, AI 호출) -> 화면으로 보내는 메시지 통로
EVENTS = queue.Queue()
PENDING_TIMERS = []


# ====================== API 키 ======================
def clean_key(raw: str) -> str:
    key = raw.strip().strip("\"'")
    if key.upper().startswith("GEMINI_API_KEY="):
        key = key.split("=", 1)[1].strip().strip("\"'")
    return key


def load_key() -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                return clean_key(line.split("=", 1)[1])
    return ""


def save_key(key: str):
    ENV_FILE.write_text(f"GEMINI_API_KEY={key}\n", encoding="utf-8")


def key_is_valid(key: str):
    try:
        c = genai.Client(api_key=key)  
        next(iter(c.models.list()))
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def explain_key_error(msg: str) -> str:
    low = msg.lower()
    if any(w in low for w in ("api key", "api_key", "invalid", "401", "403", "400", "permission")):
        return "API 키가 올바르지 않은 것 같아요. 키를 다시 복사해서 붙여넣어 주세요."
    if any(w in low for w in ("connect", "name or service", "timed out", "resolve", "network")):
        return "구글 서버에 연결하지 못했어요. 인터넷(또는 학교 네트워크)을 확인해 주세요."
    return "키를 확인하지 못했어요."


# ====================== 도구들 ======================
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


def set_timer(minutes: float, message: str) -> str:
    """지정한 분(minutes) 뒤에 message 내용으로 알림을 띄운다. 0.1분은 6초다."""
    if minutes <= 0 or minutes > 1440:
        return "0분 초과 1440분(24시간) 이하로만 설정할 수 있습니다."

    def fire():
        EVENTS.put(("alert", message))

    t = threading.Timer(minutes * 60, fire)
    t.daemon = True
    t.start()
    PENDING_TIMERS.append(t)
    return (f"{minutes}분 뒤에 '{message}'를 알려드리도록 설정했습니다. "
            "(이 프로그램이 켜져 있어야 합니다)")


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
    find_files, read_file, set_timer, calculate, get_weather,]


# ====================== 대화 기록 ======================
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


def make_system_prompt():
    today = datetime.now().strftime("%Y-%m-%d %A")
    return f"""
- 너는 'AI'야. 오늘은 {today}이야.
- 친절하고 간결한 존댓말을 쓴다.
- 마크다운 서식(별표, 샵 등)은 쓰지 말고 평범한 글로 답한다.
- 정확한 시각이 필요하면 get_current_time 도구를 쓴다.
- 기억하라는 요청은 save_note, 전체 확인은 read_notes, 특정 주제 검색은 search_notes를 쓴다.
- '내일', '다음 주' 같은 말은 오늘 날짜 기준으로 실제 날짜로 바꿔서 메모에 저장한다.
- 타이머 요청은 set_timer를 쓴다. 계산은 직접 하지 말고 calculate를 쓴다.
- 날씨는 get_weather로 조회한다. 도시를 말하지 않았으면 어느 도시인지 되묻는다.
- 도구가 실패하면 이유를 솔직히 알리고, 모르는 것은 지어내지 않는다.
"""


def clean_md(text: str) -> str:
    """화면에 보기 좋게 마크다운 기호를 정리"""
    out = []
    for line in text.splitlines():
        s = line.rstrip()
        stripped = s.lstrip()
        if stripped.startswith(("* ", "- ")):
            s = s[: len(s) - len(stripped)] + "• " + stripped[2:]
        elif stripped.startswith("#"):
            s = stripped.lstrip("#").lstrip()
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        out.append(s)
    return "\n".join(out)


# ====================== (AI) ======================
class Assistant:
    def __init__(self, key: str):
        self.client = genai.Client(api_key=key)
        self.model_idx = 0
        self.chat = self._make_chat()

    @property
    def model_name(self):
        return MODELS[self.model_idx]

    def _make_chat(self):
        return self.client.chats.create(
            model=MODELS[self.model_idx],
            history=items_to_contents(load_history_items()),
            config=types.GenerateContentConfig(
                system_instruction=make_system_prompt(),
                tools=TOOLS,
            ),
        )

    def reset(self):
        HISTORY_FILE.unlink(missing_ok=True)
        self.chat = self._make_chat()

    def ask(self, text: str) -> str:
        while True:
            try:
                response = self.chat.send_message(text)
                save_history(self.chat)
                return response.text or "(응답이 비어 있어요. 다시 말씀해 주세요.)"
            except Exception as e:
                if "429" in str(e) and self.model_idx < len(MODELS) - 1:
                    self.model_idx += 1
                    self.chat = self._make_chat()
                    EVENTS.put(("model", MODELS[self.model_idx]))
                    continue
                return f"[오류] 문제가 생겼어요: {e}"


# ====================== 화면 ======================
class KeyDialog(tk.Toplevel):
    """API 키 입력 창"""

    def __init__(self, parent, first_time=True):
        super().__init__(parent)
        self.title("처음 설정 - API 키 입력" if first_time else "API 키 변경")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None

        text = ("Gemini API 키가 필요해요.\n\n"
                "1. 아래 버튼으로 Google AI Studio를 열고 로그인\n"
                "2. Get API key → Create API key 클릭\n"
                "3. 만들어진 키를 복사해서 아래에 붙여넣기")
        ttk.Label(self, text=text, justify="left").pack(padx=18, pady=(16, 8), anchor="w")
        ttk.Button(self, text="Google AI Studio 열기",
                   command=lambda: webbrowser.open("https://aistudio.google.com")
                   ).pack(padx=18, anchor="w")

        self.entry = ttk.Entry(self, width=54, show="*")
        self.entry.pack(padx=18, pady=(12, 4))
        ttk.Label(self, text="키는 이 컴퓨터에만 저장되고, 구글에 요청을 보낼 때만 쓰여요.",
                  foreground="#666").pack(padx=18, anchor="w")

        self.status = ttk.Label(self, text="", foreground="#b00020",
                                wraplength=430, justify="left")
        self.status.pack(padx=18, pady=(8, 0), anchor="w")

        row = ttk.Frame(self)
        row.pack(padx=18, pady=14, anchor="e")
        ttk.Button(row, text="저장하고 시작", command=self.submit).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="취소", command=self.cancel).pack(side="left")

        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Return>", lambda e: self.submit())
        self.update_idletasks()
        self.grab_set()
        self.entry.focus_set()

    def submit(self):
        key = clean_key(self.entry.get())
        if len(key) < 20 or any(c.isspace() for c in key):
            self.status.config(text="키가 너무 짧거나 공백이 들어 있어요. 다시 복사해 주세요.")
            return
        self.status.config(text="키를 확인하는 중이에요...", foreground="#444")
        self.config(cursor="watch")
        self.update()
        ok, msg = key_is_valid(key)
        self.config(cursor="")
        if ok:
            self.result = key
            self.destroy()
        else:
            self.status.config(text=explain_key_error(msg) + "\n" + msg[:200],
                               foreground="#b00020")

    def cancel(self):
        self.result = None
        self.destroy()


class HaruApp:
    def __init__(self, root):
        self.root = root
        self.assistant = None
        self.busy = False
        root.title(APP_NAME)
        root.geometry("780x660")
        root.minsize(510, 760)
        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll_id = None
        root.after(150, self.start)
        self._poll_id = root.after(200, self.poll)

    # ---------- 화면 만들기 ----------
    def _build_ui(self):
        font = ("Malgun Gothic", 11)
        bold = ("Malgun Gothic", 10, "bold")

        top = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        for label, cmd in [
            ("대화 초기화", self.on_reset),
            ("메모 보기", self.on_show_notes),
            ("메모 삭제", self.on_delete_notes),
            ("API 키 변경", self.on_change_key),
            ("데이터 폴더", self.on_open_folder),
        ]:
            ttk.Button(top, text=label, command=cmd).pack(side="left", padx=(0, 6))
        self.model_label = ttk.Label(top, text="", foreground="#666")
        self.model_label.pack(side="right")

        self.chat_box = scrolledtext.ScrolledText(
            self.root, wrap="word", font=font, state="disabled",
            padx=12, pady=10, relief="flat", bg="#fafafa", spacing3=2)
        self.chat_box.pack(fill="both", expand=True, padx=8, pady=4)
        self.chat_box.tag_config("h_user", foreground="#0b57d0", font=bold)
        self.chat_box.tag_config("h_bot", foreground="#137333", font=bold)
        self.chat_box.tag_config("h_sys", foreground="#b06000", font=bold)
        self.chat_box.tag_config("b_user", foreground="#1f1f1f")
        self.chat_box.tag_config("b_bot", foreground="#1f1f1f")
        self.chat_box.tag_config("b_sys", foreground="#6b5500")

        bottom = ttk.Frame(self.root, padding=(8, 4, 8, 4))
        bottom.pack(fill="x")
        self.entry = ttk.Entry(bottom, font=font)
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.send_btn = ttk.Button(bottom, text="보내기", command=self.on_send)
        self.send_btn.pack(side="left", padx=(8, 0))
        self.entry.bind("<Return>", lambda e: self.on_send())

        self.status = ttk.Label(self.root, text="", foreground="#666",
                                padding=(12, 0, 12, 8))
        self.status.pack(fill="x")

    def append(self, who, text):
        names = {"user": "나", "bot": "AI", "sys": "알림"}
        box = self.chat_box
        box.config(state="normal")
        box.insert("end", names[who] + "\n", ("h_" + who,))
        body = text if who == "user" else clean_md(text)
        box.insert("end", body + "\n\n", ("b_" + who,))
        box.config(state="disabled")
        box.see("end")

    def clear_chat(self):
        self.chat_box.config(state="normal")
        self.chat_box.delete("1.0", "end")
        self.chat_box.config(state="disabled")

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.entry.config(state=state)
        self.send_btn.config(state=state)
        self.status.config(text="AI가 생각하는 중이에요..." if busy else "")
        if not busy:
            self.entry.focus_set()

    # ---------- 시작 ----------
    def start(self):
        key = load_key()
        if not key:
            key = self.ask_key(first_time=True)
            if not key:
                self.quit_app()
                return
            save_key(key)
        self.init_assistant(key)

    def ask_key(self, first_time):
        dlg = KeyDialog(self.root, first_time)
        self.root.wait_window(dlg)
        return dlg.result

    def init_assistant(self, key):
        try:
            self.assistant = Assistant(key)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"AI를 시작하지 못했어요.\n\n{e}", parent=self.root)
            return
        self.model_label.config(text=f"모델: {self.assistant.model_name}")
        self.show_history()
        self.entry.focus_set()

    def show_history(self):
        self.clear_chat()
        items = load_history_items()
        for m in items:
            self.append("user" if m["role"] == "user" else "bot", m["text"])
        if not items:
            self.append("bot", "안녕하세요, AI예요. 무엇을 도와드릴까요?")

    # ---------- 대화 ----------
    def on_send(self):
        if self.busy or not self.assistant:
            return
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.append("user", text)
        self.set_busy(True)
        threading.Thread(target=self._worker, args=(text,), daemon=True).start()

    def _worker(self, text):
        try:
            reply = self.assistant.ask(text)
        except Exception as e:
            reply = f"[오류] 문제가 생겼어요: {e}"
        EVENTS.put(("reply", reply))

    def poll(self):
        try:
            while True:
                kind, payload = EVENTS.get_nowait()
                if kind == "reply":
                    self.append("bot", payload)
                    self.set_busy(False)
                elif kind == "model":
                    self.model_label.config(text=f"모델: {payload}")
                    self.append("sys", f"사용량 한도를 넘어서 {payload} 모델로 바꿨어요.")
                elif kind == "alert":
                    self.show_alert(payload)
        except queue.Empty:
            pass
        self._poll_id = self.root.after(200, self.poll)

    def show_alert(self, message):
        self.append("sys", f"[타이머] {message}")
        try:
            import winsound
            winsound.Beep(1000, 600)
        except Exception:
            self.root.bell()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(1500, lambda: self.root.attributes("-topmost", False))
        messagebox.showinfo("타이머 알림", message, parent=self.root)

    # ---------- 버튼들 ----------
    def _guard_busy(self):
        if self.busy:
            messagebox.showinfo(APP_NAME, "AI가 답하는 중이에요. 잠시 뒤에 다시 눌러 주세요.",
                                parent=self.root)
            return True
        return False

    def on_reset(self):
        if self._guard_busy() or not self.assistant:
            return
        if messagebox.askyesno("대화 초기화", "지금까지의 대화를 지울까요?\n(메모는 그대로예요)",
                               parent=self.root):
            self.assistant.reset()
            self.show_history()

    def on_show_notes(self):
        win = tk.Toplevel(self.root)
        win.title("저장된 메모")
        win.geometry("560x420")
        box = scrolledtext.ScrolledText(win, wrap="word", font=("Malgun Gothic", 10))
        box.pack(fill="both", expand=True)
        notes = NOTE_FILE.read_text(encoding="utf-8") if NOTE_FILE.exists() else ""
        box.insert("1.0", notes.strip() or "저장된 메모가 없어요.")
        box.config(state="disabled")

    def on_delete_notes(self):
        if messagebox.askyesno("메모 삭제", "저장된 메모를 모두 지울까요?\n되돌릴 수 없어요.",
                               icon="warning", parent=self.root):
            NOTE_FILE.unlink(missing_ok=True)
            self.append("sys", "메모를 모두 지웠어요.")

    def on_change_key(self):
        if self._guard_busy():
            return
        key = self.ask_key(first_time=False)
        if key:
            save_key(key)
            self.init_assistant(key)

    def on_open_folder(self):
        try:
            os.startfile(str(DATA_DIR))
        except AttributeError:
            webbrowser.open(DATA_DIR.as_uri())

    def on_close(self):
        if any(t.is_alive() for t in PENDING_TIMERS):
            if not messagebox.askyesno(
                    "종료", "아직 울리지 않은 타이머가 있어요.\n종료하면 알림이 사라져요. 그래도 종료할까요?",
                    parent=self.root):
                return
        self.quit_app()

    def quit_app(self):
        if self._poll_id:
            try:
                self.root.after_cancel(self._poll_id)
            except Exception:
                pass
        self.root.destroy()


def log_error(text: str):
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}\n")
    except Exception:
        pass


def main():
    try:   # 화면이 흐리게 보이는 것을 막는다 (Windows)
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()

    def on_tk_error(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log_error(text)
        messagebox.showerror(APP_NAME, f"예상하지 못한 문제가 생겼어요.\n자세한 내용은 아래 파일에 있어요.\n{LOG_FILE}")

    root.report_callback_exception = on_tk_error
    HaruApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_error(traceback.format_exc())
        raise