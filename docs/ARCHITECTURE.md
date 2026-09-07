# Architecture

Historical architecture reference: some examples below predate the current implementation. For the 2026-09-07 snapshot, use `reliability_fix_20260907.md` and `RELEASE_NOTES_20260907.md` for current behavior and known limitations.

## Module Map

```
cli.py                   ← CLI 入口（argparse）
ui/app.py                ← 前端入口（Streamlit）→ ui/ui_backend.py 编排
core/main.py             ← 引擎库，对外暴露 run_pipeline / extract_terms（无 __main__）
  │
  ├── profiles/<game>.yaml   ← 项目配置（每个项目一个文件）
  ├── config.py              ← 引擎参数（并发、超时、投票温度）
  ├── prompt_base.py         ← prompt 构造（系统 + 用户 + 翻译）
  ├── llm_extractor.py       ← LLMExtractor（异步批量提取）
  ├── llm_translator.py      ← TermTranslator（批量翻译）
  ├── embed_store.py         ← EmbedStore（SQLite 向量库，本地 bge-m3）
  ├── checkpoint.py          ← 断点续跑
  └── logger.py              ← 日志配置
```

---

## Public Interfaces

### `main.py`

#### `run_pipeline` — 完整工作流入口
```python
run_pipeline(
    source_path: str,            # 源文本 Excel
    glossary_path: str,          # 术语表 Excel
    profile_name: str = "yanyun",
    api_key: str = "",
    base_url: str = "https://api.openai.com/v1",
    model: str = "gemini-3.1-pro-preview",
    output_dir: str = "",
    raw_dir: str = "",
    checkpoint_dir: str = "",
    progress_callback: callable = None,
    opts: PipelineOpts = PipelineOpts(),  # 语言对、源结构、译文策略、通用列映射与并发
) -> List[dict]
```
返回术语列表，每项：
```json
{
  "term": "墨门",
  "category": "门派势力",
  "source_text": "青长老说墨门弟子需恪守门规",
  "source_key": "DLG_001",
  "translation": "Momen",
  "match_type": "exact | aligned | bilingual | llm_translated | no_translate",
  "note": "（可选）同行目标列与术语表冲突时记录同行原值",
  "ref_term": "...",
  "ref_trans": "...",
  "ref_sim": 0.92
}
```

#### `results_to_template_df` / `save_outputs` — 标注模板导出
```python
results_to_template_df(results: List[dict], timestamp: str = "") -> pd.DataFrame
# 8 列模板（TEMPLATE_COLUMNS）：Key值/术语分类/术语原文/术语译文/备注/来源原文/审核状态/最新修订时间

save_outputs(results: List[dict], out_dir) -> Tuple[Path, Path]
# 同时落盘 results.xlsx + 候选术语_模板.xlsx，CLI 与 UI 共用
```

#### `extract_terms` — 仅提取，不翻译
```python
extract_terms(
    texts: List[str],
    profile: dict,
    api_key: str,
    base_url: str,
    model: str,
    target_chars: int = 1200,    # 每个 chunk 的目标字符数
    max_tokens: int = None,      # 默认读 EXTRACTOR_CONFIG
    concurrent: int = None,
    output_dir: str = "",
    raw_dir: str = "",
    checkpoint_dir: str = "",
    glossary_keys: set = None,
) -> List[dict]
```
返回 `[{"term": str, "category": str, "source_text": str}, ...]`

#### `match_and_translate` — 仅翻译
```python
match_and_translate(
    extracted: List[dict],
    glossary: dict,              # {source_lower: (source, target)}
    profile: dict,
    api_key: str,
    base_url: str,
    model: str,
    raw_dir: str = "",
    embed_store: EmbedStore = None,
    bilingual: bool = False,
    source_language: str = "zh-Hans",
    target_language: str = "en",
    translate_missing: bool = True,
) -> List[dict]
```

#### `load_config` / `load_glossary`
```python
load_config(profile_name: str) -> dict
load_glossary(path: str) -> Tuple[dict, set]   # (glossary_dict, keys_set)
```

#### `filter_derived_terms`
```python
filter_derived_terms(
    terms: List[dict],
    address_suffixes: list,      # 从 profile["address_suffixes"] 读取
    glossary_keys: set = None,
) -> List[dict]
```
过滤称谓派生变体（如冯大哥→冯），glossary 中的词豁免。

