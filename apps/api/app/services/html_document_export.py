from __future__ import annotations

import html
import re
from pathlib import Path

from app.models import BoardDocument
from app.services.rich_document import text_to_html

_KATEX_VERSION = "0.16.45"
_SCRIPT_TAG_RE = re.compile(r"<script\b[\s\S]*?</script\s*>", re.IGNORECASE)
_DANGEROUS_TAG_RE = re.compile(r"</?(?:iframe|object|embed|link|meta)\b[^>]*>", re.IGNORECASE)
_EVENT_HANDLER_ATTR_RE = re.compile(r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_JAVASCRIPT_URL_RE = re.compile(r"\s+(href|src)\s*=\s*(['\"])\s*javascript:[\s\S]*?\2", re.IGNORECASE)


def _safe_document_body(content_html: str) -> str:
    body = _SCRIPT_TAG_RE.sub("", content_html)
    body = _DANGEROUS_TAG_RE.sub("", body)
    body = _EVENT_HANDLER_ATTR_RE.sub("", body)
    return _JAVASCRIPT_URL_RE.sub("", body)


def standalone_html(document: BoardDocument) -> str:
    title = html.escape(document.title or "OpenClass Document")
    content = _safe_document_body(document.content_html or text_to_html(document.content_text))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@{_KATEX_VERSION}/dist/katex.min.css">
  <style>
    :root {{
      color-scheme: light;
      font-family: "Inter", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", Arial, sans-serif;
      color: #172033;
      background: #eef2f7;
    }}
    body {{
      margin: 0;
      padding: 40px 20px;
      background: #eef2f7;
    }}
    main {{
      box-sizing: border-box;
      max-width: 860px;
      min-height: calc(100vh - 80px);
      margin: 0 auto;
      padding: 56px 64px;
      background: #fff;
      box-shadow: 0 18px 50px rgba(15, 23, 42, 0.12);
    }}
    h1, h2, h3 {{
      line-height: 1.35;
      color: #1d4f91;
    }}
    h1 {{
      margin: 0 0 22px;
      padding-bottom: 14px;
      border-bottom: 1px solid #b9c7d8;
      font-size: 28px;
    }}
    h2 {{
      margin: 28px 0 12px;
      font-size: 21px;
    }}
    h3 {{
      margin: 22px 0 10px;
      font-size: 17px;
    }}
    p, li {{
      font-size: 15px;
      line-height: 1.85;
    }}
    table {{
      width: 100%;
      margin: 16px 0 20px;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 9px 11px;
      border: 1px solid #cfd7e3;
      vertical-align: top;
    }}
    th {{
      background: #f3f6fa;
      font-weight: 700;
    }}
    img {{
      max-width: 100%;
      height: auto;
    }}
    [data-type="inline-math"] {{
      display: inline-block;
      max-width: 100%;
      overflow-x: auto;
      vertical-align: middle;
    }}
    [data-type="block-math"] {{
      display: block;
      max-width: 100%;
      margin: 16px auto;
      overflow-x: auto;
      text-align: center;
    }}
    .math-fallback {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      white-space: pre-wrap;
    }}
    @media print {{
      body {{
        padding: 0;
        background: #fff;
      }}
      main {{
        max-width: none;
        min-height: 0;
        padding: 0;
        box-shadow: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    {content}
  </main>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@{_KATEX_VERSION}/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@{_KATEX_VERSION}/dist/contrib/mhchem.min.js"></script>
  <script>
    window.addEventListener("load", function () {{
      document.querySelectorAll('[data-type="inline-math"], [data-type="block-math"]').forEach(function (node) {{
        var latex = node.getAttribute("data-latex") || "";
        var displayMode = node.getAttribute("data-type") === "block-math";
        if (!latex) {{
          return;
        }}
        if (window.katex) {{
          try {{
            window.katex.render(latex, node, {{
              displayMode: displayMode,
              throwOnError: false,
              strict: "ignore"
            }});
            return;
          }} catch (error) {{
            // Fall through to readable source fallback.
          }}
        }}
        node.classList.add("math-fallback");
        node.textContent = displayMode ? "\\\\[" + latex + "\\\\]" : "\\\\(" + latex + "\\\\)";
      }});
    }});
  </script>
</body>
</html>
"""


def export_html(document: BoardDocument, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(standalone_html(document), encoding="utf-8")
    return path
