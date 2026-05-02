#!/usr/bin/env python3
"""learn/pattern/index.html, learn/guide/index.html 의 '준비중' 카드 제거 + 빈 섹션 정리."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGETS = [
    ROOT / "learn" / "pattern" / "index.html",
    ROOT / "learn" / "guide" / "index.html",
]

# <div class="card coming" ...>...4개의 </div></div> 구조...
COMING_RE = re.compile(
    r'\s*<div class="card coming"[^>]*>\s*'
    r'<div[^>]*>[^<]*</div>\s*'
    r'<div[^>]*>[^<]*</div>\s*'
    r'<div[^>]*>[^<]*</div>\s*'
    r'</div>',
    re.DOTALL,
)

# 빈 grid (준비중 모두 제거 후) — 헤더 함께 정리: <h2>...</h2>\s*<div class="grid">\s*</div>
EMPTY_SECTION_RE = re.compile(
    r'\s*<h2[^>]*>[^<]*</h2>\s*<div class="grid">\s*</div>',
    re.DOTALL,
)

for p in TARGETS:
    src = p.read_text(encoding="utf-8")
    before = src.count('class="card coming"')
    src = COMING_RE.sub("", src)
    src = EMPTY_SECTION_RE.sub("", src)
    p.write_text(src, encoding="utf-8")
    after = src.count('class="card coming"')
    print(f"{p.relative_to(ROOT)}: {before}개 → {after}개")