---

### `LLMExtractor`（llm_extractor.py）

```python
extractor = LLMExtractor(
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    base_dir: str = "output",    # API 日志目录
    cache_dir: str = "",         # 请求级缓存目录（空=禁用）
)
```

**主要方法：**

```python
# 异步（在已有 event loop 内调用）
await extractor.run_batch_async(
    texts: List[str],
    system_prompt: str,
    user_prompts: List[str],
    model: str,
    temperature: float,
    max_tokens: int,
    max_concurrent: int,
    source_files: List[str],
    batch_id: str = "",
) -> List[dict]

# 同步封装（会自己 asyncio.run）
extractor.process_batch_concurrent(
    texts, system_prompt, user_prompts, model,
    temperature, max_tokens, max_concurrent, source_files, batch_id,
) -> List[dict]
```

每个结果项：
```json
{
  "custom_id": "b0_v0_t0",
  "extracted_terms": {"terms": [...]},
  "usage": {"total_tokens": 1200, "prompt_tokens": 900, "completion_tokens": 300},
  "model": "...",
  "source_file": "c0",
  "created": 1716123456
}
```

---

### `TermTranslator`（llm_translator.py）

```python
translator = TermTranslator(
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o",
    profile: dict = None,
)

translator.translate_with_hints(
    system_prompt: str,
    user_prompt: str,
) -> Dict[str, str]   # {"墨门": "Momen", ...}
```

---

### `prompt_base.py`

```python
build_system_prompt(
    profile: dict,
    source_language: str = "zh-Hans",
) -> str

build_user_prompt(
    profile: dict,
    text: str,
    include_context: bool = True,
    bilingual: bool = False,       # 同行双语文本（如 EN: ... | DE: ...）
    jieba_hints: list = None,      # 术语表命中提示词
    ner_hints: dict = None,        # {"persons": [...], "places": [...]}
    source_language: str = "zh-Hans",
    target_language: str = "en",
) -> str

build_translation_prompt(
    profile: dict,
    terms_with_ref: list,          # 每项含 _ref_term / _ref_trans / source_text
    source_language: str = "zh-Hans",
    target_language: str = "en",
) -> tuple[str, str]               # (system_prompt, user_prompt)
```

---

### `EmbedStore`（embed_store.py）

```python
store = EmbedStore(
    db_path: str,                  # SQLite 文件路径
    model: str = "E:/huggingface/models/bge-m3",
    device: str = "cpu",
)

store.is_built -> bool             # 库是否已建好
store.count -> int                 # 已存词条数

store.build(terms: list, batch_size: int = 256)   # 构建/增量更新
store.search(queries: list) -> list[tuple[str, float]]  # [(最近邻词, 相似度), ...]
```

---

### `checkpoint.py`

```python
task_id(source_path: str, profile: str) -> str   # 由文件哈希生成，用作 checkpoint 目录名
load(checkpoint_dir: str) -> dict                # {"chunk_idx": int, "terms": list, "total_chunks": int}
save(checkpoint_dir: str, chunk_idx: int, terms: list, total_chunks: int)
clear(checkpoint_dir: str)                       # 任务完成后清除
```

---

### `config.py`（常量）

```python
VOTE_TEMPS = [0, 0.3, 0.7]        # 三次投票的温度

EXTRACTOR_CONFIG = {
    "max_tokens": 65536,
    "max_concurrent": 10,
    "timeout": 120.0,
    "max_retries": 3,
}

TRANSLATOR_CONFIG = {
    "temperature": 0.1,
    "max_tokens": 65536,
    "max_concurrent": 10,
    "timeout": 120.0,
    "max_retries": 3,
}

get_token_param_name(model: str) -> str   # "max_tokens" or "max_output_tokens"
```

---

