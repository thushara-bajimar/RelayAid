"""Build the PDF deliverables from the Markdown sources.

    python scripts/build_pdfs.py

Markdown -> HTML (Python-Markdown, tables, code, TOC) -> PDF with headless Microsoft Edge via
Playwright. ```mermaid blocks are rendered as diagrams by mermaid.js (fetched from the
jsDelivr CDN at build time; without internet they fall back to the diagram source text).
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"

DOCS = {
    "RelayAid_Technical_Documentation.pdf": ("RelayAid - Technical Documentation", [ROOT / "TECHNICAL_DOCUMENTATION.md"]),
    "RelayAid_Project_Report.pdf": ("RelayAid - Project Report", [ROOT / "docs" / "PROJECT_REPORT.md"]),
}

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 10.5pt; line-height: 1.5; color: #111827; }
h1 { font-size: 22pt; border-bottom: 3px solid #2a78d6; padding-bottom: 6px; margin-top: 0; page-break-before: always; }
h1:first-of-type { page-break-before: avoid; }
h2 { font-size: 15pt; color: #0f172a; border-bottom: 1px solid #e2e8f0; padding-bottom: 3px; margin-top: 22px; page-break-after: avoid; }
h3 { font-size: 12pt; color: #1e293b; margin-top: 16px; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 9pt; page-break-inside: auto; }
th, td { border: 1px solid #cbd5e1; padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #f1f5f9; }
tr { page-break-inside: avoid; }
code { font-family: 'Cascadia Mono', Consolas, monospace; font-size: 8.8pt; background: #f1f5f9; padding: 0 3px; border-radius: 3px; }
pre { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
img { max-width: 100%; border: 1px solid #e2e8f0; border-radius: 6px; margin: 6px 0; page-break-inside: avoid; }
blockquote { border-left: 4px solid #2a78d6; margin: 8px 0; padding: 2px 12px; color: #334155; background: #f8fafc; }
.mermaid { text-align: center; margin: 10px 0; page-break-inside: avoid; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always; }
.cover .t { font-size: 34pt; font-weight: 700; color: #0f172a; }
.cover .s { font-size: 16pt; color: #2a78d6; margin-top: 6px; }
.cover .m { margin-top: 40px; color: #475569; font-size: 11pt; }
.toc { page-break-after: always; }
.toc ul { list-style: none; padding-left: 14px; }
.toc > ul { padding-left: 0; }
.toc a { color: #0f172a; text-decoration: none; }
a { color: #1d4ed8; }
"""


def md_to_html(text: str) -> tuple[str, str]:
    blocks: list[str] = []

    def stash(m):
        blocks.append(m.group(1))
        return f"\n\nMERMAIDBLOCK{len(blocks) - 1}\n\n"
    text = re.sub(r"```mermaid\n(.*?)```", stash, text, flags=re.S)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"],
                           extension_configs={"toc": {"toc_depth": "1-2"}})
    body = md.convert(text)
    for i, src in enumerate(blocks):
        body = body.replace(f"<p>MERMAIDBLOCK{i}</p>", f'<pre class="mermaid">{html.escape(src)}</pre>')
    return body, md.toc


def build(title: str, sources: list[Path], out: Path, page) -> None:
    parts = []
    for src in sources:  # resolve relative image links against each source file's folder
        t = src.read_text(encoding="utf-8")
        t = re.sub(r"!\[([^\]]*)\]\((?!https?:|file:)([^)]+)\)",
                   lambda m: f"![{m.group(1)}]({(src.parent / m.group(2)).resolve().as_uri()})", t)
        parts.append(t)
    body, toc = md_to_html("\n\n".join(parts))
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title><style>{CSS}</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script></head><body>
<div class="cover"><div class="t">RelayAid</div><div class="s">{title.split(' - ')[1]}</div>
<div class="s" style="font-size:13pt;color:#475569">Coordinate Without Connection - offline-first disaster coordination
and resilient data synchronization</div><div class="m">Domain: Data Engineering &amp; Distributed Systems</div></div>
<div class="toc"><h2>Contents</h2>{toc}</div>
{body}
<script>
  window.__done = false;
  if (window.mermaid) {{
    mermaid.initialize({{ startOnLoad: false, theme: 'neutral', securityLevel: 'strict' }});
    mermaid.run({{ querySelector: '.mermaid' }}).then(() => window.__done = true).catch(() => window.__done = true);
  }} else {{ window.__done = true; }}
</script></body></html>"""
    tmp = ROOT / f".pdfbuild-{out.stem}.html"  # in the repo root so relative image paths resolve
    tmp.write_text(doc, encoding="utf-8")
    try:
        page.goto(tmp.as_uri())
        page.wait_for_function("window.__done === true", timeout=60000)
        page.wait_for_timeout(500)
        page.pdf(path=str(out), format="A4", print_background=True, display_header_footer=True,
                 header_template="<div></div>",
                 footer_template='<div style="font-size:8px;color:#64748b;width:100%;text-align:center;">'
                                 f'{title} &middot; page <span class="pageNumber"></span> of <span class="totalPages"></span></div>',
                 margin={"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"})
    finally:
        tmp.unlink(missing_ok=True)
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page()
        for name, (title, sources) in DOCS.items():
            missing = [s for s in sources if not s.exists()]
            if missing:
                sys.exit(f"missing source: {missing}")
            build(title, sources, OUT / name, page)
        browser.close()


if __name__ == "__main__":
    main()
