# CHANGELOG — manuscript v1.2 (2026-10-27)

Base: `manuscript_v1.1.md` (2026-10-27, submission candidate after v1.1 benchmark integration).
Output: `manuscript_v1.2.md` (same structure; Sections 2.1–2.13, 3.1–3.5, 4, 5, Tables 1–7).

## Why v1.2 exists

v1.1 quoted the discovery-configuration scores obtained in September 2026 against a reference
library snapshot (`databases/virusref_db/kv_index`) that has since been **deleted from the
repository**. That snapshot also lacked the TSWV L/M/S segments, which is why the headline
figure was 20/28 (71.4%). Two consequences made a fresh version necessary rather than optional:

1. The primary sensitivity number could not be re-derived from a library a reader can obtain.
2. `docs/DEVELOPMENT_NOTES` of the September runs showed that `run_one.py` resolved the
   annotation table from a hard-coded path (`KV_INFO`, same deleted directory), so the harness
   could not be pointed at another library at all.

Both were fixed in the benchmark harness (see "Harness fix" below), the discovery
configuration was re-run against the **delivered** library build (2026-10-19 rebuild:
12,196 reference records, TSWV L/M/S included), and every number that depends on it was
replaced. Raw material: `docs/paper/benchmark_rerun_20261027/` (`manifest.json`,
`results.json`, `score.json`, `compare.md`, `README.md`, `artifacts/`, `logs/`).

## Headline change

| Quantity | v1.1 (2026-09 snapshot) | v1.2 (delivered build) | Source |
|---|---|---|---|
| Discovery, Datasets 1–10, species level | 20/28 (71.4%), precision 32.8%, FP 41 | **23/28 (82.1%)**, precision 30.7%, FP 52 | `score.json` → `salmon.d110` |
| Discovery, Datasets 11–18 | 8/8 (100%), precision 62% | 8/8 (100%), precision 80% | `score.json` → `salmon.d1118` |
| Discovery, total | — | **31/36 (86.1%)**, precision 36.5% | `score.json` → `salmon.total` |
| Throughput, discovery, 18 datasets | 127.4 s / 233.6 MB (D1, salmon) | 8.4 min for 17 datasets; D1 90.1 s / 349.5 MB; two 1 GB single-end sets ~1.27 GB | `results.json` → `peak_rss_mb`, `driver_wall_s` |
| minibwa MAE upper bound | 1.54 pp (text) vs 1.78 pp (Table) | **1.78 pp** | `benchmark/results/metrics_summary.csv`, D16 row |

Per-dataset moves (new − old, true positives): D3 1→2, D7 0→1 (TSWV), D8 1→2; all other
datasets unchanged (D1 5/5, D2 5/5, D4 4/5, D5 1/1, D6 1/1, D9 1/1, D10 1/2, D11–D18 1/1 each).
D1 and D2 false positives rose 9→13 each (precision 35.7% → 27.8%), i.e. the larger library buys
sensitivity with precision; the paper states this trade-off rather than presenting only the gain.

## Line-level edits (all in `manuscript_v1.2.md`)

1. **File header** — added a v1.2 paragraph: what was re-derived, why, and where the archive lives.
2. **Abstract, Results** — "…recovered 20 of 28 species-level truths (71.4%)…" →
   "…recovered 23 of 28 species-level truths (82.1%)… measured against the delivered reference
   build (the 2026-09 snapshot, which lacked the TSWV segments, had scored 20 of 28)".
3. **Abstract, Conclusions** — "71.4% species-level" → "82.1% species-level against the
   delivered reference build".
4. **§2.13** — the archive sentence now names `docs/paper/benchmark_rerun_20261027/` alongside
   `docs/paper/benchmark/`.
5. **§2.13, discovery-configuration definition** — "shipped default library (8,464 references)"
   → "shipped default library (12,196 records in the delivered build)".
6. **§3.3, opening** — added: the 2026-09 library no longer exists, the configuration was
   re-run against the delivered build, the re-run supersedes the snapshot numbers, and the
   snapshot is retained only to document the library-coverage effect.
7. **§3.3, discovery detection paragraph** — rewritten around 23/28 (precision 30.7%) and 8/8
   (precision 80%); the 20/28 snapshot run is retained as the contrast; mechanism attributed to
   library coverage (D7 TSWV 0/1→1/1, D8 1/2→2/2, D3 1/5→2/5); FP rise 9→13 (D1) and 41→52
   (D1–10) stated; alignment engine explicitly flagged as **not** re-run.
8. **§3.3, quantification** — minibwa MAE range "0.00–1.54" → "0.00–1.78" pp (Dataset 16).
   Cause of the old text/table mismatch: the 1.54 in the prose was a stale maximum; the
   per-isolate table (`benchmark/results/metrics_summary.csv`) always carried 1.78 for D16.
