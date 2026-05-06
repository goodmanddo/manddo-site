#!/usr/bin/env python3
"""
차트 분석 PDF 2종을 Anthropic Files API로 업로드 — 1회 실행.

업로드 후 file_id를 ~/manddo-site/scripts/.chart_files.json 에 저장하면
daily_pick.py 등이 매 호출마다 document 블록으로 첨부 + cache_control 적용한다.

세력의 매집원가 PDF는 100MB로 Files API 한도(32MB) 초과 → 차트분석가이드.md에
이미 핵심 내용이 흡수돼 있어 스킵.
"""

import json
import os
import sys
from pathlib import Path

import anthropic

ROOT = Path.home() / "manddo-site"
KEY_FILE = Path.home() / "stock_auto_trade" / ".anthropic_key"
OUT = ROOT / "scripts" / ".chart_files.json"

PDFS = [
    Path("/Users/mandoo/Desktop/(PDF)차트분석바이블특별부록-기술적모식도모음집-한스미디어-20240911.pdf"),
    Path("/Users/mandoo/Desktop/1. 기술적 차트 분석.pdf"),
]


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY") or (
        KEY_FILE.read_text().strip() if KEY_FILE.exists() else ""
    )
    if not api_key:
        print("ANTHROPIC_API_KEY 미설정"); sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # 기존 매니페스트 로드 (idempotent: 이미 업로드한 파일은 스킵)
    manifest = {}
    if OUT.exists():
        manifest = json.loads(OUT.read_text())

    for pdf in PDFS:
        if not pdf.exists():
            print(f"[skip] 없음: {pdf.name}")
            continue
        key = pdf.name
        if key in manifest:
            print(f"[skip] 이미 업로드됨: {key} → {manifest[key]['id']}")
            continue
        size_mb = pdf.stat().st_size / 1024 / 1024
        print(f"[upload] {key} ({size_mb:.1f}MB) ...")
        with pdf.open("rb") as f:
            uploaded = client.beta.files.upload(
                file=(pdf.name, f, "application/pdf"),
            )
        manifest[key] = {
            "id": uploaded.id,
            "filename": uploaded.filename,
            "size_bytes": uploaded.size_bytes,
            "created_at": str(uploaded.created_at),
        }
        print(f"  ✓ {uploaded.id}")

    OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"\n저장: {OUT}")
    for k, v in manifest.items():
        print(f"  - {k}: {v['id']}")


if __name__ == "__main__":
    main()
