# PM Integrated Usage Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one verified, offline PM HTML guide for the extraction-to-annotation-to-glossary-return workflow, replace the obsolete deployment document, and repair README guide links.

**Architecture:** Copy the approved annotation guide as the visual base, retain its embedded Langlobal logo and offline CSS, and replace its single-tool body with five evidence-backed operational sections. Add a standard-library regression check for structure, offline safety, content, links, and migration completion.

**Tech Stack:** Standalone HTML5/CSS, embedded JPEG data URI, Python 3 standard library, Git.

## Global Constraints

- Final guide: `docs/PM使用指南_游戏术语提取与标注.html`.
- Section order: purpose/preparation, manual operation, natural-language Agent operation, acceptance checks, common problems/recovery.
- Supported deployment claim: Windows 10/11; other platforms receive only the source-defined technical command and an explicit unverified note.
- Public entries: `run.bat` and `annotation/术语标注助手_v3.0.html`.
- Preserve existing uncommitted work; use targeted edits and never stage unrelated README/UI changes.
- No external fonts, scripts, images, or network resources.
- Final columns, in order: `Key值 / 术语分类 / 术语原文 / 术语译文 / 备注 / 来源原文 / 审核状态 / 最新修订时间`.
- `README_ZH.md` is a user-untracked file in the original repository: isolated branches check it only when present; final verification in the original repository must run with `REQUIRE_README_ZH=1`.
- Delete `docs/DEPLOY.txt` only after the replacement guide passes content checks.

---

### Task 1: Add a failing guide contract test

**Files:**
- Create: `scripts/test_pm_usage_guide.py`

**Interfaces:**
- Consumes: final guide path, README files, retired deployment path.
- Produces: an executable standard-library check for structure, content, internal links, offline safety, and migration.

- [ ] **Step 1: Create the contract test**

```python
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
```

- [ ] **Step 2: Verify the initial failure**

Run: `python scripts/test_pm_usage_guide.py`

Expected: non-zero exit because the final guide does not yet exist.

### Task 2: Build the integrated offline guide

**Files:**
- Copy: `annotation/docs/操作指南_术语标注助手.html` → `docs/PM使用指南_游戏术语提取与标注.html`
- Modify: `docs/PM使用指南_游戏术语提取与标注.html`
- Read: `README_ZH.md`, `docs/DEPLOY.txt`, `ui/app.py`, `core/main.py`, `cli.py`, `run.bat`, `setup.bat`, `annotation/README.md`, `annotation/术语标注助手_v3.0.html`

**Interfaces:**
- Consumes: verified startup, labels, file contracts, output names, annotation behavior, and accepted design.
- Produces: self-contained HTML with stable anchors and no external resource loads.

- [ ] **Step 1: Copy the approved visual base**

Run: `cp annotation/docs/操作指南_术语标注助手.html docs/PM使用指南_游戏术语提取与标注.html`

Expected: destination exists and retains `data:image/jpeg;base64,`.

- [ ] **Step 2: Replace the identity and add navigation**

```html
<title>PM 使用指南 · 游戏术语提取与标注 — Langlobal</title>
<nav class="toc" aria-label="本页目录">
  <a href="#purpose-preparation">01 用途与准备</a>
  <a href="#manual-operation">02 手动操作</a>
  <a href="#agent-operation">03 Agent 操作</a>
  <a href="#acceptance">04 验收</a>
  <a href="#recovery">05 故障恢复</a>
  <a href="#extractor">主工具</a>
  <a href="#annotation">标注工具</a>
</nav>
```

- [ ] **Step 3: Replace the body with the verified workflow**

Use these exact top-level IDs and coverage:

