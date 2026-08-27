#!/usr/bin/env python3
"""命令行入口。前端入口为 ui/app.py，两者共用 core.main.run_pipeline。"""
import os, time, argparse, tempfile
from pathlib import Path

from core.main import PipelineInterruptedError, empty_glossary_bytes, run_pipeline, PipelineOpts, save_outputs
from core.checkpoint import checkpoint_dir_name, clear as clear_checkpoint
from core.languages import (
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    validate_language_pair,
)
from config_template import (
    DEFAULT_API_BASE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBED_WORKERS,
    DEFAULT_MODEL,
    DEFAULT_NER_MODEL,
    DEFAULT_PROFILE,
)


ROOT = Path(__file__).parent


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须为大于 0 的整数")
    return parsed


def _column_index(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("列索引不能小于 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="游戏术语提取 CLI")
    parser.add_argument("--source", required=True, help="源文件 xlsx")
    parser.add_argument("--glossary", default="", help="已有双语术语表 xlsx（可选；不传则直接提取并翻译）")
    parser.add_argument("--source-language", "--source-lang", default=DEFAULT_SOURCE_LANGUAGE,
                        help=f"源语言 BCP 47 风格代码 (default: {DEFAULT_SOURCE_LANGUAGE})")
    parser.add_argument("--target-language", "--target-lang", default=DEFAULT_TARGET_LANGUAGE,
                        help=f"目标语言 BCP 47 风格代码 (default: {DEFAULT_TARGET_LANGUAGE})")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help=f"配置名 (default: {DEFAULT_PROFILE})")
    parser.add_argument("--api-key", default=_first_env("OPENAI_API_KEY"),
                        help="主生成服务密钥（建议通过 OPENAI_API_KEY 设置）")
    parser.add_argument("--base-url", default=_first_env("OPENAI_API_BASE", "OPENAI_BASE_URL") or DEFAULT_API_BASE,
                        help=f"主生成服务地址 (default: {DEFAULT_API_BASE})")
    parser.add_argument("--model", default=_first_env("OPENAI_MODEL") or DEFAULT_MODEL,
                        help=f"提取与翻译模型 ID (default: {DEFAULT_MODEL})")
    parser.add_argument("--ner-api-key", default=_first_env("NER_API_KEY"),
                        help="NER 服务密钥（可选；留空继承主生成服务）")
    parser.add_argument("--ner-base-url", default=_first_env("NER_API_BASE", "NER_BASE_URL"),
                        help="NER 服务地址（可选；留空继承主生成服务）")
    parser.add_argument("--ner-model", default=_first_env("NER_MODEL") or DEFAULT_NER_MODEL,
                        help=f"NER 模型 ID (default: {DEFAULT_NER_MODEL})")
    parser.add_argument("--embedding-api-key", default=_first_env("EMBEDDING_API_KEY", "EMBED_API_KEY"),
                        help="Embedding 服务密钥（可选；留空继承主生成服务）")
    parser.add_argument(
        "--embedding-base-url",
        default=_first_env("EMBEDDING_API_BASE", "EMBEDDING_BASE_URL", "EMBED_API_BASE", "EMBED_BASE_URL"),
        help="Embedding 服务地址（可选；留空继承主生成服务）",
    )
    parser.add_argument("--embedding-model", default=_first_env("EMBEDDING_MODEL", "EMBED_MODEL") or DEFAULT_EMBEDDING_MODEL,
                        help=f"Embedding 模型 ID (default: {DEFAULT_EMBEDDING_MODEL})")
    parser.add_argument("--output", default="", help="输出目录（默认按运行时间生成）")
    parser.add_argument("--checkpoint", default="",
                        help="断点目录（默认按源文件和项目规则自动生成）")
    parser.add_argument("--src-col", type=_column_index, default=0, help="源文本列索引")
    parser.add_argument("--key-col", type=_column_index, default=None,
                        help="源文件 Key/ID 列索引（可选，填充导出模板的 Key值 列）")
    parser.add_argument("--gl-cn-col", type=_column_index, default=0, help="术语表原文列索引")
    parser.add_argument("--gl-en-col", type=_column_index, default=1, help="术语表译文列索引")
    parser.add_argument("--gl-source-col", type=_column_index, default=None,
                        help="术语表源术语列索引；优先于兼容参数 --gl-cn-col")
    parser.add_argument("--gl-target-col", type=_column_index, default=None,
                        help="术语表目标译文列索引；优先于兼容参数 --gl-en-col")
    parser.add_argument("--bilingual", "--aligned-target", dest="bilingual", action="store_true",
                        help="源文件含同行目标文本，抽取时直接沿用对应目标术语")
    parser.add_argument("--src-en-col", type=_column_index, default=1,
                        help="兼容参数：同行英文列索引")
    parser.add_argument("--src-target-col", type=_column_index, default=None,
                        help="同行目标文本列索引；优先于兼容参数 --src-en-col")
    parser.add_argument("--no-translate", action="store_true",
                        help="跳过 LLM 翻译，仅提取术语（同行目标模式仍照抄目标列）")
    parser.add_argument("--no-flag-unsourced", action="store_true",
                        help="不给原文未命中的词在结果 note 字段打⚠待核标记（默认标记，不删除）")
    parser.add_argument("--concurrent", type=_positive_int, default=None, help="AI 提取并发数")
    parser.add_argument("--max-tokens", type=_positive_int, default=None, help="单次响应 Token 上限")
    parser.add_argument("--embed-workers", type=_positive_int, default=DEFAULT_EMBED_WORKERS, help="向量编码并发数")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    source_path = Path(args.source)
    if not source_path.is_file():
        parser.error(f"源文件不存在：{source_path}")
    if source_path.suffix.lower() != ".xlsx":
        parser.error("源文件必须为 .xlsx")
    if args.glossary:
        glossary_path = Path(args.glossary)
        if not glossary_path.is_file():
            parser.error(f"术语表不存在：{glossary_path}")
        if glossary_path.suffix.lower() != ".xlsx":
            parser.error("术语表必须为 .xlsx")
    if not (ROOT / "profiles" / f"{args.profile}.yaml").is_file():
        parser.error(f"项目规则不存在：{args.profile}")
    if not args.api_key:
        parser.error("缺少 API 密钥：请传 --api-key 或设置 OPENAI_API_KEY")
    try:
        source_language, target_language = validate_language_pair(
            args.source_language, args.target_language
        )
    except ValueError as exc:
        parser.error(str(exc))

    t0 = time.time()
    out_dir = Path(args.output or f"output/run_{time.strftime('%Y%m%d_%H%M%S')}")
    raw_dir = out_dir / "raw_data"
    ckpt_dir = args.checkpoint or f"output/_checkpoints/{checkpoint_dir_name(source_path.read_bytes(), args.profile, source_language, target_language)}"

    opts = PipelineOpts(
        source_language=source_language, target_language=target_language,
        bilingual=args.bilingual, no_translate=args.no_translate,
        src_col=args.src_col, src_en_col=args.src_en_col,
        src_target_col=args.src_target_col, key_col=args.key_col,
        gl_cn_col=args.gl_cn_col, gl_en_col=args.gl_en_col,
        gl_source_col=args.gl_source_col, gl_target_col=args.gl_target_col,
        embed_workers=args.embed_workers,
        max_concurrent=args.concurrent, max_tokens=args.max_tokens,
        flag_unsourced=not args.no_flag_unsourced,
        ner_use_main=not bool(args.ner_api_key or args.ner_base_url),
        ner_api_key=args.ner_api_key or args.api_key,
        ner_base_url=args.ner_base_url or args.base_url,
        ner_model=args.ner_model,
        embedding_use_main=not bool(args.embedding_api_key or args.embedding_base_url),
        embedding_api_key=args.embedding_api_key or args.api_key,
        embedding_base_url=args.embedding_base_url or args.base_url,
        embedding_model=args.embedding_model,
    )
    def _run(glossary_path: Path):
        return run_pipeline(
            str(source_path), str(glossary_path), args.profile,
            args.api_key, args.base_url, args.model,
            output_dir=str(out_dir), raw_dir=str(raw_dir), checkpoint_dir=ckpt_dir,
            opts=opts,
        )

    if args.glossary:
        results = _run(Path(args.glossary))
    else:
        with tempfile.TemporaryDirectory(prefix="game_terms_cli_") as temp_dir:
            glossary_path = Path(temp_dir) / "_empty_reference_glossary.xlsx"
            glossary_path.write_bytes(empty_glossary_bytes(source_language, target_language))
            results = _run(glossary_path)

    res_path, tpl_path = save_outputs(results, out_dir, profile_name=args.profile, source_path=args.source)
    clear_checkpoint(ckpt_dir)
    print(f"\nDone. {len(results)} terms. Output: {out_dir} ({res_path.name} + {tpl_path.name})")
    print(f"Total: {(time.time()-t0)/60:.1f}m")
    return results


if __name__ == "__main__":
    try:
        main()
    except PipelineInterruptedError:
        print("\n任务已中断，断点已保留；使用相同参数重新运行即可继续。")
        raise SystemExit(130)
