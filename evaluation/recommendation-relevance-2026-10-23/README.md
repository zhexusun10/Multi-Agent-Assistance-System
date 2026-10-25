# Historical 20-scenario recommendation evaluation

This is the **23 October `d5af7e1` run**, with 20 scenarios and the original
577 frozen scenario/listing judgments. Use the [current QA handoff](../README.md)
for the later 40-scenario evaluation; do not combine the two versions' scores.
This archive retains K=1, 3, 4, 5, 6, 7, 8, 9 and 10 analyses,
including the later K=6–9 calculations. In this
older run, K above the four displayed items used saved internal rankings; those
cutoffs were not ten-item UI responses.

| Material | Purpose |
| --- | --- |
| [Original scenario/preparation ZIP](Group42_Recommendation_Relevance_2026-10-23.zip) | Requests, candidates, rating guidance and source manifest |
| [Original rating ZIP](Group42_AI_Assisted_Ratings_2026-10-23.zip) | Preserved pre-scoring and review material |
| Seven `Group42_Offline_Evidence_Rev3.zip.part00*` files | The complete original 220-file execution/metrics/report archive |
| [Parts manifest](evidence_parts.json) | Ordered filenames, lengths and SHA256 hashes |
| [Restore utility](restore_evidence.py) | Verify the parts or reconstruct the original ZIP |

From this directory, check the seven parts without writing a new archive:

```sh
python3 restore_evidence.py --verify-only
```

Run `python3 restore_evidence.py` to reconstruct the ZIP in `restored/`.
The PDF, workbook, metrics, rankings, ratings and human-review template remain
inside that archive. [The preservation manifest](../CLEANUP_MANIFEST_EN.json)
maps every removed loose copy to its exact member. The parts and restore
utility are unchanged; the original archive is 128,892,483 bytes with SHA256
`c987a84e6d0f8b8065a4861cd333ff89e3bc173a8c3dd375c8141f62abf5e397`.