## Profile 字段参考（YAML）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `game_type` | str | — | 注入 system prompt 的游戏类型描述；默认 `"游戏"` |
| `term_categories` | list[str] | — | LLM 可用的全部分类名；默认 `[]`（prompt 中无分类约束） |
| `task_description` | str | — | 任务标签，仅作文档说明，代码不读取 |
| `filterable_categories` | list[str] | — | 提取后可过滤的分类（通用/噪音类）；默认 `[]`，不过滤 |
| `address_suffixes` | list[str] | — | 称谓后缀（如大哥、嫂子），用于去派生变体；默认 `[]`，不过滤 |
| `extraction_notes` | list[str] | — | 追加到 user prompt 的补充规则 |
| `extract_examples.include` | list[str] | — | system prompt 中的正例 |
| `extract_examples.exclude` | list[str] | — | system prompt 中的负例 |
| `fewshot_examples` | list[dict] | — | `{input, output}` 格式的 few-shot 样本 |
| `fewshot_examples_bilingual` | list[dict] | — | 默认中→英同行双语的兼容 few-shot（output 含 zh_term/eng_term）；其他语言对优先使用下方通用字段 |
| `translation_rules` | list[str] | — | 翻译风格规则，注入翻译 prompt |
| `core_principle_by_source_language` | dict[str, str] | — | 非默认源语言的核心提取原则 |
| `extract_examples_by_source_language` | dict[str, dict] | — | 按源语言配置提取正反例 |
| `extraction_notes_by_source_language` | dict[str, list[str]] | — | 按源语言配置补充提取规则 |
| `fewshot_examples_by_language_pair` | dict[str, list[dict]] | — | 按 `en->de` 等语言对配置 few-shot |
| `translation_rules_by_language_pair` | dict[str, list[str]] | — | 按语言对配置翻译规则 |
| `rule_extractors.surname_names` | dict | — | 基于姓氏的规则抽取（燕云专用） |
| `core_principle` | str | — | 追加到 system prompt 开头的核心原则 |

---

## Data Flow

```
source.xlsx（源语言列；可选同行目标语言列）
  │
  ├─ load_excel → List[str] (去重、去变量占位符)
  ├─ _chunk_texts → List[chunk]  (每块 ~1200 字)
  │
  ├─ 每批 chunk（10并发）:
  │     ├─ NER Flash Lite → persons / places
  │     ├─ rule_extractors → rule_persons
  │     ├─ build_user_prompt (含 ner_hints + jieba_hints)
  │     └─ LLMExtractor × 3投票 (temp=0/0.3/0.7)
  │           → 3/3 投票通过 → 候选术语
  │
  ├─ filter_derived_terms (去除称谓派生变体)
  ├─ profile.filterable_categories 过滤（非术语库中的通用分类）
  │
  ├─ 统一译文解析链:
  │     ① glossary 精确命中 → exact（与同行目标列冲突时取术语表、同行原值写 note）
  │     ② 能在同行目标单元格中核对的术语译文 → aligned（默认中→英兼容值为 bilingual）
  │     ③ translate_missing=True：EmbedStore top-1 + LLM 翻译 → llm_translated
  │        translate_missing=False：无法可靠对应的项目留空 → no_translate
  │
  └─ save_outputs → results.xlsx + 候选术语_模板.xlsx（8列，审核状态=未审核，note→备注）
```

---

## CLI

```bash
# 完整流水线（CLI 入口为根目录 cli.py，调用 core.main.run_pipeline）
python cli.py \
  --source test_file/原文.xlsx \
  --glossary test_file/【日更】0514术语表.xlsx \
  --profile yanyun \
  --api-key sk-... \
  --base-url https://api.vectorengine.ai/v1 \
  --model gemini-3.1-pro-preview \
  --output output/run_20260520

# 英语→德语同行双语；按行号对齐，目标列优先于 AI 补译
python cli.py --source 双语原文.xlsx --glossary 术语表.xlsx \
  --source-lang en --target-lang de --profile xiuxiu \
  --aligned-target --src-col 0 --src-target-col 1 --api-key sk-...

# 不调用 AI 翻译；仍沿用术语表精确译文及同行目标列，其余留空
python cli.py --source 双语原文.xlsx --glossary 术语表.xlsx \
  --source-lang en --target-lang de --aligned-target \
  --src-col 0 --src-target-col 1 --no-translate --api-key sk-...

# 评估（提取 + P/R/F1 对比人工术语表）
python eval.py \
  --source test_file/原文.xlsx \
  --manual test_file/人工术语_cleaned.xlsx \
  --profile yanyun
```
