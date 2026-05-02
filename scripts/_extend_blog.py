#!/usr/bin/env python3
"""
블로그 글 본문 확장 헬퍼.
사용: python3 scripts/_extend_blog.py <slug>
입력: stdin으로 새 본문 markdown (frontmatter 제외)
처리: ~/블로그/게시완료/<slug>.md 본문 교체 + blog/<slug>.html 재생성
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import publish_blog as pb  # noqa: E402

HOME = Path.home()
MD_DIR = HOME / "블로그" / "게시완료"


def update(slug: str, new_body: str):
    md_path = MD_DIR / f"{slug}.md"
    if not md_path.exists():
        sys.exit(f"❌ md 없음: {md_path}")
    src = md_path.read_text()
    meta, _old_body = pb.parse_frontmatter(src)
    fm_lines = ["---"]
    for k, v in meta.items():
        fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    new_md = "\n".join(fm_lines) + "\n\n" + new_body.strip() + "\n"
    md_path.write_text(new_md)

    html, _d = pb.render_post(meta, new_body, slug)
    out = pb.BLOG_DIR / f"{slug}.html"
    out.write_text(html)
    print(f"✅ {slug}: md+html 갱신")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("사용: _extend_blog.py <slug> (본문은 stdin)")
    slug = sys.argv[1]
    body = sys.stdin.read()
    if not body.strip():
        sys.exit("❌ stdin 본문 비어있음")
    update(slug, body)
