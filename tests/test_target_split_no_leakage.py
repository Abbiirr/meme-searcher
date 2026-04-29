from __future__ import annotations

import json

from vidsearch.feedback.target_split import build_splits, read_jsonl


def test_target_split_keeps_duplicate_cluster_together(tmp_path):
    pack = tmp_path / "pack.jsonl"
    rows = [
        {"record_type": "target_image_task_v1", "target_id": "target-a", "near_duplicate_cluster": "cluster-1"},
        {"record_type": "target_image_task_v1", "target_id": "target-a-copy", "near_duplicate_cluster": "cluster-1"},
        {"record_type": "target_image_task_v1", "target_id": "target-b", "near_duplicate_cluster": "cluster-2"},
    ]
    pack.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    out = tmp_path / "splits"
    build_splits(
        pack_path=pack,
        output_dir=out,
        train_count=1,
        val_count=1,
        holdout_count=1,
        group_by=["near_duplicate_cluster"],
        summary_path=tmp_path / "summary.md",
    )

    split_names = {}
    for split in ("train", "val", "holdout"):
        for row in read_jsonl(out / f"{split}_pack.jsonl"):
            split_names[row["target_id"]] = split

    assert split_names["target-a"] == split_names["target-a-copy"]

