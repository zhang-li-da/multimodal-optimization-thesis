"""Build standalone HTML readers and check all local references."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
NAMES = ["README", "DELIVERY", "PROPOSAL_CHAPTER6", "PREREGISTRATION", "NOVELTY_AUDIT", "TECHNICAL_ROUTE",
         "EXPERIMENT_DESIGN", "results/measured_report", "results/classifier_context",
         "results/witness_mechanism", "results/mechanism_diagnostics", "results/descriptor_diagnostic"]
STYLE = """<style>
body{max-width:1180px;padding:36px 28px;font:16px/1.85 'Microsoft YaHei','Segoe UI',sans-serif;color:#253c36;background:#fafbf8}
h1,h2,h3{color:#175e52;line-height:1.45}h1{font-size:29px}h2{margin-top:2em;font-size:23px}
a{color:#17695e}table{font-size:14px;display:block;overflow-x:auto}th,td{padding:9px 12px;border-bottom:1px solid #d7e1d7}
thead{background:#e9f0e8}pre{background:#eef2eb;padding:15px;white-space:pre-wrap}code{font:14px Consolas,monospace}
blockquote{border-left:4px solid #91ae96;padding-left:20px}img{max-width:100%;height:auto}
math{font-size:1.05em}@media print{body{max-width:none;padding:0;background:white}a{color:inherit}h2{break-after:avoid}}
</style>"""


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []

    def handle_starttag(self, tag, attrs):
        if tag in ("a", "img"):
            self.links.extend(value for key, value in attrs if key in ("href", "src"))


def main():
    sources = [ROOT / (name + ".md") for name in NAMES if (ROOT / (name + ".md")).exists()]
    rendered = {p.resolve() for p in sources}
    style = ROOT / "artifacts/report_style.html"
    style.parent.mkdir(exist_ok=True); style.write_text(STYLE, encoding="utf-8")
    issues = []
    for source in sources:
        text = source.read_text(encoding="utf-8")
        if chr(0xfffd) in text:
            issues.append({"file": str(source), "problem": "replacement character"})

        def rewrite(match):
            target = match.group(1)
            if urlsplit(target).scheme or target.startswith("#"):
                return match.group(0)
            path = source.parent / unquote(target)
            if path.resolve() in rendered:
                path = path.with_suffix(".html")
            relative = os.path.relpath(path, source.parent).replace(os.sep, "/")
            return "](" + quote(relative, safe="/#:") + ")"

        text = re.sub(r"\]\(([^)]+)\)", rewrite, text)
        subprocess.run(["pandoc", "--from=markdown", "--standalone", "--mathml",
                        "--metadata", "lang=zh-CN", "--metadata", "pagetitle=" + text.splitlines()[0].lstrip("# "),
                        "--include-in-header", str(style), "--output", str(source.with_suffix(".html"))],
                       input=text, encoding="utf-8", check=True)
        parser = Links(); parser.feed(source.with_suffix(".html").read_text(encoding="utf-8"))
        for link in parser.links:
            value = urlsplit(link)
            if value.scheme or not value.path:
                continue
            if not (source.parent / unquote(value.path)).exists():
                issues.append({"file": str(source), "missing_link": link})
    result = {"pass": not issues, "reports": len(sources), "issues": issues}
    (ROOT / "results/document_check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
