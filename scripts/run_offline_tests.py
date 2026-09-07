#!/usr/bin/env python3
"""Run isolated offline regression scripts and retain first-run logs."""
import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    output = Path(args.output) if args.output else ROOT / "output" / f"tests_{datetime.now():%Y%m%d_%H%M%S_%f}"
    output.mkdir(parents=True, exist_ok=False)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("OPENAI_", "NER_", "EMBEDDING_", "EMBED_", "DETECT_"))}
    scripts = sorted((ROOT / "scripts").glob("test_*.py"))

    def run(script):
        try:
            process = subprocess.run([sys.executable, "-B", str(script)], cwd=ROOT, env=environment,
                                     capture_output=True, text=True, timeout=60)
            log = process.stdout + process.stderr
            status = process.returncode
        except subprocess.TimeoutExpired:
            log, status = "TIMEOUT after 60 seconds", 124
        (output / f"{script.stem}.log").write_text(log, encoding="utf-8")
        match = re.search(r"(\d+)/(\d+) passed", log)
        unittest_count = re.search(r"Ran (\d+) tests?", log)
        count = int(match[2]) if match else int(unittest_count[1]) if unittest_count else len(re.findall(r"^PASS \|", log, re.MULTILINE)) or None
        return {"script": script.name, "exit_code": status, "checks": count, "log": str(output / f"{script.stem}.log")}

    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(run, script) for script in scripts]):
            result = future.result()
            results.append(result)
            detail = f"{result['checks']} checks" if result["checks"] is not None else "assertion-based contract check"
            print(f"{'PASS' if result['exit_code'] == 0 else 'FAIL'} | {result['script']} | {detail}", flush=True)
    results.sort(key=lambda item: item["script"])
    (output / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Logs: {output}")
    return 0 if all(result["exit_code"] == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
