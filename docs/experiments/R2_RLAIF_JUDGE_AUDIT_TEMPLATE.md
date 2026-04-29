# R2 RLAIF Judge Audit Template

Use this template to manually audit sampled AI labels. Keep raw samples in `artifacts/`; commit only summarized results.

| Prompt ID | Target ID | AI Label | Human Label | Agreement | Notes |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

Required summary metrics:

- AI-human agreement.
- False-positive target-found rate.
- False-negative target-found rate.
- Position consistency across randomized candidate order.
- Uncertain rate.
- Near-duplicate disagreement rate.
