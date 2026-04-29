from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else f"_Missing local summary: `{path}`_\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_r2_report(
    *,
    prompt_summary: Path,
    judge_summary: Path,
    bucket_summary: Path,
    post_verify: Path,
    output_path: Path,
) -> dict[str, Any]:
    verify = _read_json(post_verify)
    promotion_ready = bool(verify.get("promotion_ready", False))
    lines = [
        "# R2 RLAIF-MemeRank Final Report",
        "",
        "Experiment ID: `rlaif-memerank-r2`",
        "",
        "Serving decision: learned ranker remains disabled unless every promotion gate passes.",
        "",
        "## Prompt Balance",
        "",
        _read_text(prompt_summary),
        "## Judge Audit",
        "",
        _read_text(judge_summary),
        "## Rank Buckets",
        "",
        _read_text(bucket_summary),
        "## Post-RLAIF Verification",
        "",
        f"Promotion ready: `{promotion_ready}`",
        "",
    ]
    if verify:
        lines.extend(
            [
                "| Metric | Base | Learned | Delta |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for metric, base_value in sorted((verify.get("base_metrics") or {}).items()):
            learned_value = (verify.get("learned_metrics") or {}).get(metric)
            delta = (verify.get("deltas") or {}).get(metric)
            lines.append(f"| `{metric}` | `{base_value}` | `{learned_value}` | `{delta}` |")
        lines.extend(["", "Promotion gates:", ""])
        for gate, value in sorted((verify.get("promotion_gates") or {}).items()):
            lines.append(f"- `{gate}`: `{value}`")
    lines.extend(
        [
            "",
            "## Paper Table Rows",
            "",
            "Populate this section from summarized artifacts only. Raw `artifacts/` JSONL files are intentionally gitignored.",
            "",
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "written", "output": str(output_path), "promotion_ready": promotion_ready}


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble committed R2 markdown report from summarized artifacts.")
    parser.add_argument("--prompt-summary", required=True)
    parser.add_argument("--judge-summary", required=True)
    parser.add_argument("--bucket-summary", required=True)
    parser.add_argument("--post-verify", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build_r2_report(
        prompt_summary=Path(args.prompt_summary),
        judge_summary=Path(args.judge_summary),
        bucket_summary=Path(args.bucket_summary),
        post_verify=Path(args.post_verify),
        output_path=Path(args.output),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
