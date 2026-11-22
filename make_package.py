r"""배포용 AI.zip 만들기
"""
import re
import zipfile
from pathlib import Path

BASE = Path(__file__).parent
DIST = BASE / "dist"
APP = "assistant8.py"          # 배포할 AI 파일 이름
OUT = BASE / "AI.zip"

# zip 안에 들어갈 파일 (여기 없는 파일은 절대 들어가지 않는다)
FILES = {
    APP: BASE / APP,
    "requirements.txt": DIST / "requirements.txt",
    "setup.bat": DIST / "setup.bat",
    "start.bat": DIST / "start.bat",
    "stop.bat": DIST / "stop.bat",
    "README.txt": DIST / "README.txt",
}

missing = [name for name, path in FILES.items() if not path.exists()]
if missing:
    print("다음 파일이 없어요:", ", ".join(missing))
    raise SystemExit(1)

# 안전 검사: 코드 안에 API 키가 직접 적혀 있으면 중단
KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{30,}|AQ\.[0-9A-Za-z_\-]{20,}")
code = (BASE / APP).read_text(encoding="utf-8", errors="replace")
if KEY_PATTERN.search(code):
    print(f"[중단] {APP} 안에 API 키처럼 보이는 글자가 있어요. 지우고 다시 실행하세요.")
    raise SystemExit(1)

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for name, path in FILES.items():
        z.write(path, arcname=f"AI/{name}")

print(f"만들었어요: {OUT}")
for name in FILES:
    print("  -", name)
