# PHASE_5_PLAN.md — Hardening, multi-GPU, backups, runbook

**Version:** 2026-04-18
**Status:** Blocked on Phase 4 exit (gates P4-G1…G5 closed)
**Upstream:** `PHASE_4_PLAN.md`, `../ARCHITECTURE.md` §25 (Phase 5)
**Downstream:** completes the POC

---

## 1. Purpose

Phase 5 hardens the POC so it can run for weeks unattended, recover cleanly from crashes, and be handed off. Three deliverables:

1. **Operational robustness** — chaos-tested, backup-proven, restore-proven.
2. **Multi-GPU split** — the single-GPU constraint is optional after this phase; ingest and serving can live on separate cards.
3. **Runbook + handoff artifacts** — `docs/runbook.md` covers every common operator task; Helm chart exists so the same compose maps one-to-one onto Kubernetes.

No new retrieval or evaluation work. Phase 5 is strictly about operations.

## 2. Scope

### In scope
- `restic` (or equivalent) backups for Postgres, MinIO, Qdrant.
- Tested restore drills on a scratch stack.
- Hourly `pg_dump` → MinIO with retention; nightly Qdrant snapshots → MinIO; daily MinIO mirror to an external drive (if available).
- Second GPU wiring (if hardware becomes available): `ingest` card vs `serve` card; two Prefect work pools with GPU affinity.
- Helm chart conversion of the compose stack (`infra/helm/vidsearch/`).
- Feedback-driven reranker LoRA fine-tune loop (`vidsearch/flows/rerank_lora.py`).
- Chaos test: kill each service sequentially, confirm graceful recovery.
- 30-day uptime simulation with synthetic ingest and query load.
- Operator handoff docs: runbook, disaster recovery, on-call playbook, credential rotation, cost tracking dashboards.
- Delete / purge workflow documented (legal / privacy requirement awareness).
- Optional PITR (point-in-time recovery) for Postgres via `pg_basebackup` + WAL archiving (if second disk available).

### Explicitly out of scope
- New models.
- New eval queries (Phase 3 is the last eval-growth phase).
- New retrieval modes.
- Multi-tenant auth.

## 3. Architecture delta from Phase 4

### Added
- Backup services: `restic` container for Postgres + MinIO; Qdrant snapshot cron.
- `vidsearch/flows/rerank_lora.py` — weekly fine-tune, register winner in `ops.model_versions`.
- `infra/helm/vidsearch/` — Helm chart (values file, templates for every service).
- `docs/chaos_test.md` — procedure + last-run report.
- `docs/handoff.md` — onboarding for the next operator.
- `infra/prometheus/` dashboards for GPU utilisation, queue depth, cost burn rates.

### Changed
- `docker-compose.gpu.yml` gains a second NVIDIA device binding guarded by a `GPU_COUNT` env var.
- Prefect work pools split: `ingest-pool` (GPU 0), `serve-pool` (GPU 1) if two GPUs present; otherwise fall back to the single-GPU discipline.

## 4. Backup + restore design

### 4.1 Targets
- **Postgres:** hourly `pg_dump` to `minio://backups/postgres/<YYYY-MM-DD>/<HH>.sql.gz`; daily consolidated; monthly archive.
- **MinIO:** daily `mc mirror` to an external drive (if mounted); weekly `restic` snapshot including all buckets.
- **Qdrant:** nightly `qdrant snapshot` per collection alias; uploaded to MinIO backups bucket.
- **Configs + secrets:** `.env`, `infra/litellm/config.yaml`, Prometheus config, Grafana dashboards → `restic` snapshot.

### 4.2 Restore drill (part of exit criteria)
1. Wipe a scratch stack.
2. Restore Postgres from the latest `pg_dump`.
3. Restore Qdrant from the latest snapshot.
4. Restore MinIO from `restic`.
5. Run the Phase 3 sampled eval; confirm `nDCG@10` within 1 pp of the live stack.
6. Time the full restore; document in `docs/disaster_recovery.md`.

### 4.3 Retention
- Postgres dumps: 7 days hourly, 30 days daily, 12 months monthly.
- Qdrant snapshots: 7 days nightly.
- `restic` snapshots: 30-day retention with `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12`.

## 5. Multi-GPU split (conditional)

- Only executed if a second GPU is acquired. If not, Phase 5 still proceeds on a single GPU with the `ingest-mode` / `serve-mode` discipline intact.
- Two Prefect work pools with NVIDIA device affinity via `NVIDIA_VISIBLE_DEVICES`.
- `ingest-pool` runs Lane A heavy work (Parakeet, BGE-M3, SigLIP-2, PP-OCRv5).
- `serve-pool` runs Lane B (if a VLM was promoted) + vLLM for synthesis fallback.
- `docker-compose.gpu.yml` gains conditional device IDs based on `GPU_COUNT`.

## 6. Reranker LoRA loop

### 6.1 Design
- Weekly flow `rerank_lora.py`:
  1. Join `feedback.events` with `eval.run_results` to build pairwise preferences.
  2. Emit JSONL training set under `data/feedback_training/<YYYY-MM-DD>.jsonl`.
  3. Run LoRA fine-tune of `jina-reranker-v2-base-multilingual` (or whichever is incumbent).
  4. Register as a candidate in `ops.model_versions`.
  5. Run the Phase 3 sampled eval against the candidate.
  6. Promote only if `nDCG@10` improves by ≥ 1 pp aggregate and no intent class regresses by more than 1 pp.