```html
<section id="purpose-preparation">用途、Windows 10/11、工作目录、API/模型/Profile、两个 xlsx 输入、闭环、产物分类</section>
<section id="manual-operation">
  <h3 id="extractor">主工具：安装、启动、设置、列核对、提取、下载、断点</h3>
  <h3 id="annotation">标注工具：打开、两次导入、合并、标注、审核、保存、导出、回流</h3>
</section>
<section id="agent-operation">复制即用的环境检查、CLI 提取、结果验收指令</section>
<section id="acceptance">数量、文件、8 列、审核状态、Key/来源、人工语义验收</section>
<section id="recovery">API、Python、依赖、浏览器、列识别、断点、草稿、列缺失、大文件</section>
```

Each Agent prompt must include project path `/Users/spellbook/Desktop/Langlobal/Game-Terms-Extraction-Langlobal`, input placeholders, output requirement, constraints, checks, and explicit failure reporting. State that annotation has no public CLI and requires browser-based human review.

- [ ] **Step 4: Add responsive and print-safe styles**

Add `.toc`, `.table-wrap`, `.prompt`, and `.copy-note`; at `max-width: 720px`, make the TOC one column and tables horizontally scrollable; for print, hide navigation, wrap code, and avoid splitting cards/tables where practical.

- [ ] **Step 5: Run guide-only checks**

Run:

```bash
rg -n "purpose-preparation|manual-operation|agent-operation|acceptance|recovery|data:image/jpeg;base64" docs/PM使用指南_游戏术语提取与标注.html
rg -n "https?://|<script[^>]+src=|<link[^>]+href=" docs/PM使用指南_游戏术语提取与标注.html
```

Expected: required IDs and embedded logo exist; external-resource scan returns no matches.

### Task 3: Retire deployment guide and repair entry links

**Files:**
- Modify: `README.md`
- Modify: `README_ZH.md`
- Delete: `docs/DEPLOY.txt`

**Interfaces:**
- Consumes: verified replacement guide.
- Produces: one canonical PM guide and no stale deployment route.

- [ ] **Step 1: Add the canonical links**

Chinese README:

```markdown
> 安装、配置、提取、人工标注与验收见 [PM 整合使用指南](docs/PM使用指南_游戏术语提取与标注.html)。
```

English README:

```markdown
For installation, extraction, human annotation, and acceptance, see the [integrated PM usage guide](docs/PM使用指南_游戏术语提取与标注.html) (Chinese).
```

Remove the stale `docs/DEPLOY.md` link if present.

- [ ] **Step 2: Delete only `docs/DEPLOY.txt`**

Retain every other file under `docs/`.

- [ ] **Step 3: Run the full contract test**

Run: `python scripts/test_pm_usage_guide.py`

Expected: exit code 0 with no output.

- [ ] **Step 4: Inspect the migration diff**

Run: `git diff --check` and targeted `git diff` for the guide, test, READMEs, and deployment deletion.

Expected: no whitespace errors; README changes are limited to canonical links; pre-existing UI/README work stays unstaged.

### Task 4: Render and visually verify

**Files:**
- Verify: `docs/PM使用指南_游戏术语提取与标注.html`
- Output: `work/pm-guide-desktop.png`
- Output: `work/pm-guide-mobile.png`

**Interfaces:**
- Consumes: completed standalone guide.
- Produces: desktop and narrow-screen visual evidence without runtime changes.

- [ ] **Step 1: Render at 1440×1000 and 390×844**

Use an available local Chromium/Playwright runtime. If unavailable, record visual rendering as the only verification gap and retain static checks.

- [ ] **Step 2: Inspect and correct targeted CSS**

Check header/logo, section order, navigation, table overflow, prompt wrapping, clipped text, and footer identity.

- [ ] **Step 3: Re-run verification**

Run: `REQUIRE_README_ZH=1 python3 scripts/test_pm_usage_guide.py` and `git diff --check`.

Expected: test passes and diff check is clean.

- [ ] **Step 4: Commit only isolated files when safe**

```bash
git add docs/PM使用指南_游戏术语提取与标注.html scripts/test_pm_usage_guide.py docs/DEPLOY.txt
git commit -m "docs: add integrated PM workflow guide"
```

Do not stage either README because both contain pre-existing user work unless their new link hunks can be isolated safely.
