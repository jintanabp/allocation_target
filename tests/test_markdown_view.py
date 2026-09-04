"""ตัวแปลง Markdown ของหน้า /doc/<ชื่อ> — เอกสารในโปรเจกต์ต้องอ่านออก ไม่ใช่ '#' ดิบ"""
import unittest

from backend.services.markdown_view import md_to_html, render_markdown_page


class TestMarkdownView(unittest.TestCase):
    def test_heading_levels(self):
        html = md_to_html("# หัวข้อใหญ่\n\n### หัวข้อย่อย")
        self.assertIn("<h1>หัวข้อใหญ่</h1>", html)
        self.assertIn("<h3>หัวข้อย่อย</h3>", html)

    def test_paragraph_joins_wrapped_lines(self):
        html = md_to_html("บรรทัดแรก\nบรรทัดที่สอง\n\nย่อหน้าใหม่")
        self.assertIn("<p>บรรทัดแรก บรรทัดที่สอง</p>", html)
        self.assertIn("<p>ย่อหน้าใหม่</p>", html)

    def test_lists(self):
        self.assertIn("<ul><li>หนึ่ง</li><li>สอง</li></ul>", md_to_html("- หนึ่ง\n- สอง"))
        self.assertIn("<ol><li>หนึ่ง</li><li>สอง</li></ol>", md_to_html("1. หนึ่ง\n2. สอง"))

    def test_table(self):
        html = md_to_html("| ชื่อ | ค่า |\n|---|---|\n| a | 1 |")
        self.assertIn("<th>ชื่อ</th>", html)
        self.assertIn("<td>a</td>", html)
        self.assertIn("<td>1</td>", html)

    def test_pipe_line_without_separator_is_not_a_table(self):
        html = md_to_html("ข้อความ | ที่มีขีดคั่น")
        self.assertNotIn("<table>", html)

    def test_fenced_code_keeps_text_as_is(self):
        html = md_to_html("```\n**ไม่ใช่ตัวหนา**\n```")
        self.assertIn("<pre><code>**ไม่ใช่ตัวหนา**</code></pre>", html)

    def test_inline_code_beats_bold(self):
        html = md_to_html("ใช้ `a**b**c` ตรงนี้")
        self.assertIn("<code>a**b**c</code>", html)
        self.assertNotIn("<strong>", html)

    def test_bold_italic_link(self):
        html = md_to_html("**หนา** และ *เอียง* และ [ลิงก์](https://example.com/x)")
        self.assertIn("<strong>หนา</strong>", html)
        self.assertIn("<em>เอียง</em>", html)
        self.assertIn('<a href="https://example.com/x" rel="noopener">ลิงก์</a>', html)

    def test_html_in_source_is_escaped(self):
        html = md_to_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_blockquote_and_hr(self):
        html = md_to_html("> เตือน\n\n---")
        self.assertIn("<blockquote>เตือน</blockquote>", html)
        self.assertIn("<hr />", html)

    def test_page_has_title_and_body(self):
        page = render_markdown_page("# สวัสดี", "DATA_FLOW")
        self.assertIn("<title>DATA_FLOW</title>", page)
        self.assertIn("<h1>สวัสดี</h1>", page)
        self.assertTrue(page.startswith("<!doctype html>"))

    def test_real_docs_render_without_error(self):
        import pathlib

        docs = pathlib.Path(__file__).resolve().parent.parent / "docs"
        found = sorted(docs.glob("*.md"))
        self.assertTrue(found, "ต้องมีไฟล์ .md ใน docs/ ให้ทดสอบ")
        for md in found:
            with self.subTest(doc=md.name):
                out = md_to_html(md.read_text(encoding="utf-8"))
                self.assertIsInstance(out, str)


if __name__ == "__main__":
    unittest.main()
