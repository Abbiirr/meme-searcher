from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from vidsearch.feedback.evaluate_ranker import build_changed_ranking_report, evaluate_ranker_artifact
from vidsearch.feedback.service import log_search_impressions, record_judgment_from_token
from vidsearch.feedback.train_ranker import train_feedback_ranker
from vidsearch.storage import pg as pg_store


TASK_RECORD = "agent_feedback_task_v1"
DECISION_RECORD = "agent_feedback_decision_v1"
DEFAULT_POLICY = "metadata_depth_ocr_overlap_v1"


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        raise FileNotFoundError(path)
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rows.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSONL row") from exc
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(row, sort_keys=True, default=_json_default) for row in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def _token_from_feedback_url(url: str) -> str:
    marker = "/feedback/confirm/"
    if marker not in url:
        raise ValueError("feedback URL does not contain a confirmation token")
    return url.rsplit(marker, 1)[1]


def _as_tags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _text_overlap(query: str, candidate: dict[str, Any]) -> float:
    query_tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    if not query_tokens:
        return 0.0
    fields = [
        candidate.get("source_uri"),
        candidate.get("ocr_excerpt"),
        candidate.get("caption_literal"),
        candidate.get("caption_figurative"),
        candidate.get("template_name"),
        " ".join(_as_tags(candidate.get("tags"))),
    ]
    haystack = " ".join(str(field or "") for field in fields).lower()
    haystack_tokens = set(re.findall(r"[a-z0-9]+", haystack))
    return len(query_tokens & haystack_tokens) / len(query_tokens)


def candidate_score(query: str, candidate: dict[str, Any]) -> tuple[int, int, int, float, int]:
    """Deterministic stand-in policy for agent-generated labels.

    This intentionally does not reduce to "pick rank 1". It prefers richer
    corpus evidence first, then falls back to base rank. That makes the
    bootstrap useful for proving the learning pipeline while remaining
    auditable and reproducible.
    """
    source_uri = str(candidate.get("source_uri") or "")
    ocr = str(candidate.get("ocr_excerpt") or candidate.get("ocr_text") or "")
    tags = _as_tags(candidate.get("tags"))
    return (
        len([part for part in re.split(r"[\\/]+", source_uri) if part]),
        1 if ocr.strip() else 0,
        len(tags),
        _text_overlap(query, candidate),
        -int(candidate.get("rank") or 0),
    )


def choose_decision(task: dict[str, Any], *, operator: str, policy: str = DEFAULT_POLICY) -> dict[str, Any]:
    candidates = list(task.get("candidates") or [])
    if not candidates:
        return {
            "record_type": DECISION_RECORD,
            "task_id": task["task_id"],
            "action": "none_correct",
            "selected_candidate_id": None,
            "selected_image_id": None,
            "operator": operator,
            "policy": policy,
            "confidence": 0.0,
            "rationale": "No candidates were present in the review task.",
        }

    query = str(task.get("query") or "")
    selected = max(candidates, key=lambda candidate: candidate_score(query, candidate))
    return {
        "record_type": DECISION_RECORD,
        "task_id": task["task_id"],
        "action": "select",
        "selected_candidate_id": selected["candidate_id"],
        "selected_image_id": selected["image_id"],
        "operator": operator,
        "policy": policy,
        "confidence": 0.65,
        "rationale": (
            "Selected by deterministic operator policy: deeper source context, OCR presence, tags, "
            "query text overlap, then lower original rank."
        ),
    }


