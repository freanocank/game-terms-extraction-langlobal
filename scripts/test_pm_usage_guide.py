from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "PM使用指南_游戏术语提取与标注.html"


class GuideParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.hrefs = []
        self.external = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if "id" in data:
            self.ids.add(data["id"])
        if tag == "a" and "href" in data:
            self.hrefs.append(data["href"])
        if tag in {"script", "img", "link"}:
            key = "href" if tag == "link" else "src"
            value = data.get(key, "")
            if value and not value.startswith(("data:", "#")):
                self.external.append(value)


def main():
    html = GUIDE.read_text(encoding="utf-8")
    parser = GuideParser()
    parser.feed(html)
    required_ids = {
        "purpose-preparation", "manual-operation", "agent-operation",
        "acceptance", "recovery", "extractor", "annotation",
    }
    assert required_ids <= parser.ids
    assert not parser.external, parser.external
    for href in parser.hrefs:
        if href.startswith("#"):
            assert href[1:] in parser.ids, href
    required_text = [
        "run.bat", "setup.bat", "streamlit run ui/app.py",
        "annotation/术语标注助手_v3.0.html", "terms_extraction_results.xlsx",
        "term_project_", "术语表_", "仅补空白字段", "未审核",
        "Key值", "术语分类", "术语原文", "术语译文", "备注",
        "来源原文", "审核状态", "最新修订时间",
    ]
    for text in required_text:
        assert text in html, text
    assert re.search(r"<title>[^<]*游戏术语提取[^<]*标注[^<]*</title>", html)
    assert not (ROOT / "docs" / "DEPLOY.txt").exists()
    for readme in (ROOT / "README.md", ROOT / "README_ZH.md"):
        assert "docs/PM使用指南_游戏术语提取与标注.html" in readme.read_text(encoding="utf-8-sig")


if __name__ == "__main__":
    main()
