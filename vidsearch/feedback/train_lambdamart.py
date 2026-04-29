from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vidsearch.feedback.train_ranker import _feedback_volume, _load_pairs, _training_composition


def train_lambdamart_contract(*, client_session_prefix: str | None, output_path: Path) -> dict[str, Any]:
    pairs = _load_pairs(client_session_prefix=client_session_prefix)
    volume = _feedback_volume(client_session_prefix=client_session_prefix)
    composition = _training_composition(pairs, rank1_weight=0.05) if pairs else {}
    try:
        import xgboost as xgb  # type: ignore  # noqa: F401

        xgboost_available = True
    except Exception:
        xgboost_available = False

    status = "blocked"
    reasons = []
    if not xgboost_available:
        reasons.append("xgboost is not installed in this environment")
    if not pairs:
        reasons.append("no eligible preference pairs available")
    if xgboost_available and pairs:
        status = "ready_for_training"

    artifact: dict[str, Any] = {
        "kind": "lambdamart_xgboost_contract",
        "status": status,
        "client_session_prefix": client_session_prefix,
        "objective": "rank:ndcg",
        "serving_enabled": False,
        "promotion_approved": False,
        "volume": volume,
        "training_composition": composition,
        "reasons": reasons,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return artifact | {"artifact": str(output_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="R2 LambdaMART/XGBoost training contract.")
    parser.add_argument("--client-session-prefix", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--objective", default="rank:ndcg")
    args = parser.parse_args()
    result = train_lambdamart_contract(
        client_session_prefix=args.client_session_prefix or None,
        output_path=Path(args.output),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
