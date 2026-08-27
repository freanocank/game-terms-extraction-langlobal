# 游戏术语提取工具

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-app-ff4b4b.svg)](https://streamlit.io/)

[English](README.md) | 中文

从游戏文本中自动提取术语并生成指定目标语言译文，输出双语术语表 xlsx。默认语言对仍为简体中文→英语。

## 多语言对

- 单次任务处理一个目标语言。
- 网页内置常见本地化语言；CLI 接受规范化的 BCP 47 风格语言代码。
- 源文件已有同行目标译文时，可直接沿用目标列；没有时由 AI 生成译文。
- `EN-v2 / DE-MT / FR-MT / ID` 等表头可按所选语言对自动映射。
- 新语言对的代码兼容不等于语言质量认证，交付前仍需对应语言 LQA。

非默认源语言不会继承 Profile 中的中文姓名、称谓等专项规则。需要项目级调优时，可按规范代码配置 `core_principle_by_source_language`、`extract_examples_by_source_language`、`extraction_notes_by_source_language`、`fewshot_examples_by_language_pair` 和 `translation_rules_by_language_pair`，键示例为 `en`、`en->de`。

## 快速启动

**Windows 首次使用：先双击 `setup.bat`，完成后双击 `run.bat`**

手动启动：
```bash
pip install -r requirements.txt
streamlit run ui/app.py
```

CLI 入口（术语表可选）：
```bash
python cli.py --source 原文.xlsx
python cli.py --source 原文.xlsx --glossary 已有术语表.xlsx

# 英语→德语：列索引从 0 开始
python cli.py --source 原文.xlsx --source-lang en --target-lang de \
  --src-col 2 --aligned-target --src-target-col 3 --key-col 0

# 英语→法语
python cli.py --source 原文.xlsx --source-lang en --target-lang fr \
  --src-col 2 --aligned-target --src-target-col 4 --key-col 0
```

## API 服务配置

网页「设置」可分别配置四组兼容 OpenAI 的服务：

- 主生成服务：术语提取与翻译。
- 人名/地名预识别（NER）：减少专名漏提；默认继承主服务密钥和地址。
- 术语相似度匹配（Embedding）：沿用最接近的项目术语；默认继承主服务密钥和地址。
- 表头识别：Excel 列自动判断；默认继承主服务密钥和地址。

所有 API 密钥仅保存在当前进程内存，不写入 `.ui_config.json`、断点、日志或导出文件。重启后可重新填写，或通过环境变量提供：

| 服务 | 密钥 | 地址 | 模型 |
|---|---|---|---|
| 主生成 | `OPENAI_API_KEY` | `OPENAI_API_BASE` / `OPENAI_BASE_URL` | `OPENAI_MODEL` |
| NER | `NER_API_KEY` | `NER_API_BASE` / `NER_BASE_URL` | `NER_MODEL` |
| Embedding | `EMBEDDING_API_KEY` | `EMBEDDING_API_BASE` / `EMBEDDING_BASE_URL` | `EMBEDDING_MODEL` |
| 表头识别 | `DETECT_API_KEY` | `DETECT_API_BASE` / `DETECT_BASE_URL` | `DETECT_MODEL` |

CLI 支持对应的 `--ner-*` 与 `--embedding-*` 参数；未提供专用密钥或地址时继承主生成服务。表头识别仅用于网页上传阶段。
表头识别不可用时，网页会显示原因并暂用默认列；可手动改列并继续提取，人工确认只记忆到相同文件上下文。

> 安装、主工具、标注工具、Agent 操作与验收，见 [PM 使用指南](docs/PM使用指南_游戏术语提取与标注.html)。

## 工作流

```
原文.xlsx + 可选的已有术语表.xlsx
  ├─ 列自动识别（源文本列 / Key列 / 同行目标列 / 对应语言对术语表列）
  ├─ 人名/地名预识别（NER）
  ├─ LLM 术语提取（3轮投票，取3票一致项）
  ├─ 术语相似度匹配（Embedding；精确命中走术语表）
  └─ LLM 翻译（新术语）
        ├─ results.xlsx（原始结果，含调试字段）
        └─ 候选术语_模板.xlsx（8列标注模板，含来源行 Key，审核状态=未审核）
```

## 人工审校闭环（标注工具）

提取结果不是终点：`候选术语_模板.xlsx` 导入配套的 **[术语标注助手](annotation/README.md)**（`annotation/术语标注助手_v3.0.html`，浏览器双击即用）做人工标注审核，导出的审定术语表可直接回流本工具作为下次提取的术语表输入。

CLI 绑定源文件 Key 列用 `--key-col <列索引>`；UI 上传后自动识别，可手动改。

## 目录结构

```
├── core/
│   ├── main.py             # 流水线编排
│   ├── languages.py        # 语言代码注册与规范化
│   ├── llm_extractor.py    # 术语提取（3轮投票）
│   ├── llm_translator.py   # 术语翻译
│   ├── embed_store.py      # 向量库（SQLite + text-embedding-3-large）
│   ├── checkpoint.py       # 断点续跑
│   ├── header_detect.py    # 表头自动识别
│   └── prompt_base.py      # Prompt 构造
├── ui/
│   ├── app.py              # Streamlit 前端
│   └── ui_backend.py       # UI 适配层
├── annotation/             # 术语标注助手（人工审校工作台，单HTML）
├── scripts/                # 验证与评估脚本
├── profiles/               # 游戏配置文件（YAML）
├── database/               # 向量库缓存（自动生成）
├── output/                 # 运行结果（自动生成）
├── config.py               # 引擎运行参数（不存 API 密钥）
├── config_template.py      # 非敏感默认配置
├── run.bat                 # Windows 一键启动
├── setup.bat               # 自动安装 Python 环境
└── requirements.txt
```

## Profile 配置

每个游戏项目对应一个 `profiles/*.yaml`，控制提取规则、术语分类、翻译策略。

- 在 UI「设置 → 项目规则与性能」中上传 YAML 文件
- 或直接放入 `profiles/` 目录后重启

## 断点续跑

任务中断后重新上传相同文件、选相同 profile，UI 会自动检测断点并从上次位置继续。

## License

[MIT](LICENSE)
