#!/usr/bin/env python3
"""สร้าง docs/user-manual-th.html จาก user-manual-th.md — จัดหน้าแล้ว เปิดอ่าน/พิมพ์ PDF ได้ทันที"""
from __future__ import annotations

import base64
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
MD_PATH = DOCS / "user-manual-th.md"
IMAGES_SRC = DOCS / "images"
OUT_PATHS = (
    DOCS / "user-manual-th.html",
    DOCS.parent / "frontend" / "user-manual-th.html",
)
FRONTEND_IMAGES = DOCS.parent / "frontend" / "manual-images"
PDF_PATH = DOCS / "user-manual-th.pdf"

IMG_SRC_RE = re.compile(r'src="((?:images|manual-images)/[^"]+)"')


def embed_local_images(html_text: str) -> str:
    """ฝัง PNG ใน HTML — PDF/พิมพ์โหลดรูปได้แน่นอน ไม่พึ่ง path แบบ relative"""

    def repl(m: re.Match[str]) -> str:
        src = m.group(1)
        path = IMAGES_SRC / Path(src).name
        if not path.is_file():
            return m.group(0)
        b64 = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        return f'src="data:image/png;base64,{b64}"'

    return IMG_SRC_RE.sub(repl, html_text)


IMG_RE = re.compile(r"^!\[(.*?)\]\((.+?)\)$")
INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)")


def render_image(alt: str, src: str, lead: bool = False) -> str:
    alt_esc = html.escape(alt)
    src_esc = html.escape(src)
    cap = f"<figcaption>{alt_esc}</figcaption>" if alt else ""
    cls = "shot shot--lead" if lead else "shot"
    return f'<figure class="{cls}"><img src="{src_esc}" alt="{alt_esc}">{cap}</figure>'


