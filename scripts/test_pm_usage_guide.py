from html.parser import HTMLParser
from pathlib import Path
import os
import re

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "PM使用指南_游戏术语提取与标注.html"
RESOURCE_ATTRIBUTES = {
    "audio": ("src",),
    "embed": ("src",),
    "iframe": ("src",),
    "image": ("href", "xlink:href"),
    "img": ("src", "srcset"),
    "input": ("src",),
    "link": ("href",),
    "object": ("data",),
    "script": ("src",),
    "source": ("src", "srcset"),
    "track": ("src",),
    "use": ("href", "xlink:href"),
    "video": ("src", "poster"),
}
HIDDEN_TAGS = {"noscript", "script", "style", "template"}
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class GuideParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.hrefs = []
        self.external = []
        self.top_level_section_ids = []
        self.visible_text = []
        self.css = []
        self.title_text = []
        self._section_depth = 0
        self._body_depth = 0
        self._hidden_depth = 0
        self._style_depth = 0
        self._title_depth = 0
        self._element_stack = []

    def handle_starttag(self, tag, attrs):
        self._starttag(tag, attrs, self_closing=tag in VOID_TAGS)

    def handle_startendtag(self, tag, attrs):
        self._starttag(tag, attrs, self_closing=True)

    def _starttag(self, tag, attrs, self_closing):
        data = dict(attrs)
        if "id" in data:
            self.ids.add(data["id"])
        if tag == "a" and "href" in data:
            self.hrefs.append(data["href"])
        if tag == "section":
            if self._section_depth == 0 and "id" in data:
                self.top_level_section_ids.append(data["id"])
            if not self_closing:
                self._section_depth += 1
        for key in RESOURCE_ATTRIBUTES.get(tag, ()):
            value = (data.get(key) or "").strip()
            if key == "srcset" and value:
                self.external.append(f"{tag}[{key}] is not allowed")
            elif value and not value.startswith(("data:", "#")):
                self.external.append(f"{tag}[{key}]={value}")
        inline_style = data.get("style", "")
        if inline_style:
            self.css.append(inline_style)
        if self_closing:
            return
        hidden = (
            tag in HIDDEN_TAGS
            or "hidden" in data
            or data.get("aria-hidden", "").strip().lower() == "true"
            or bool(re.search(
                r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden|"
                r"content-visibility\s*:\s*hidden)\b",
                inline_style,
                re.IGNORECASE,
            ))
        )
        self._element_stack.append((tag, hidden))
        if hidden:
            self._hidden_depth += 1
        if tag == "body":
            self._body_depth += 1
        if tag == "style":
            self._style_depth += 1
        if tag == "title":
            self._title_depth += 1

    def handle_endtag(self, tag):
        if tag == "section" and self._section_depth:
            self._section_depth -= 1
        for index in range(len(self._element_stack) - 1, -1, -1):
            if self._element_stack[index][0] != tag:
                continue
            closing = self._element_stack[index:]
            del self._element_stack[index:]
            for name, hidden in closing:
                if hidden:
                    self._hidden_depth -= 1
                if name == "body":
                    self._body_depth -= 1
                if name == "style":
                    self._style_depth -= 1
                if name == "title":
                    self._title_depth -= 1
            break

    def handle_data(self, data):
        if self._title_depth:
            self.title_text.append(data)
        if self._style_depth:
            self.css.append(data)
        if self._body_depth and not self._hidden_depth:
            self.visible_text.append(data)


def main():
    html = GUIDE.read_text(encoding="utf-8")
    parser = GuideParser()
    parser.feed(html)
    body_text = re.sub(r"\s+", " ", "".join(parser.visible_text)).strip()
    title_text = re.sub(r"\s+", " ", "".join(parser.title_text)).strip()
    required_ids = {
        "purpose-preparation", "manual-operation", "agent-operation",
        "acceptance", "recovery", "extractor", "annotation",
    }
    require(required_ids <= parser.ids, f"missing ids: {sorted(required_ids - parser.ids)}")
    required_section_order = [
        "purpose-preparation", "manual-operation", "agent-operation",
        "acceptance", "recovery",
    ]
    actual_section_order = [
        section_id for section_id in parser.top_level_section_ids
        if section_id in required_section_order
    ]
    require(
        actual_section_order == required_section_order,
        f"top-level section order: {actual_section_order}",
    )
    require(not parser.external, f"external resources: {parser.external}")
    for href in parser.hrefs:
        if href.startswith("#"):
            require(href[1:] in parser.ids, f"broken internal link: {href}")
    css = "\n".join(parser.css)
    require(not re.search(r"@import\b", css, re.IGNORECASE), "CSS @import is not offline-safe")
    require(not re.search(r"@font-face\b", css, re.IGNORECASE), "CSS @font-face is not offline-safe")
    for match in re.finditer(
        r"url\(\s*(?P<quote>['\"]?)(?P<value>.*?)(?P=quote)\s*\)",
        css,
        re.IGNORECASE | re.DOTALL,
    ):
        value = match.group("value").strip()
        require(value.startswith(("data:", "#")), f"external CSS url(): {value}")
    required_text = [
        "run.bat", "setup.bat", "streamlit run ui/app.py",
        "annotation/术语标注助手_v3.0.html", "terms_extraction_results.xlsx",
        "term_project_", "术语表_", "仅补空白字段", "未审核",
        "Key值", "术语分类", "术语原文", "术语译文", "备注",
        "来源原文", "审核状态", "最新修订时间",
        "正式支持平台：Windows 10/11", "其他平台未经过正式验证",
    ]
    for text in required_text:
        require(text in body_text, f"missing visible text: {text}")
    columns = [
        "Key值", "术语分类", "术语原文", "术语译文", "备注",
        "来源原文", "审核状态", "最新修订时间",
    ]
    column_separator = r"\s*(?:[/·]\s*)?"
    column_pattern = column_separator.join(re.escape(column) for column in columns)
    require(re.search(column_pattern, body_text), f"column order: {' / '.join(columns)}")
    require(re.search(r"游戏术语提取.*标注", title_text), f"guide title: {title_text}")
    require(not (ROOT / "docs" / "DEPLOY.txt").exists(), "docs/DEPLOY.txt must be retired")
    readmes = [ROOT / "README.md"]
    optional_readme = ROOT / "README_ZH.md"
    if optional_readme.exists():
        readmes.append(optional_readme)
    else:
        require(
            os.environ.get("REQUIRE_README_ZH") != "1",
            "README_ZH.md is required when REQUIRE_README_ZH=1",
        )
    for readme in readmes:
        content = readme.read_text(encoding="utf-8-sig")
        require(
            "docs/PM使用指南_游戏术语提取与标注.html" in content,
            f"missing guide link: {readme}",
        )
        require("docs/DEPLOY.md" not in content, f"stale deployment link: {readme}")


if __name__ == "__main__":
    main()