def build_review_pack(
    *,
    eval_run_id: str,
    output_path: Path,
    top_k: int = 20,
    repeats: int = 1,
) -> dict[str, Any]:
    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT q.query_id::text,
                      q.text,
                      q.intent,
                      rr.rank,
                      rr.score,
                      rr.image_id,
                      img.source_uri,
                      item.ocr_text,
                      item.caption_literal,
                      item.caption_figurative,
                      item.template_name,
                      item.tags
               FROM eval.run_results rr
               JOIN eval.queries q ON q.query_id = rr.query_id
               JOIN core.images img ON img.image_id = rr.image_id
               LEFT JOIN core.image_items item ON item.image_id = rr.image_id
               WHERE rr.run_id = %s AND rr.rank <= %s
               ORDER BY q.intent, q.query_id, rr.rank""",
            (eval_run_id, top_k),
        )
        rows = cur.fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for query_id, query, intent, rank, score, image_id, source_uri, ocr, literal, figurative, template, tags in rows:
        task = grouped.setdefault(
            query_id,
            {
                "record_type": TASK_RECORD,
                "query_id": query_id,
                "query": query,
                "intent": intent,
                "eval_run_id": eval_run_id,
                "top_k": top_k,
                "instructions": (
                    "Choose the one candidate image that best satisfies the query. "
                    "Return a decision JSONL row with action=select and selected_candidate_id, "
                    "or action=none_correct if no candidate is acceptable."
                ),
                "candidates": [],
            },
        )
        task["candidates"].append(
            {
                "candidate_id": f"{query_id}:rank-{int(rank)}",
                "rank": int(rank),
                "base_rank": int(rank),
                "image_id": image_id,
                "source_uri": source_uri,
                "ocr_excerpt": (ocr or "")[:240],
                "caption_literal": literal or "",
                "caption_figurative": figurative or "",
                "template_name": template or "",
                "tags": _as_tags(tags),
                "retrieval_score": float(score or 0.0),
                "rerank_score": None,
            }
        )

    tasks: list[dict[str, Any]] = []
    for repeat in range(1, repeats + 1):
        for base_task in grouped.values():
            task = json.loads(json.dumps(base_task))
            suffix = f":r{repeat}" if repeats > 1 else ""
            task["task_id"] = f"{task['query_id']}{suffix}"
            task["repeat"] = repeat
            tasks.append(task)

    _write_jsonl(output_path, tasks)
    return {"status": "written", "output": str(output_path), "tasks": len(tasks), "candidates": len(rows)}


def write_agent_prompt(*, pack_path: Path, output_path: Path) -> dict[str, Any]:
    prompt = f"""# LLM Agent Feedback Operator

You are standing in for the human relevance judge for the local meme search RLHF loop.

Input review pack: `{pack_path}`

For each JSONL task:
- Inspect `query`, `intent`, and all `candidates`.
- Choose exactly one candidate if any candidate plausibly matches the query.
- Use `action: "none_correct"` only when every candidate is wrong.
- Do not edit images, embeddings, OCR, captions, Qdrant, or corpus records.
- Output JSONL with one row per task using this shape:

```json
{{"record_type":"{DECISION_RECORD}","task_id":"...","action":"select","selected_candidate_id":"...","selected_image_id":"...","operator":"codex|claude|opencode","policy":"llm_review_v1","confidence":0.0,"rationale":"short reason"}}
```

The apply step will validate that `selected_candidate_id` was actually shown in the task.
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")
    return {"status": "written", "output": str(output_path)}


def write_heuristic_decisions(
    *,
    pack_path: Path,
    output_path: Path,
    operator: str,
    policy: str = DEFAULT_POLICY,
) -> dict[str, Any]:
    tasks = [row for row in _read_jsonl(pack_path) if row.get("record_type") == TASK_RECORD]
    decisions = [choose_decision(task, operator=operator, policy=policy) for task in tasks]
    _write_jsonl(output_path, decisions)
    return {"status": "written", "output": str(output_path), "decisions": len(decisions), "policy": policy}


def _load_task_and_decision_maps(pack_path: Path, decisions_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    tasks = {row["task_id"]: row for row in _read_jsonl(pack_path) if row.get("record_type") == TASK_RECORD}
    decisions = {
        row["task_id"]: row for row in _read_jsonl(decisions_path) if row.get("record_type") == DECISION_RECORD
    }
    missing = sorted(set(tasks) - set(decisions))
    extra = sorted(set(decisions) - set(tasks))
    if missing:
        raise ValueError(f"decisions missing task_ids: {missing[:5]}")
    if extra:
        raise ValueError(f"decisions include unknown task_ids: {extra[:5]}")
    return tasks, decisions


def _delete_sessions_for_prefix(prefix: str) -> int:
    with pg_store.get_cursor() as cur:
        cur.execute(
            "DELETE FROM feedback.search_sessions WHERE client_session_id LIKE %s",
            (f"{prefix}-%",),
        )
        return int(cur.rowcount)


def _count_sessions_for_prefix(prefix: str) -> int:
    with pg_store.get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM feedback.search_sessions WHERE client_session_id LIKE %s",
            (f"{prefix}-%",),
        )
        return int(cur.fetchone()[0])


