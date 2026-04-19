#!/usr/bin/env bash
# 기존 주식차트/*.html 8개를 manddo-site/stock/ 로 복사 + 상단 내비/하단 면책 주입
set -euo pipefail

SRC="$HOME/주식차트"
DST="$HOME/manddo-site/stock"

# 매핑: 원본 파일명 → 사이트 슬러그
declare -a MAP=(
  "samsung_sdi_analysis.html|samsung-sdi.html"
  "alteogen_analysis.html|alteogen.html"
  "ananti_analysis_v2.html|ananti.html"
  "hanwha_solutions_analysis.html|hanwha-solutions.html"
  "ncsoft_analysis.html|ncsoft.html"
  "oci_holdings_analysis.html|oci-holdings.html"
  "sdn_analysis.html|sdn.html"
  "tym_analysis.html|tym.html"
)

TOPBAR='<div class="manddo-topbar"><a href="/" class="manddo-logo">만또<span>.kr</span></a><a href="/stock/" class="manddo-back">← 스톡 리서치 목록</a></div>'

DISCLAIMER='<div class="manddo-disclaimer"><b>⚠ 투자 유의사항</b><br>본 분석은 공개 정보 기반의 참고용 자료이며, 특정 종목의 매수·매도 권유가 아닙니다. 분석 시점 이후 시장 상황은 변할 수 있으며, 모든 투자 판단과 그 결과는 투자자 본인의 책임입니다.</div>'

FOOTER='<div class="manddo-footer"><a href="/">홈</a>·<a href="/stock/">스톡 리서치</a>·<a href="/about.html">소개</a>·<a href="/privacy.html">개인정보</a>·<a href="/terms.html">약관</a>·<a href="/contact.html">문의</a><br>© 2026 만또 (manddo.kr)</div>'

CSS_LINK='<link rel="stylesheet" href="/css/page-nav.css">'

for entry in "${MAP[@]}"; do
  src_name="${entry%|*}"
  dst_name="${entry#*|}"
  src_path="$SRC/$src_name"
  dst_path="$DST/$dst_name"

  if [ ! -f "$src_path" ]; then
    echo "SKIP: $src_path 없음"
    continue
  fi

  # Python으로 안전하게 HTML 변환
  python3 - "$src_path" "$dst_path" "$TOPBAR" "$DISCLAIMER" "$FOOTER" "$CSS_LINK" <<'PYEOF'
import sys, re
src, dst, topbar, disclaimer, footer, css_link = sys.argv[1:7]

with open(src, 'r', encoding='utf-8') as f:
    html = f.read()

# </head> 앞에 CSS 링크 삽입
if css_link not in html:
    html = html.replace('</head>', css_link + '\n</head>', 1)

# <body> 직후에 topbar 삽입
html = re.sub(r'(<body[^>]*>)', r'\1\n' + topbar, html, count=1)

# </body> 앞에 disclaimer + footer 삽입
html = html.replace('</body>', disclaimer + '\n' + footer + '\n</body>', 1)

with open(dst, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"OK: {dst}")
PYEOF

done

echo "완료"
ls -1 "$DST"
