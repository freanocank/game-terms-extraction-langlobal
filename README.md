# Game Terms Extraction Tool

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-app-ff4b4b.svg)](https://streamlit.io/)

English | [中文](README_ZH.md)

## Overview

 Game terminology extraction pipeline that uses an LLM to extract terms from Chinese game text and produce deliverable bilingual glossaries.

## Key Capabilities

- Identifies term candidates from game text.
- Generates English renderings and glossary fields.
- Outputs deliverable xlsx files.

## Usage

 Prepare source text according to the README input format and run the extraction scripts.

 PM setup, extraction, annotation, agent prompts, acceptance, and recovery: [PM Usage Guide (Chinese)](docs/PM使用指南_游戏术语提取与标注.html).

## Notes

 Terminology output should be reviewed by localization staff before delivery.

## Command and Configuration Reference

The following code blocks keep commands, paths, filenames, and configuration keys literal; explanatory comments are translated for the English README.

```bash
pip install -r requirements.txt
streamlit run ui/app.py

# CLI: the existing glossary is optional
python cli.py --source source.xlsx
python cli.py --source source.xlsx --glossary glossary.xlsx
```

## API Service Configuration

The Settings page exposes four OpenAI-compatible services: primary extraction/translation, NER pre-scan, Embedding, and Excel header detection. Auxiliary services inherit the primary key and Base URL by default, but each can use independent credentials, endpoint, and model.

API keys stay in process memory only. They are never written to `.ui_config.json`, checkpoints, logs, or exports. Configure them in the UI after startup or provide environment variables:

| Service | Key | Base URL | Model |
|---|---|---|---|
| Primary | `OPENAI_API_KEY` | `OPENAI_API_BASE` / `OPENAI_BASE_URL` | `OPENAI_MODEL` |
| NER | `NER_API_KEY` | `NER_API_BASE` / `NER_BASE_URL` | `NER_MODEL` |
| Embedding | `EMBEDDING_API_KEY` | `EMBEDDING_API_BASE` / `EMBEDDING_BASE_URL` | `EMBEDDING_MODEL` |
| Header detection | `DETECT_API_KEY` | `DETECT_API_BASE` / `DETECT_BASE_URL` | `DETECT_MODEL` |

The CLI also accepts matching `--ner-*` and `--embedding-*` options. Header detection is used only by the web upload flow.

```
source.xlsx + optional existing glossary.xlsx
  ├─ AI column auto-detection (source column / Key column / bilingual English column / glossary ZH-EN columns)
  ├─ NER pre-scan (names/places)
  ├─ LLM term extraction (3-round voting; keep unanimous items)
  ├─ vector embedding match (exact hits use glossary entries)
  └─ LLM translation (new terms)
        ├─ results.xlsx (raw results with debug fields)
        └─ 候选术语_模板.xlsx (8-column annotation template with source-row Key; review_status=unreviewed)
```

```
├── core/
│   ├── main.py             # pipeline orchestration
│   ├── llm_extractor.py    # term extraction (3-round voting)
│   ├── llm_translator.py   # term translation
│   ├── embed_store.py      # vector store (SQLite + text-embedding-3-large)
│   ├── checkpoint.py       # checkpoint and resume support
│   ├── header_detect.py    # header auto-detection
│   └── prompt_base.py      # prompt construction
├── ui/
│   ├── app.py              # Streamlit frontend
│   └── ui_backend.py       # UI adapter layer
├── annotation/             # term annotation assistant (human review workbench, single HTML)
├── scripts/                # validation and evaluation scripts
├── profiles/               # game profile files (YAML)
├── database/               # vector-store cache (generated)
├── output/                 # run outputs (generated)
├── config.py               # engine parameters (never stores API keys)
├── config_template.py      # non-secret defaults
├── run.bat                 # one-click Windows launcher
├── setup.bat               # automatic Python environment setup
└── requirements.txt
```