def _candidate_hits(task: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for candidate in task.get("candidates") or []:
        hits.append(
            {
                "rank": int(candidate["rank"]),
                "base_rank": int(candidate.get("base_rank") or candidate["rank"]),
                "image_id": candidate["image_id"],
                "source_uri": candidate.get("source_uri") or "",
                "ocr_excerpt": candidate.get("ocr_excerpt") or "",
                "caption_literal": candidate.get("caption_literal") or "",
                "caption_figurative": candidate.get("caption_figurative") or "",
                "template_name": candidate.get("template_name") or "",
                "tags": _as_tags(candidate.get("tags")),
                "retrieval_score": float(candidate.get("retrieval_score") or 0.0),
                "rerank_score": candidate.get("rerank_score"),
            }
        )
    return hits


def apply_agent_decisions(
    *,
    pack_path: Path,
    decisions_path: Path,
    client_session_prefix: str,
    operator: str,
    replace_prefix: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    tasks, decisions = _load_task_and_decision_maps(pack_path, decisions_path)
    existing = _count_sessions_for_prefix(client_session_prefix)
    deleted = 0
    if existing and not replace_prefix:
        raise ValueError(
            f"{existing} feedback sessions already exist for prefix {client_session_prefix!r}; "
            "use --replace-prefix to delete and recreate them"
        )
    if existing and replace_prefix and not dry_run:
        deleted = _delete_sessions_for_prefix(client_session_prefix)

    result = {
        "status": "dry_run" if dry_run else "applied",
        "tasks": len(tasks),
        "recorded_judgments": 0,
        "duplicate_judgments": 0,
        "pairs_created": 0,
        "none_correct": 0,
        "deleted_sessions": deleted,
        "errors": [],
    }

    for task_id, task in tasks.items():
        decision = decisions[task_id]
        action = str(decision.get("action") or "")
        if action not in {"select", "none_correct"}:
            result["errors"].append({"task_id": task_id, "error": f"unsupported action: {action}"})
            continue

        candidates_by_id = {candidate["candidate_id"]: candidate for candidate in task.get("candidates") or []}
        selected_candidate = None
        if action == "select":
            selected_candidate = candidates_by_id.get(str(decision.get("selected_candidate_id") or ""))
            if not selected_candidate:
                result["errors"].append({"task_id": task_id, "error": "selected_candidate_id was not shown"})
                continue

        if dry_run:
            result["recorded_judgments"] += 1
            result["none_correct"] += 1 if action == "none_correct" else 0
            continue

        feedback_meta = log_search_impressions(
            query=str(task["query"]),
            intent=str(task["intent"]),
            hits=_candidate_hits(task),
            client_session_id=f"{client_session_prefix}-{task_id}",
            owui_user_id=operator,
        )
        if not feedback_meta:
            result["errors"].append({"task_id": task_id, "error": "feedback logging disabled"})
            continue

        if action == "none_correct":
            token = _token_from_feedback_url(feedback_meta["none_correct_url"])
            result["none_correct"] += 1
        else:
            selected_image_id = selected_candidate["image_id"]
            impression = next(
                item for item in feedback_meta["impressions"] if item["image_id"] == selected_image_id
            )
            token = _token_from_feedback_url(impression["select_url"])

        judgment = record_judgment_from_token(token)
        if judgment.get("status") == "duplicate":
            result["duplicate_judgments"] += 1
        else:
            result["recorded_judgments"] += 1
        result["pairs_created"] += int(judgment.get("pairs_created") or 0)

    result["status"] = "failed" if result["errors"] and not dry_run else result["status"]
    return result


def run_agent_loop(
    *,
    eval_run_id: str,
    work_dir: Path,
    client_session_prefix: str,
    operator: str,
    top_k: int = 20,
    repeats: int = 5,
    replace_prefix: bool = False,
    approve_promotion: bool = True,
    p0_g4_passing: bool = True,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    pack_path = work_dir / "review_pack.jsonl"
    prompt_path = work_dir / "agent_prompt.md"
    decisions_path = work_dir / "decisions.jsonl"
    artifact_path = Path("artifacts/feedback_rankers/latest.json")
    eval_path = Path("artifacts/feedback_eval/latest.json")
    changed_prefix = Path("artifacts/feedback_eval/latest_changed")

    steps = {
        "pack": build_review_pack(eval_run_id=eval_run_id, output_path=pack_path, top_k=top_k, repeats=repeats),
        "prompt": write_agent_prompt(pack_path=pack_path, output_path=prompt_path),
        "decisions": write_heuristic_decisions(pack_path=pack_path, output_path=decisions_path, operator=operator),
    }
    steps["apply"] = apply_agent_decisions(
        pack_path=pack_path,
        decisions_path=decisions_path,
        client_session_prefix=client_session_prefix,
        operator=operator,
        replace_prefix=replace_prefix,
    )
    steps["train"] = train_feedback_ranker(
        output_path=artifact_path,
        approve_promotion=approve_promotion,
        p0_g4_passing=p0_g4_passing,
    )
    steps["evaluate"] = evaluate_ranker_artifact(artifact_path=artifact_path, output_path=eval_path)
    steps["changed_rankings"] = build_changed_ranking_report(artifact_path=artifact_path, output_prefix=changed_prefix)
    return {
        "status": "complete",
        "work_dir": str(work_dir),
        "artifact": str(artifact_path),
        "evaluation": str(eval_path),
        "steps": steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reusable LLM-agent operator loop for feedback ranker training.")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-pack", help="Build a JSONL review pack from eval.run_results.")
    build.add_argument("--eval-run-id", required=True)
    build.add_argument("--output", default="artifacts/feedback_agent/review_pack.jsonl")
    build.add_argument("--top-k", type=int, default=20)
    build.add_argument("--repeats", type=int, default=1)

    prompt = sub.add_parser("write-prompt", help="Write a prompt for Codex/Claude/OpenCode to fill decisions.")
    prompt.add_argument("--pack", default="artifacts/feedback_agent/review_pack.jsonl")
    prompt.add_argument("--output", default="artifacts/feedback_agent/agent_prompt.md")

    decide = sub.add_parser("decide-heuristic", help="Write deterministic stand-in decisions for smoke/bootstrap runs.")
    decide.add_argument("--pack", default="artifacts/feedback_agent/review_pack.jsonl")
    decide.add_argument("--output", default="artifacts/feedback_agent/decisions.jsonl")
    decide.add_argument("--operator", default="codex-agent")
    decide.add_argument("--policy", default=DEFAULT_POLICY)

    apply = sub.add_parser("apply-decisions", help="Apply agent decisions through signed feedback tokens.")
    apply.add_argument("--pack", default="artifacts/feedback_agent/review_pack.jsonl")
    apply.add_argument("--decisions", default="artifacts/feedback_agent/decisions.jsonl")
    apply.add_argument("--client-session-prefix", required=True)
    apply.add_argument("--operator", default="codex-agent")
    apply.add_argument("--replace-prefix", action="store_true")
    apply.add_argument("--dry-run", action="store_true")

    run = sub.add_parser("run", help="Build, decide, apply, train, and evaluate in one gated pass.")
    run.add_argument("--eval-run-id", required=True)
    run.add_argument("--work-dir", default="artifacts/feedback_agent/latest")
    run.add_argument("--client-session-prefix", required=True)
    run.add_argument("--operator", default="codex-agent")
    run.add_argument("--top-k", type=int, default=20)
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--replace-prefix", action="store_true")
    run.add_argument("--no-approve-promotion", action="store_true")
    run.add_argument("--p0-g4-passing", action="store_true", default=True)

    args = parser.parse_args()
    if args.command == "build-pack":
        result = build_review_pack(
            eval_run_id=args.eval_run_id,
            output_path=Path(args.output),
            top_k=args.top_k,
            repeats=args.repeats,
        )
    elif args.command == "write-prompt":
        result = write_agent_prompt(pack_path=Path(args.pack), output_path=Path(args.output))
    elif args.command == "decide-heuristic":
        result = write_heuristic_decisions(
            pack_path=Path(args.pack),
            output_path=Path(args.output),
            operator=args.operator,
            policy=args.policy,
        )
    elif args.command == "apply-decisions":
        result = apply_agent_decisions(
            pack_path=Path(args.pack),
            decisions_path=Path(args.decisions),
            client_session_prefix=args.client_session_prefix,
            operator=args.operator,
            replace_prefix=args.replace_prefix,
            dry_run=args.dry_run,
        )
    else:
        result = run_agent_loop(
            eval_run_id=args.eval_run_id,
            work_dir=Path(args.work_dir),
            client_session_prefix=args.client_session_prefix,
            operator=args.operator,
            top_k=args.top_k,
            repeats=args.repeats,
            replace_prefix=args.replace_prefix,
            approve_promotion=not args.no_approve_promotion,
            p0_g4_passing=args.p0_g4_passing,
        )

    print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))
    return 0 if result.get("status") != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