def inline_md(text: str) -> str:
    out: list[str] = []
    pos = 0
    for m in INLINE_RE.finditer(text):
        out.append(html.escape(text[pos : m.start()]))
        if m.group(1):
            out.append(f"<strong>{html.escape(m.group(1))}</strong>")
        elif m.group(2):
            out.append(f"<em>{html.escape(m.group(2))}</em>")
        elif m.group(3):
            out.append(f"<code>{html.escape(m.group(3))}</code>")
        elif m.group(4):
            out.append(
                f'<a href="{html.escape(m.group(5))}" target="_blank" rel="noopener">'
                f"{html.escape(m.group(4))}</a>"
            )
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def slug_heading(text: str) -> str:
    s = re.sub(r"<[^>]+>", "", text)
    s = re.sub(r"[^\w\s\-ก-๙]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s.strip()).lower()
    return s or "section"


def parse_table(lines: list[str], start: int) -> tuple[str, int]:
    rows: list[list[str]] = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        rows.append(row)
        i += 1
    if len(rows) < 2:
        return "", start
    header, sep, body = rows[0], rows[1], rows[2:]
    if not all(re.match(r"^:?-+:?$", c.replace(" ", "")) for c in sep):
        return "", start
    parts = ['<div class="table-wrap"><table>']
    parts.append("<thead><tr>" + "".join(f"<th>{inline_md(c)}</th>" for c in header) + "</tr></thead>")
    parts.append("<tbody>")
    for row in body:
        cells = row + [""] * (len(header) - len(row))
        parts.append("<tr>" + "".join(f"<td>{inline_md(c)}</td>" for c in cells[: len(header)]) + "</tr>")
    parts.append("</tbody></table></div>")
    return "\n".join(parts), i


def md_to_body(md: str) -> tuple[str, list[tuple[str, str]]]:
    lines = md.splitlines()
    out: list[str] = []
    toc: list[tuple[str, str]] = []
    i = 0
    in_code = False
    code_buf: list[str] = []
    list_buf: list[str] = []
    list_type: str | None = None
    chapter_open = False
    last_section_num: int | None = None

    def close_chapter() -> None:
        nonlocal chapter_open
        if chapter_open:
            out.append("</section>")
            chapter_open = False

    def open_chapter() -> None:
        nonlocal chapter_open
        close_chapter()
        out.append('<section class="chapter">')
        chapter_open = True

    def flush_list() -> None:
        nonlocal list_buf, list_type
        if not list_buf:
            return
        tag = list_type or "ul"
        out.append(f"<{tag}>")
        for item in list_buf:
            out.append(f"<li>{inline_md(item)}</li>")
        out.append(f"</{tag}>")
        list_buf = []
        list_type = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if in_code:
            if stripped.startswith("```"):
                out.append(f"<pre><code>{html.escape(chr(10).join(code_buf))}</code></pre>")
                code_buf = []
                in_code = False
            else:
                code_buf.append(line)
            i += 1
            continue

        if stripped.startswith("```"):
            flush_list()
            in_code = True
            i += 1
            continue

        if not stripped:
            flush_list()
            i += 1
            continue

        if stripped == "---":
            flush_list()
            close_chapter()
            i += 1
            continue

        if stripped.startswith("|"):
            flush_list()
            tbl, i = parse_table(lines, i)
            if tbl:
                if (
                    out
                    and out[-1].startswith("<p><strong>")
                    and out[-1].endswith("</strong></p>")
                ):
                    title_p = out.pop()
                    out.append(f'<div class="keep-together">{title_p}')
                    out.append(tbl)
                    out.append("</div>")
                else:
                    out.append(tbl)
                continue

        img_m = IMG_RE.match(stripped)
        if img_m:
            flush_list()
            lead = bool(out) and out[-1].startswith("<h4")
            if not lead and out:
                last = out[-1]
                if last.startswith("<h3") or last.startswith("<p>") or last.startswith("</div>"):
                    lead = True
            out.append(render_image(img_m.group(1).strip(), img_m.group(2).strip(), lead=lead))
            i += 1
            continue

        m = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if m:
            flush_list()
            level = len(m.group(1))
            title = m.group(2).strip()
            sid = slug_heading(title)
            continue_in_chapter = False
            if level == 2 and re.match(r"^\d+\.", title):
                num = int(re.match(r"^(\d+)\.", title).group(1))
                toc.append((sid, re.sub(r"^\d+\.\s*", "", title)))
                continue_in_chapter = num == 5 and last_section_num == 4 and chapter_open
                if not continue_in_chapter:
                    open_chapter()
                last_section_num = num
            if continue_in_chapter:
                out.append(
                    f'<h4 id="{sid}" class="chapter-section-head">{inline_md(title)}</h4>'
                )
            else:
                tag = f"h{min(level + 1, 4)}"
                out.append(f'<{tag} id="{sid}">{inline_md(title)}</{tag}>')
            i += 1
            continue

        if stripped.startswith("> "):
            flush_list()
            out.append(f'<blockquote class="note">{inline_md(stripped[2:])}</blockquote>')
            i += 1
            continue

        om = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if om:
            if list_type not in (None, "ol"):
                flush_list()
                list_type = "ol"
            list_buf.append(om.group(2))
            i += 1
            continue

        if stripped.startswith("- "):
            if list_type not in (None, "ul"):
                flush_list()
                list_type = "ul"
            list_buf.append(stripped[2:])
            i += 1
            continue

        if stripped.startswith("**ถาม:"):
            flush_list()
            q = stripped
            a = ""
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("ตอบ:"):
                a = lines[i + 1].strip()[4:].strip()
                i += 1
            out.append(
                f'<div class="faq"><p class="faq-q">{inline_md(q)}</p>'
                f'<p class="faq-a">{inline_md(a)}</p></div>'
            )
            i += 1
            continue

        if stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**"):
            flush_list()
            out.append(f'<p class="footer-note">{inline_md(stripped.strip("*"))}</p>')
            i += 1
            continue

        flush_list()
        out.append(f"<p>{inline_md(stripped)}</p>")
        i += 1

    flush_list()
    if in_code and code_buf:
        out.append(f"<pre><code>{html.escape(chr(10).join(code_buf))}</code></pre>")
    close_chapter()
    return "\n".join(out), toc


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap');
:root {
  --bg: #f8fafc;
  --paper: #ffffff;
  --text: #0f172a;
  --muted: #475569;
  --accent: #4f46e5;
  --accent-bg: #eef2ff;
  --border: #e2e8f0;
  --warn-bg: #fffbeb;
  --warn-brd: #fcd34d;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: 'Sarabun', 'Segoe UI', sans-serif;
  font-size: 15px;
  line-height: 1.75;
  color: var(--text);
  background: var(--bg);
}
.layout {
  display: grid;
  grid-template-columns: 260px minmax(0, 820px);
  gap: 28px;
  max-width: 1180px;
  margin: 0 auto;
  padding: 24px 20px 48px;
}
nav.toc {
  position: sticky;
  top: 16px;
  align-self: start;
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 14px;
  box-shadow: 0 4px 20px rgba(15,23,42,.06);
}
nav.toc h2 {
  margin: 0 0 10px;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: .04em;
  color: var(--muted);
}
nav.toc ol {
  margin: 0;
  padding-left: 18px;
}
nav.toc a {
  color: var(--accent);
  text-decoration: none;
  font-size: 13px;
  line-height: 1.5;
}
nav.toc a:hover { text-decoration: underline; }
article {
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 32px 36px 40px;
  box-shadow: 0 8px 30px rgba(15,23,42,.07);
}
.hero {
  margin-bottom: 28px;
  padding-bottom: 20px;
  border-bottom: 2px solid var(--accent-bg);
}
.hero h1 {
  margin: 0 0 8px;
  font-size: 28px;
  line-height: 1.25;
  color: var(--accent);
}
.hero .lead { margin: 0; color: var(--muted); font-size: 16px; }
figure.shot {
  margin: 16px 0 22px;
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  background: #f8fafc;
  box-shadow: 0 6px 24px rgba(15,23,42,.08);
}
figure.shot--lead {
  margin-top: 8px;
}
figure.shot img {
  display: block;
  width: 100%;
  height: auto;
}
figure.shot figcaption {
  padding: 10px 14px;
  font-size: 12px;
  color: var(--muted);
  border-top: 1px solid var(--border);
  background: #fffbeb;
  line-height: 1.45;
}
.chapter {
  margin-bottom: 28px;
  padding-bottom: 8px;
}
.chapter > h3 {
  margin: 0 0 16px;
  padding: 12px 16px;
  font-size: 19px;
  line-height: 1.35;
  color: #312e81;
  background: linear-gradient(180deg, #eef2ff 0%, #f8fafc 100%);
  border: 1px solid #c7d2fe;
  border-radius: 10px;
}
.chapter > h3 + p,
.chapter > h3 + ol {
  margin-top: 0;
}
.chapter-section-head {
  margin: 22px 0 14px;
  padding: 12px 16px;
  font-size: 19px;
  line-height: 1.35;
  font-weight: 700;
  color: #312e81;
  background: linear-gradient(180deg, #eef2ff 0%, #f8fafc 100%);
  border: 1px solid #c7d2fe;
  border-radius: 10px;
}
.chapter-section-head + p,
.chapter-section-head + figure {
  margin-top: 0;
}
h4 {
  margin: 18px 0 8px;
  font-size: 15px;
  font-weight: 700;
  color: #334155;
}
.print-bar {
  grid-column: 1 / -1;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  background: #1e1b4b;
  color: #e0e7ff;
  padding: 12px 16px;
  border-radius: 10px;
  font-size: 13px;
}
.print-bar__text { flex: 1 1 280px; line-height: 1.55; }
.print-bar__text strong { color: #fff; }
.print-bar__tip {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: #c7d2fe;
}
.print-bar__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.print-bar button {
  background: #fff;
  color: #312e81;
  border: none;
  border-radius: 8px;
  padding: 8px 14px;
  font-family: inherit;
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
}
.print-bar button:hover { background: #eef2ff; }
h2 {
  margin: 32px 0 12px;
  padding-top: 8px;
  font-size: 22px;
  color: #1e293b;
  border-top: 1px solid var(--border);
}
h2:first-of-type { border-top: none; margin-top: 0; }
h3 {
  margin: 20px 0 8px;
  font-size: 17px;
  color: #334155;
}
.chapter h4 + p,
.chapter h4 + ul,
.chapter h4 + ol,
.chapter h4 + .table-wrap {
  margin-top: 0;
}
p { margin: 0 0 12px; }
article a { color: var(--accent); word-break: break-all; }
article a:hover { text-decoration: underline; }
ul, ol { margin: 0 0 14px; padding-left: 22px; }
li { margin-bottom: 6px; }
strong { color: #1e293b; }
code {
  font-family: Consolas, monospace;
  font-size: 13px;
  background: #f1f5f9;
  padding: 1px 6px;
  border-radius: 4px;
}
pre {
  background: #0f172a;
  color: #e2e8f0;
  padding: 14px 16px;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 13px;
  margin: 0 0 16px;
}
blockquote.note {
  margin: 12px 0 16px;
  padding: 12px 14px;
  background: var(--warn-bg);
  border-left: 4px solid var(--warn-brd);
  border-radius: 0 8px 8px 0;
  color: #78350f;
}
.table-wrap { overflow-x: auto; margin: 0 0 14px; }
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
th, td {
  border: 1px solid var(--border);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}
th { background: #f1f5f9; font-weight: 700; }
tr:nth-child(even) td { background: #fafafa; }
p.lead-block {
  margin: 0 0 10px;
  padding: 10px 12px;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid var(--border);
  font-size: 14px;
}
.keep-together {
  margin: 0 0 14px;
}
.faq {
  margin: 0 0 12px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #fafafa;
}
.faq-q { margin: 0 0 6px; font-weight: 700; color: var(--accent); }
.faq-a { margin: 0; color: var(--muted); }
.footer-note {
  margin-top: 24px;
  font-size: 13px;
  color: var(--muted);
  text-align: center;
}
hr { display: none; }
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  nav.toc { position: static; }
  article { padding: 22px 18px 28px; }
}
@media print {
  @page {
    size: A4 portrait;
    margin: 14mm 12mm 16mm 12mm;
  }
  body {
    background: #fff;
    font-size: 10.5pt;
    line-height: 1.5;
    orphans: 3;
    widows: 3;
  }
  .print-bar, nav.toc { display: none !important; }
  .layout {
    display: block;
    max-width: none;
    padding: 0;
  }
  article {
    box-shadow: none;
    border: none;
    padding: 0 0 16mm 0;
    border-radius: 0;
  }
  .hero {
    margin-bottom: 14px;
    padding-bottom: 10px;
    border-bottom-width: 1px;
  }
  .hero h1 { font-size: 19pt; margin-bottom: 6px; }
  .hero .lead { font-size: 11pt; }
  .chapter {
    margin-bottom: 0;
    padding-bottom: 0;
  }
  .chapter > h3 {
    page-break-before: always;
    break-before: page;
    page-break-after: avoid;
    break-after: avoid-page;
    margin-bottom: 10px;
    padding: 8px 10px;
    font-size: 13pt;
    border-radius: 6px;
    box-shadow: none;
  }
  .chapter:first-of-type > h3 {
    page-break-before: auto;
    break-before: auto;
  }
  .chapter-section-head {
    page-break-before: auto !important;
    break-before: auto !important;
    page-break-after: avoid;
    break-after: avoid-page;
    margin-top: 10px;
    margin-bottom: 8px;
    padding: 8px 10px;
    font-size: 13pt;
    border-radius: 6px;
    box-shadow: none;
  }
  .chapter > h4#กดคำนวณ + ol {
    page-break-after: avoid;
    break-after: avoid-page;
  }
  h4 {
    page-break-after: avoid;
    break-after: avoid-page;
    margin-top: 12px;
    font-size: 11pt;
  }
  p { margin-bottom: 7px; }
  ul, ol { margin-bottom: 8px; padding-left: 18px; }
  li { margin-bottom: 3px; }
  .table-wrap {
    page-break-inside: avoid;
    break-inside: avoid-page;
    margin-bottom: 10px;
  }
  .keep-together {
    page-break-inside: avoid;
    break-inside: avoid-page;
    margin-bottom: 10px;
  }
  table { font-size: 9.5pt; }
  th, td { padding: 5px 7px; }
  figure.shot {
    break-inside: avoid;
    page-break-inside: avoid;
    margin: 8px 0 12px;
    box-shadow: none;
    border-radius: 6px;
    overflow: visible;
  }
  figure.shot img {
    display: block !important;
    width: 100%;
    max-width: 100%;
    height: auto !important;
    max-height: 140mm;
    object-fit: contain;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  figure.shot figcaption {
    padding: 6px 8px;
    font-size: 9pt;
  }
  code { font-size: 9pt; }
}
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="th">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>คู่มือการใช้งาน Target Allocation</title>
  <style>{css}</style>
</head>
<body>
  <div class="layout">
    <div class="print-bar">
      <div class="print-bar__text">
        <strong>📖 คู่มือ Target Allocation</strong>
        <span class="print-bar__tip">บันทึก PDF แบบ<strong>ไม่มีวันที่/ชื่อไฟล์</strong>ด้านบนล่าง: รัน <code style="background:#312e81;padding:1px 6px;border-radius:4px;">python docs/build-manual-html.py --pdf</code> · หรือ Ctrl+P แล้ว<strong> ปิด Headers and footers</strong> (Edge: ตั้งค่าเพิ่มเติม → หัวกระดาษและท้ายกระดาษ)</span>
      </div>
      <div class="print-bar__actions">
        <button type="button" onclick="window.print()">🖨️ พิมพ์ / บันทึก PDF</button>
      </div>
    </div>
    <nav class="toc" aria-label="สารบัญ">
      <h2>สารบัญ</h2>
      <ol>
        {toc_links}
      </ol>
    </nav>
    <article>
      <header class="hero">
        <h1>คู่มือการใช้งาน Target Allocation</h1>
        <p class="lead">ระบบกระจายเป้าหมาย (หีบ) ให้พนักงานขายรายคน</p>
      </header>
      {body}
    </article>
  </div>
</body>
</html>
"""


def find_edge() -> Path | None:
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", "")):
        if not base:
            continue
        p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if p.is_file():
            return p
    return None


def export_pdf(html_path: Path, pdf_path: Path) -> None:
    edge = find_edge()
    if not edge:
        raise RuntimeError("ไม่พบ Microsoft Edge สำหรับสร้าง PDF")
    url = html_path.resolve().as_uri()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(edge),
        "--headless=old",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--disable-features=LazyImageLoading",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=20000",
        f"--print-to-pdf={pdf_path}",
        url,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    make_pdf = "--pdf" in sys.argv
    argv = [a for a in sys.argv[1:] if a != "--pdf"]
    md_path = MD_PATH
    if argv:
        md_path = Path(argv[0])
    md = md_path.read_text(encoding="utf-8")
    # ข้ามหัวข้อ # แรก — ใส่ใน hero แล้ว
    md = re.sub(r"^# .+\n+", "", md, count=1)
    md = re.sub(r"^\*\*ระบบกระจาย.*?\*\*\n+", "", md, count=1)
    intro_match = re.match(r"^(.*?)---\n", md, re.DOTALL)
    intro = ""
    if intro_match:
        intro = intro_match.group(1).strip()
        md = md[intro_match.end() :]
    body, toc = md_to_body(md)
    if intro:
        intro_html = "".join(f"<p>{inline_md(p.strip())}</p>" for p in intro.split("\n\n") if p.strip())
        body = intro_html + body
    toc_links = "\n".join(f'<li><a href="#{sid}">{html.escape(label)}</a></li>' for sid, label in toc)
    page = HTML_TEMPLATE.format(css=CSS, toc_links=toc_links, body=body)
    for out_path in OUT_PATHS:
        html_out = page
        if out_path.parent.name == "frontend":
            html_out = html_out.replace('src="images/', 'src="manual-images/')
            html_out = embed_local_images(html_out)
        out_path.write_text(html_out, encoding="utf-8")
        print(f"Wrote {out_path}")
    if IMAGES_SRC.is_dir():
        if FRONTEND_IMAGES.exists():
            shutil.rmtree(FRONTEND_IMAGES)
        shutil.copytree(IMAGES_SRC, FRONTEND_IMAGES)
        print(f"Copied images -> {FRONTEND_IMAGES}")
    if make_pdf:
        export_pdf(DOCS / "user-manual-th.html", PDF_PATH)
        print(f"Wrote {PDF_PATH} (no header/footer)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