9. **Table 4** — discovery rows updated (23/28, precision 30.7%; 8/8, precision 80%);
   row label now says "delivered build"; new footnote † marks the alignment-engine figures as
   snapshot values.
10. **Table 5** — minibwa clean-mixture MAE upper bound 1.54 → 1.78.
11. **§3.3, throughput** — added the re-run cost (8.4 min for 17 datasets; D1 90.1 s / 349.5 MB;
    ~1.27 GB on the two large single-end sets), with the explicit note that peak memory rose
    with reference-library size while wall time was lower than in the earlier snapshot.
12. **§3.3, end-to-end chain** — added that identification alone now reaches the same 23/28 and
    31/36, so the gap the chain previously exposed was library coverage, not stage coupling.
13. **§4, Limitations (second and third points)** — discovery figure updated to 23/28 against
    the delivered build with 20/28 named as the earlier snapshot; "name the configuration" →
    "name both the configuration and the library build"; TSWV framed as direct evidence that
    curation, not tuning, sets discovery sensitivity; FP rise 41→52 added as the counterweight.
14. **§4, Limitations (fourth point)** — narrowed: the discovery configuration is now fully
    archived (input SHA-256, library fingerprints, key outputs, row-for-row reproduction of the
    archived metrics); what remains unarchived is the strain-level/consensus suites and the
    reference-present and alignment-engine discovery runs.
15. **§5, Conclusion** — "which reference configuration each number belongs to" →
    "…and, in the discovery configuration, which library build".
16. **Availability** — public GitHub repository URL + tag range + MIT license + third-party
    notices; Dryad DOI added for the VIROMOCK datasets (`doi:10.5061/dryad.0zpc866z8`, verified
    against DataCite: 15 creators, 2021, 14,614,047,052 bytes, CC0-1.0); benchmark_rerun archive
    added; license bullet replaced.
17. **Reference-count clarification (6 spots)** — the v1.0 build is **8,464** references;
    the delivered build is **12,196** records (45.8 MB; 2026-10-19 rebuild; TSWV L/M/S added
    2026-09-19). Abstract (ii), Introduction contribution 2, §2.3.1, §2.13, §3.2 alignment
    benchmark, and the Figure 2 legend now state which build each figure refers to.

## Harness fix (code, not prose)

`docs/paper/benchmark/run_one.py:158` — `info = load_ref_info()` (no argument) always read the
hard-coded, now-deleted `databases/virusref_db/kv_index/reference.ref_info.tsv`, so any
`db_dir` override raised `FileNotFoundError`. Changed to `load_ref_info(kv_info)` and commented
with the date. Without this the re-run cannot be pointed at the delivered library; with it the
same harness produced both the September and the October numbers, which is what makes the
comparison in §3.3 meaningful.

## Two clarifications that affect how numbers may be quoted

- **MAE is configuration-specific.** The paper's 0.00–1.78 pp describes the **reference-present**
  configuration (`viromock_kv`, 61 sequences, every target present). In the discovery
  configuration (12,196 references) MAE is larger by construction — e.g. D13 3.08, D15 4.19,
  D16 2.65 pp — because same-species neighbours share reads (D15: `NC_025389.1` absorbs 6,080 of
  48,504 reads of a target designed at 25.02%, observed 12.54%). The two must never be mixed.
- **Library growth has a cost.** 12,196 vs 8,464 records recovers +3 species-level truths but
  adds 11 false positives across Datasets 1–10. Reviewers will ask whether the library was
  enlarged after seeing the benchmark; the manuscript answers that the additions were
  TSWV/strain-level records made on 2026-09-19 for reasons documented in the build manifest,
  and that the re-run was triggered by the snapshot's deletion, not by score-hunting.

## Reproduce

```
cd D:\桌面\植物病毒分析平台
python docs\paper\benchmark_rerun_20261027\rerun.py --engine salmon
python docs\paper\benchmark_rerun_20261027\score.py
```

`score.py` first re-scores the archived September `results.json` and asserts row-for-row
agreement with `docs/paper/benchmark/results/metrics_summary.csv` (35/35 rows) before scoring
new runs — so a change in scoring logic fails loudly instead of silently shifting the headline.

## Still open (author decisions)

1. minibwa discovery configuration not re-run (≈2 h); its Table 4 cells carry the snapshot values with †.
2. Reference-present configuration not re-run against the delivered library; Table 5/6 numbers
   remain from `viromock_kv` runs.
3. D3 CEVd-like 99.81% ANI / 41,132 reads claim exists only in the strain-level draft narrative
   (`viromock_benchmark_manuscript_EN.md`); no structured artefact in `benchmark/results/`.
4. Bari "21-laboratory mean 76%" should be re-read against Massart et al. 2019 before submission.
5. Which reference count the paper should headline (8,464 v1.0 build vs 12,196 delivered build)
   is now stated explicitly in all six places, but the author may prefer to consolidate on one.
