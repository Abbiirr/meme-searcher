# R2 Target Split Summary

Group fields: `target_id, template_family, near_duplicate_cluster, language`

| Split | Targets | Languages | Template families |
| --- | ---: | --- | --- |
| `train` | `180` | bn:36, unknown:144 | bangla_pathetic_text_reaction:1, bangla_tv_warning_scene:1, template_unknown:178 |
| `val` | `45` | bn:10, unknown:35 | template_unknown:44, worried_tom_thinking:1 |
| `holdout` | `45` | bn:13, unknown:32 | bangla_tv_prayer_scene:1, template_unknown:43, this is fine:1 |

All rows for a target/group key are kept in one split. Raw JSONL artifacts remain under `artifacts/` and are not committed.