### 6.2 Safety
- Training runs happen in `serve-mode` windows only.
- A failed LoRA never replaces the incumbent.
- Training sets are kept for six months to retrace decisions.

## 7. Chaos + uptime drills

### 7.1 Chaos test
- For each service in `{postgres, redis, minio, qdrant, litellm, prefect-server, prefect-worker, api, open-webui}`:
  1. Observe normal operation.
  2. `docker compose kill <service>` during active query + active ingest.
  3. Confirm graceful failure (user-visible error or queued retry, no silent data loss).
  4. Confirm recovery when the service returns.
- Document pass/fail per service in `docs/chaos_test.md`.

### 7.2 30-day uptime simulation
- Synthetic ingest load: 50 videos/day delivered to `inbox/`.
- Synthetic query load: 1,000 `/search` calls/day distributed throughout UTC.
- Caption backfill running continuously.
- Grafana dashboards capture: queue depth, GPU utilisation, error rates, cost burn.
- Pass criterion: no manual intervention required for 30 consecutive days.

## 8. Test strategy

### 8.1 Unit
- Backup filename generator is deterministic and sortable.
- LoRA data exporter produces the expected JSONL schema on a fixture feedback set.

### 8.2 Integration
- Scheduled backup job fires in a dry-run and produces an upload.
- LoRA candidate registers in `ops.model_versions` and runs the Phase 3 sampled eval.
- `docker compose kill` each service; recovery within documented bound.

### 8.3 End-to-end
- Restore drill on a scratch stack; Phase 3 sampled eval passes.
- 30-day simulation passes with no intervention.

### 8.4 Regression eval
- Phase 0–4 eval suites all continue to pass on the Phase 5 build.

## 9. Verification criteria

| Criterion | Target |
|---|---|
| Backup coverage | Postgres, MinIO, Qdrant, configs; all tested |
| Restore drill | < 2-hour restore documented; Phase 3 sampled eval within 1 pp post-restore |
| Chaos test | All services recover; failure modes documented |
| 30-day uptime simulation | Passes without manual intervention |
| Helm chart | Lints clean; can deploy to a local `kind` or `k3d` cluster |
| Reranker LoRA | One successful weekly cycle (candidate either promoted or rejected per the rule) |
| Multi-GPU split | Proven if hardware available; deferred with a documented reason otherwise |
| Runbook | Every common task has a documented procedure |

## 10. Closing gates

- **P5-G1 — Backups live + tested.** Scheduled jobs running; one restore drill completed.
- **P5-G2 — Chaos test pass.** `docs/chaos_test.md` has a green row for every service.
- **P5-G3 — 30-day uptime.** Simulation green; `docs/uptime_log.md` updated.
- **P5-G4 — Helm chart.** `helm lint` clean; deploy to a local kind cluster verified.
- **P5-G5 — Reranker LoRA.** One weekly cycle completed end-to-end (candidate outcome either way).
- **P5-G6 — Runbook + handoff.** `docs/runbook.md`, `docs/disaster_recovery.md`, `docs/handoff.md` all complete and reviewed.
- **P5-G7 — No regressions.** Phase 0–4 eval suites all pass.

## 11. Dependencies

- Phase 4 closed.
- External disk mounted (for MinIO mirror) — optional but preferred.
- Second GPU (optional).
- NGC or Kubernetes environment for the Helm chart deploy test.

## 12. Rollback / abort triggers

- Restore drill fails → block Phase 5 exit; treat as a production bug and resolve before declaring done.
- Multi-GPU split introduces scheduling bugs → revert to single-GPU; document gap and move on.
- Helm chart lints fail in a way that reveals a compose design issue → fix compose first, then Helm.
- LoRA pipeline produces a consistently-worse reranker → disable the weekly loop; keep data collection for later analysis.

## 13. Deliverables

- `docker-compose.backups.yml` (profile).
- `infra/backups/restic_policy.json`, `pg_dump.sh`, `qdrant_snapshot.sh`.
- `vidsearch/flows/rerank_lora.py`.
- `infra/helm/vidsearch/` Helm chart.
- `docs/runbook.md` expanded.
- `docs/disaster_recovery.md`.
- `docs/chaos_test.md`.
- `docs/handoff.md`.
- `docs/uptime_log.md`.
- Optional: `docs/multigpu_split.md`.

## 14. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Restore drill reveals a schema divergence | High | Caught before POC handoff; fix before P5-G1 closes |
| 30-day simulation masked by cached provider responses | Medium | LiteLLM cache keyed by input hash; rotate caches weekly during the simulation |
| LoRA dataset leaks feedback PII via query text | Medium | Hash `query_text` in training set; raw text never leaves `feedback.events` |
| External disk unavailable | Low | MinIO mirror optional; backups still land in MinIO itself |
| Helm chart drifts from compose | Medium | Chart generated from compose via a documented conversion recipe; re-verified on Phase 5 exit |
| Second GPU acquisition delay | Low | Multi-GPU path is documented as conditional; not a hard gate |
| LoRA runs during active user traffic | Medium | Strict `serve-mode` windows; Prefect schedules enforce |

## 15. Exit sentence

Phase 5 is **done** when the operator can lose any single service, restore from a backup, and return to the last green `nDCG@10` within two hours — and when a 30-day unattended simulation passes without manual intervention.
