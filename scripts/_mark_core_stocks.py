#!/usr/bin/env python3
"""stock/index.html 카드에 data-core="1" 추가 — 핵심 종목만"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "stock" / "index.html"

CORE_SLUGS = {
    "samsung-electronics", "sk-hynix", "samsung-sdi", "alteogen", "ncsoft",
    "ananti", "hanwha-solutions", "oci-holdings", "sdn", "tym",
    "hyundai-motor", "naver", "kakao", "lg-chem", "lg-energy-solution",
}

src = INDEX.read_text(encoding="utf-8")
count = 0


def repl(m):
    global count
    href = m.group(1)
    rest = m.group(2)
    slug = href.rsplit("/", 1)[-1].replace(".html", "")
    if slug in CORE_SLUGS and "data-core" not in rest:
        count += 1
        return f'<a href="{href}" class="card" data-core="1"{rest}'
    return m.group(0)


pattern = re.compile(r'<a href="(/stock/[^"]+\.html)" class="card"((?:\s[^>]*)?)')
new = pattern.sub(repl, src)
INDEX.write_text(new, encoding="utf-8")
print(f"data-core=1 표시: {count}건")
