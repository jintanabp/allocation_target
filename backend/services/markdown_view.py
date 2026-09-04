"""แปลง Markdown ในโฟลเดอร์ docs/ เป็นหน้า HTML อ่านได้

ทำไมต้องเขียนเอง:
  ปุ่ม「เอกสารฉบับเต็ม」ในแท็บแหล่งข้อมูลลิงก์ไป /docs/DATA_FLOW.md ตรง ๆ ซึ่ง
  StaticFiles เสิร์ฟเป็น text/plain เบราว์เซอร์จึงโชว์ '#' กับ '|' ดิบ ๆ
  จะโหลดตัวแปลง markdown จาก CDN ก็ไม่ได้ — เครือข่ายองค์กรบล็อก CDN อยู่แล้ว
  (เหตุผลเดียวกับที่ msal ต้องวางเป็นไฟล์ในโปรเจกต์) และไม่มีไลบรารี markdown
  ใน requirements ด้วย · ตัวแปลงนี้จึงรองรับเฉพาะที่เอกสารในโปรเจกต์ใช้จริง
  หัวข้อ / ย่อหน้า / รายการ / ตาราง / code block / blockquote / เส้นคั่น
  ไม่ใช่ markdown ครบสเปก และไม่ควรเอาไปใช้กับข้อความจากผู้ใช้
"""
from __future__ import annotations

import html
import re

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ULI = re.compile(r"^\s*[-*+]\s+(.*)$")
_OLI = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HR = re.compile(r"^\s*([-*_])\1{2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


def _inline(text: str) -> str:
    """เอสเคปก่อนเสมอ แล้วค่อยใส่แท็กที่อนุญาต — กัน HTML ในเอกสารหลุดออกมา"""
    out = html.escape(text, quote=False)
    # code ต้องมาก่อน ไม่งั้น ** ข้างในโค้ดจะกลายเป็นตัวหนา
    holds: list[str] = []

    def keep(m: re.Match[str]) -> str:
        holds.append(m.group(1))
        return "\x00%d\x00" % (len(holds) - 1)

    out = _INLINE_CODE.sub(keep, out)
    out = _LINK.sub(lambda m: '<a href="%s" rel="noopener">%s</a>'
                    % (html.escape(m.group(2), quote=True), m.group(1)), out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    for i, code in enumerate(holds):
        out = out.replace("\x00%d\x00" % i, "<code>%s</code>" % code)
    return out


def _split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def md_to_html(md_text: str) -> str:
    lines = str(md_text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # code block
        if line.lstrip().startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].lstrip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(buf), quote=False))
            continue

        if not line.strip():
            i += 1
            continue

        if _HR.match(line):
            out.append("<hr />")
            i += 1
            continue

        m = _HEADING.match(line)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, _inline(m.group(2).strip()), lvl))
            i += 1
            continue

        # ตาราง: ต้องมีบรรทัดคั่น |---|---| ต่อจากหัวตารางเท่านั้น
        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]) and "|" in lines[i + 1]:
            head = _split_row(line)
            i += 2
            body: list[list[str]] = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                body.append(_split_row(lines[i]))
                i += 1
            out.append("<table><thead><tr>"
                       + "".join("<th>%s</th>" % _inline(c) for c in head)
                       + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join("<td>%s</td>" % _inline(c) for c in row) + "</tr>"
                                 for row in body)
                       + "</tbody></table>")
            continue

        if _ULI.match(line) or _OLI.match(line):
            ordered = bool(_OLI.match(line))
            items: list[str] = []
            while i < n and lines[i].strip():
                mm = _OLI.match(lines[i]) if ordered else _ULI.match(lines[i])
                if not mm:
                    break
                items.append("<li>%s</li>" % _inline(mm.group(1)))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join(items), tag))
            continue

        if line.lstrip().startswith(">"):
            quote = []
            while i < n and lines[i].lstrip().startswith(">"):
                quote.append(lines[i].lstrip()[1:].strip())
                i += 1
            out.append("<blockquote>%s</blockquote>" % _inline(" ".join(quote)))
            continue

        # ย่อหน้า — รวมบรรทัดติดกันจนกว่าจะเจอบรรทัดว่างหรือบล็อกอื่น
        para = []
        while i < n and lines[i].strip():
            nxt = lines[i]
            if (_HEADING.match(nxt) or _ULI.match(nxt) or _OLI.match(nxt)
                    or _HR.match(nxt) or nxt.lstrip().startswith("```")
                    or nxt.lstrip().startswith(">")):
                break
            para.append(nxt.strip())
            i += 1
        if para:
            out.append("<p>%s</p>" % _inline(" ".join(para)))
        else:
            i += 1
    return "\n".join(out)


_PAGE = """<!doctype html>
<html lang="th"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    margin: 0 auto; padding: 28px 20px 64px; max-width: 54rem;
    font-family: "Sarabun", "Segoe UI", system-ui, sans-serif;
    font-size: 15px; line-height: 1.75; color: #1e293b; background: #ffffff;
  }}
  h1, h2, h3, h4 {{ line-height: 1.35; margin: 1.6em 0 0.5em; color: #0f172a; }}
  h1 {{ font-size: 1.7rem; margin-top: 0; }}
  h2 {{ font-size: 1.35rem; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; }}
  h3 {{ font-size: 1.1rem; }}
  p, li {{ margin: 0.5em 0; }}
  ul, ol {{ padding-left: 1.4em; }}
  code {{
    font-family: "Cascadia Mono", Consolas, monospace; font-size: 0.9em;
    background: #f1f5f9; padding: 1px 5px; border-radius: 4px;
  }}
  pre {{
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 12px 14px; overflow-x: auto;
  }}
  pre code {{ background: none; padding: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; display: block; overflow-x: auto; }}
  th, td {{ border: 1px solid #e2e8f0; padding: 7px 10px; text-align: left; vertical-align: top; }}
  th {{ background: #f8fafc; }}
  blockquote {{
    margin: 1em 0; padding: 8px 14px; border-left: 3px solid #cbd5e1;
    background: #f8fafc; color: #475569;
  }}
  hr {{ border: 0; border-top: 1px solid #e2e8f0; margin: 2em 0; }}
  a {{ color: #4338ca; }}
  .doc-back {{ display: inline-block; margin-bottom: 18px; font-size: 13px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #0f1729; color: #e6edf7; }}
    h1, h2, h3, h4 {{ color: #f1f5f9; }}
    h2 {{ border-bottom-color: #1e293b; }}
    code {{ background: #1e293b; }}
    pre, th, blockquote {{ background: #131c2f; }}
    pre, th, td, hr, blockquote {{ border-color: #1e293b; }}
    a {{ color: #a5b4fc; }}
  }}
</style></head>
<body>
<a class="doc-back" href="/">← กลับหน้าแอป</a>
{body}
</body></html>
"""


def render_markdown_page(md_text: str, title: str) -> str:
    return _PAGE.format(title=html.escape(title, quote=False), body=md_to_html(md_text))
