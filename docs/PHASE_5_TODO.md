# PHASE_5_TODO.md — Hardening, multi-GPU, runbook

**Version:** 2026-04-18
**Blocks on:** Phase 4 gates P4-G1…G5 closed.
**See:** `PHASE_5_PLAN.md` for rationale and gates.

---

## P5.0 — Pre-flight

- [ ] Phase 4 exit sign-off.
- [ ] Snapshot Phase 4 live baseline into `docs/decision_log.md` (used as the Phase 5 floor).
- [ ] Decide GPU count target: stay on 4060 Ti only, or second GPU acquired? Record the decision.

## P5.1 — Backups (unlocks P5-G1)

- [ ] `infra/backups/pg_dump.sh` — hourly dump to MinIO with the naming scheme in `PHASE_5_PLAN.md` §4.1.
- [ ] `infra/backups/qdrant_snapshot.sh` — nightly snapshot per alias; uploaded to MinIO.
- [ ] `infra/backups/restic_policy.json` — weekly restic of MinIO, configs, secrets.
- [ ] `docker-compose.backups.yml` — profile container that runs the schedules.
- [ ] Retention enforcement: `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12`.
- [ ] Optional: mount external drive; `mc mirror` runs daily.
- [ ] **TEST:** integration — dry-run backup lands files in MinIO with expected naming.
- [ ] **TEST:** retention purge removes older snapshots past the policy.

## P5.2 — Restore drill (unlocks P5-G1)

- [ ] Write `docs/disaster_recovery.md` — the step-by-step drill.
- [ ] Execute drill on a scratch `docker compose` project:
  - [ ] Restore Postgres from latest dump.
  - [ ] Restore Qdrant from latest snapshot.
  - [ ] Restore MinIO from restic.
  - [ ] Run Phase 3 sampled eval; `nDCG@10` within 1 pp of live.
- [ ] Time the restore; document the result.
- [ ] **GATE:** restore completes in documented time; sampled eval passes.

## P5.3 — Chaos test (unlocks P5-G2)

- [ ] Draft `docs/chaos_test.md` with one row per service.
- [ ] For each service in `{postgres, redis, minio, qdrant, litellm, prefect-server, prefect-worker, api, open-webui}`:
  - [ ] Normal operation confirmed.
  - [ ] `docker compose kill <service>` during live query + ingest.
  - [ ] Observed failure mode recorded (graceful?).
  - [ ] Observed recovery recorded (time-to-green).
- [ ] Any service that does not recover gracefully → file a fix; do not close P5-G2 until all green.
- [ ] **GATE:** every service has a green row.

## P5.4 — Multi-GPU split (conditional; unlocks P5-G1)

- [ ] If a second GPU is present:
  - [ ] Extend `docker-compose.gpu.yml` with per-service device IDs gated on `GPU_COUNT`.
  - [ ] Two Prefect work pools: `ingest-pool` (GPU 0), `serve-pool` (GPU 1).
  - [ ] Update `make ingest-mode` / `make serve-mode` to be no-ops when `GPU_COUNT=2` (both run concurrently).
  - [ ] `docs/multigpu_split.md` documents the policy.
- [ ] If no second GPU:
  - [ ] Document the deferral in `docs/decision_log.md`; Phase 5 still exits.
- [ ] **TEST:** with `GPU_COUNT=2`, ingest and `/search` latency dashboards show independence.

## P5.5 — Reranker LoRA loop (unlocks P5-G5)

- [ ] `vidsearch/flows/rerank_lora.py` weekly Prefect flow per `PHASE_5_PLAN.md` §6.
- [ ] Training dataset hashes `query_text` before JSONL write (PII guard).
- [ ] Registration writes a new row in `ops.model_versions`; candidate tag distinct from incumbent.
- [ ] Promotion rule: aggregate `nDCG@10` +1 pp AND no intent class regresses by more than 1 pp.
- [ ] **TEST:** integration — synthetic feedback events produce a JSONL training set of the expected shape.
- [ ] **TEST:** integration — a fine-tune run registers a candidate and triggers the sampled eval.
- [ ] **GATE:** one weekly cycle completed with either promotion or documented rejection.

## P5.6 — Helm chart (unlocks P5-G4)

- [ ] `infra/helm/vidsearch/` Chart skeleton (Chart.yaml, values.yaml, templates per service).
- [ ] `helm lint` clean.
- [ ] Deploy to local kind or k3d cluster; confirm services healthy.
- [ ] Document the compose → Helm conversion recipe in `docs/helm_conversion.md`.
- [ ] **TEST:** `helm install` to a scratch cluster, run `/health` against the API pod.
- [ ] **TEST:** delete the release; resources clean up.
- [ ] **GATE:** lint + local deploy documented.

## P5.7 — 30-day uptime simulation (unlocks P5-G3)

- [ ] Simulator script `scripts/uptime_simulator.py` that drives 50 video ingests/day + 1,000 search calls/day.
- [ ] Log rotation through the 30-day window.
- [ ] Grafana dashboard: queue depth, GPU utilisation, error rates, cost burn.
- [ ] **TEST:** intermediate — at day 7, 14, 21, nothing requires manual intervention.
- [ ] **GATE:** day 30 reached with zero manual interventions. Log in `docs/uptime_log.md`.

## P5.8 — Delete / purge workflow (unlocks P5-G6)

- [ ] Document `/image/{id}` and a new `/video/{id}` DELETE endpoint.
- [ ] Tombstone row written in `ops.purges` with reason + operator id.
- [ ] Removal from Postgres, Qdrant, MinIO (original + thumbnails + keyframes + transcripts).
- [ ] Log retention: delete audit preserved for one year.
- [ ] **TEST:** integration — delete a video; all artifacts removed; tombstone row present.

## P5.9 — Runbook + handoff (unlocks P5-G6)

- [ ] `docs/runbook.md` expanded: every `make` target documented; every Prefect flow described; every Grafana dashboard linked; every on-call alert has a response procedure.
- [ ] `docs/handoff.md` — a day-1 / week-1 / month-1 onboarding for a new operator.
- [ ] `docs/credential_rotation.md` — how to rotate LiteLLM master key, provider keys, MinIO credentials, Postgres password, OWUI secret.
- [ ] `docs/cost_tracking.md` — where to see spend; who to alert when a monthly budget threshold is crossed.

## P5.10 — Regression (cross-gate)

- [ ] Phase 0 meme eval.
- [ ] Phase 1 video eval.
- [ ] Phase 2 caption-lift eval.
- [ ] Phase 3 100-query eval.
- [ ] Phase 4 entity-heavy eval.
- [ ] Phase 4 CCTV eval.
- [ ] **GATE:** all suites pass within Phase 3 regression tolerance (3 pp).

---

## Cross-cutting rules

- [ ] No new models, no new eval queries, no new retrieval modes in Phase 5.
- [ ] No destructive operation leaves the stack without a backup fewer than 1 hour old.
- [ ] Every Phase 5 change runs the full regression suite before merge.

## Exit checklist (mirrors `PHASE_5_PLAN.md` §9–§10)

- [ ] Backups live + restore drill documented.
- [ ] Chaos test all-green per service.
- [ ] 30-day uptime simulation passed.
- [ ] Helm chart lints + deploys locally.
- [ ] Reranker LoRA one cycle closed.
- [ ] Multi-GPU split implemented or deferred with documented reason.
- [ ] Runbook, disaster recovery, handoff docs complete.
- [ ] Phase 0–4 eval suites pass.
- [ ] POC declared operationally robust; ready for handoff.
