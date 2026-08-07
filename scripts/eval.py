#!/usr/bin/env python3
import os, sys, time, argparse
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from datetime import datetime
import pandas as pd

from core.checkpoint import checkpoint_dir_name, clear as ckpt_clear

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config_template import DEFAULT_API_BASE, DEFAULT_EMBEDDING_MODEL, DEFAULT_MODEL, DEFAULT_NER_MODEL, DEFAULT_PROFILE
from core.main import PipelineOpts, run_pipeline


def _env(*names):
    return next((os.getenv(name, "").strip() for name in names if os.getenv(name, "").strip()), "")

T = ROOT / "test_file"

parser = argparse.ArgumentParser()
parser.add_argument("--source", default=str(T / "原文.xlsx"))
parser.add_argument("--manual", default=str(T / "人工术语_cleaned.xlsx"))
parser.add_argument("--profile", default=DEFAULT_PROFILE)
parser.add_argument("--api-key", default=_env("OPENAI_API_KEY"))
parser.add_argument("--base-url", default=_env("OPENAI_API_BASE", "OPENAI_BASE_URL") or DEFAULT_API_BASE)
parser.add_argument("--model", default=_env("OPENAI_MODEL") or DEFAULT_MODEL)
parser.add_argument("--ner-api-key", default=_env("NER_API_KEY"))
parser.add_argument("--ner-base-url", default=_env("NER_API_BASE", "NER_BASE_URL"))
parser.add_argument("--ner-model", default=_env("NER_MODEL") or DEFAULT_NER_MODEL)
parser.add_argument("--embedding-api-key", default=_env("EMBEDDING_API_KEY", "EMBED_API_KEY"))
parser.add_argument("--embedding-base-url", default=_env("EMBEDDING_API_BASE", "EMBEDDING_BASE_URL", "EMBED_API_BASE", "EMBED_BASE_URL"))
parser.add_argument("--embedding-model", default=_env("EMBEDDING_MODEL", "EMBED_MODEL") or DEFAULT_EMBEDDING_MODEL)
args = parser.parse_args()

if not args.api_key:
    parser.error("缺少主生成服务 API 密钥：请传 --api-key 或设置 OPENAI_API_KEY")

SOURCE = args.source
MANUAL = Path(args.manual)
PROFILE = args.profile
GLOSSARY = str(T / "【日更】0514术语表.xlsx")

t0 = time.time()
tid = checkpoint_dir_name(Path(SOURCE).read_bytes(), PROFILE)
RUN_ID = f"{datetime.now().strftime('%Y%m%d_%H%M')}_{PROFILE}"
CKPT = str(ROOT / "output" / "_checkpoints" / tid)
OUT = ROOT / "output" / "runs" / RUN_ID
OUT.mkdir(parents=True, exist_ok=True)

opts = PipelineOpts(
    ner_use_main=not bool(args.ner_api_key or args.ner_base_url),
    ner_api_key=args.ner_api_key or args.api_key,
    ner_base_url=args.ner_base_url or args.base_url,
    ner_model=args.ner_model,
    embedding_use_main=not bool(args.embedding_api_key or args.embedding_base_url),
    embedding_api_key=args.embedding_api_key or args.api_key,
    embedding_base_url=args.embedding_base_url or args.base_url,
    embedding_model=args.embedding_model,
)
results = run_pipeline(
    SOURCE, GLOSSARY, PROFILE, args.api_key, args.base_url, args.model,
    output_dir=str(OUT), checkpoint_dir=CKPT, raw_dir=str(OUT / "raw_data"), opts=opts,
)
ckpt_clear(CKPT)

df_man = pd.read_excel(MANUAL)
manual = {}
for _, row in df_man.iterrows():
    s = str(row[df_man.columns[0]]).strip()
    if s and s != "nan":
        manual[s.lower()] = s

ext_set = set(t["term"].lower() for t in results)
man_set = set(manual.keys())
tp = ext_set & man_set
fn = man_set - ext_set
fp = ext_set - man_set

p = len(tp) / len(ext_set) * 100 if ext_set else 0
r = len(tp) / len(man_set) * 100 if man_set else 0
f1 = 2 * p * r / (p + r) if p + r > 0 else 0

exact = sum(1 for t in results if t.get("match_type") == "exact")
hint = sum(1 for t in results if t.get("match_type") == "llm_translated")

# ===== Against full glossary =====
glossary_keys = set()
df_gl = pd.read_excel(GLOSSARY)
for _, row in df_gl.iterrows():
    k = str(row.iloc[0]).strip()
    if k and k != "nan":
        glossary_keys.add(k.lower())
gl_hit = ext_set & glossary_keys
gl_cov = len(gl_hit) / len(ext_set) * 100 if ext_set else 0

print(f"\n{'='*60}")
print(f"  vs 人工术语表 ({len(man_set)} terms)")
print(f"{'='*60}")
print(f"  Precision: {p:.1f}%  Recall: {r:.1f}%  F1: {f1:.1f}%")
print(f"  Hit: {len(tp)}  Missed: {len(fn)}  Noise: {len(fp)}")

print(f"\n{'='*60}")
print(f"  vs 全量术语表 ({len(glossary_keys)} terms)")
print(f"{'='*60}")
print(f"  Coverage: {gl_cov:.1f}% ({len(gl_hit)}/{len(ext_set)})")
print(f"  Exact match: {exact}  LLM translated: {hint}")

with open(OUT / "report.txt", "w", encoding="utf-8") as f:
    f.write(f"=== vs 人工术语表 ===\n")
    f.write(f"Precision: {p:.1f}% | Recall: {r:.1f}% | F1: {f1:.1f}%\n\n")
    f.write(f"HITS ({len(tp)}):\n")
    for t in sorted(tp):
        f.write(f"  {manual[t]}\n")
    f.write(f"\nMISSED ({len(fn)}):\n")
    for t in sorted(fn):
        f.write(f"  {manual[t]}\n")
    f.write(f"\nNOISE ({len(fp)}):\n")
    for t in sorted(fp):
        f.write(f"  {t}\n")
    f.write(f"\n=== vs 全量术语表 ===\n")
    f.write(f"Coverage: {gl_cov:.1f}% ({len(gl_hit)}/{len(ext_set)})\n")
    f.write(f"Exact match: {exact} | LLM translated: {hint}\n")

pd.DataFrame(results).to_excel(OUT / "results.xlsx", index=False)
print(f"Report: {OUT / 'report.txt'}")
print(f"Total: {(time.time()-t0)/60:.1f}m")
