# Proofreading Report — VirusPlatform manuscript (revised v2 → final)

*执行依据：academic-proofreader 技能六项检查（语言/术语/公式/图表/引用/格式）。润色对象：manuscript_revised_v2.md；产出：manuscript_final.md。*

## Critical Issues

None found.（无会误导读者或损害可信度的错误。初稿中"7/10 isolates removed"的旧代码注释值已在修订阶段以实测值 6/10 覆盖——见 revision_response.md R-2。）

## Major Suggestions (applied)

| # | Location | Issue | Fix applied |
|---|---|---|---|
| M1 | §1 para. 2 | Overgeneralization: "none is operable by laboratory personnel without a compute cluster" — Kraken 2 runs on desktops | Softened to "none of them is packaged for non-expert, offline desktop use" |
| M2 | §3.2 | Counting mismatch: "we report two measurements" followed by three measurement blocks | Changed to "we report the following measurements" |
| M3 | §2.9 | Attribution imprecision: "China National GeneBank's NGDC GSA" conflates organization and database | Reworded to "the National Genomics Data Center's Genome Sequence Archive (GSA; NGDC)" |

## Minor/Stylistic Issues (applied)

| # | Location | Issue | Fix applied |
|---|---|---|---|
| N1 | Abstract | Final result sentence wordy | Tightened; CMV abbreviation introduced at first use |
| N2 | §1 para. 1 | Awkward trailing clause "with continuing methodological advances … more generally" | Reworded to "and plant viral diagnostics continue to advance methodologically" |
| N3 | §2.2 | ORF used without expansion at first body-text use | Expanded: "open reading frame (ORF)" |
| N4 | §2.3.1 | ICTV used without expansion at first use | Expanded: "International Committee on Taxonomy of Viruses (ICTV)" |
| N5 | §2.3.3 | SNV used without expansion | Expanded: "single-nucleotide variants (SNVs)" |
| N6 | §2.6 | SDT used without expansion; CJK unexplained | Expanded both: "Sequence Demarcation Tool (SDT)"; "Chinese/Japanese/Korean (CJK)" |
| N7 | §2.9 | SRA used without expansion at first main-text use | Expanded: "NCBI Sequence Read Archive (SRA)" |

## Consistency Checklist (verified, no change needed)

- Number formatting: 8,464 / 212 / 2,292 / 25 / 15 / 85 — thousands separators consistent; percentages as digits + % throughout.
- Spelling convention: American English throughout (behavior, harmonized, apportioned).
- Terminology: "Poisson ratio" (not "Poison") verified in all 6 occurrences; "root-to-tip" hyphenation consistent; "expectation–maximization" en dash consistent; "multi-mapping" and "multipartite" consistent.
- Cross-references: Figures 1–3 all referenced in text and embedded in final; Tables 1–3 all referenced; section cross-references (§2.3, §2.7–2.8, §3.2) verified against section numbering.
- Equations: λ, P, R defined immediately after use; ANI formula (aln_len − NM)/aln_len consistent between §2.3.3 and Figure 2.
- Thresholds quoted identically wherever repeated: reads ≥ 10 / depth ≥ 0.5× / coverage ≥ 10% / Poisson ratio ≥ 0.3 / gene-region 80% & 5% / ANI 95% / align_qc 0.90 / 0.10 / 0.05 / 98%.
- Bibliography: numbered by first appearance (53 refs); no uncited entries; no missing keys; "et al." usage and DOI formatting uniform; corrected bibliographic details applied (RDP5 2021 paper; BioAider = Zhou et al. 2020, Sustain Cities Soc; Maliogka 2018 in Viruses; Massart 2017 = 8:45; SDT 9(9); Hatcher D482–D490; MMseqs2 doi nbt.3988; Fitch doi 10.2307/2412116).

## Verification Flags (author to complete before submission)

1. Author names, affiliations, corresponding email — placeholders.
2. Code repository URL + Zenodo DOI + license — promised "upon acceptance"; must be honored at submission if the journal requires pre-deposit.
3. `requirements.lock`-pinned Python dependency versions in Table 3 — re-verify at camera-ready (snapshot is from the 2026-09 build).
4. The internal development benchmark (7.9 s five-module workflow, GQMIX.q06) is quoted from the in-suite README — reproduce and re-measure on the camera-ready build if the journal requests a complete benchmark table.
