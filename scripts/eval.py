#!/usr/bin/env python3
"""Score native results offline, or explicitly run the native CLI before scoring."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import cli
from core.request_cache import write_json
from core.evaluation import score_terms, replay_extraction


def build_parser():
    parser = cli.build_parser(source_required=False)
    parser.description = "术语评估：默认不调用 API；--live 显式使用原生 CLI 流程"
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--results", help="已有原生 results.xlsx、标注模板或 JSON 结果")
    mode.add_argument("--live", action="store_true", help="调用 API 提取后评分，所有处理参数与 CLI 相同")
    mode.add_argument("--replay", help="离线重放本版本 raw_data/extraction 目录中的完整三轮响应；不调用 NER 或补译")
    parser.add_argument("--manual", required=True, help="人工审核过的完整标准术语表 xlsx")
    parser.add_argument("--manual-term-col", type=cli._column_index, default=0)
    parser.add_argument("--manual-target-col", type=cli._column_index, default=None,
                        help="标准译文列；指定后另外计算译文准确率")
    return parser


def load_results(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        results = json.loads(path.read_text(encoding="utf-8"))
    else:
        frame = pd.read_excel(path).fillna("")
        frame = frame.rename(columns={"术语原文": "term", "术语译文": "translation", "术语分类": "category"})
        results = frame.to_dict("records")
    if not isinstance(results, list) or any(not isinstance(r, dict) or not isinstance(r.get("term"), str) for r in results):
        raise ValueError("结果必须包含 term/术语原文 文本列")
    return results


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    manual = pd.read_excel(args.manual).fillna("")
    for column in (args.manual_term_col, args.manual_target_col):
        if column is not None and column >= len(manual.columns):
            parser.error("人工标准表列索引超出范围")
    gold = [
        {"term": str(row.iloc[args.manual_term_col]).strip(),
         "translation": str(row.iloc[args.manual_target_col]).strip() if args.manual_target_col is not None else None}
        for _, row in manual.iterrows() if str(row.iloc[args.manual_term_col]).strip()
    ]
    if not gold:
        parser.error("人工标准术语表为空，不能计算召回率")
    output = Path(args.output or f"output/evaluation_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1000000}")
    if args.live:
        if not args.source:
            parser.error("--live 必须提供 --source")
        args.output = str(output)
        args.checkpoint = args.checkpoint or str(output / "_checkpoint")
        results = cli.run_with_args(args, parser)
    elif args.replay:
        if not args.source:
            parser.error("--replay 必须提供原始 --source 及相同列映射")
        results = replay_extraction(args.replay, args.source, args.glossary, args.profile, cli.options_from_args(args))
        from core.main import save_outputs
        save_outputs(results, output, profile_name=args.profile, source_path=args.source)
    else:
        results = load_results(args.results)
    report = score_terms(results, gold)
    mode = "live" if args.live else "offline-response-replay" if args.replay else "offline-results"
    report.update({"mode": mode,
                   "manual": str(Path(args.manual).resolve()), "results": args.results,
                   "source_language": args.source_language, "target_language": args.target_language,
                   "scope": "相对于完整人工标准表的词条集合评分；不是模型置信度或语言对通用成绩"})
    write_json(output / "report.json", report)
    summary = (
        f"Mode: {report['mode']}\n"
        f"Precision: {report['precision']:.2%} | Recall: {report['recall']:.2%} | F1: {report['f1']:.2%}\n"
        f"TP: {report['tp']} | FP: {report['fp']} | FN: {report['fn']}\n"
        f"结果词数: {report['predicted_count']} | 标准词数: {report['gold_count']}\n"
        "仅适用于人工标准表完整覆盖的范围；不完整/失败的在线任务不会产生成功评分。\n"
    )
    (output / "report.txt").write_text(summary, encoding="utf-8")
    print(summary + f"Report: {output / 'report.json'}")
    return report


if __name__ == "__main__":
    main()
