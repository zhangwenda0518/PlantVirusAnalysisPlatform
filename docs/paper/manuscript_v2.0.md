<!-- VirusPlatform manuscript v2.0 (full rewrite, 2026-10-27).
     Spine: the 2026-10-13..19 benchmark programme (docs/paper/benchmark_20261019/), i.e. the
     reference-library completeness audit and the tiered (confirmed/review/discovery) decision model.
     Supersedes manuscript_v1.1.md and manuscript_v1.2.md; no text is carried over verbatim except
     where the platform's implementation is unchanged. Numbers: species-level discovery = 23/28 confirmed,
     26/28 two-tier (independently reproduced on the shipped build, 2026-10-27); strain-level
     reference-present = 46/46 and 101.8% quantitative recovery (upper bound by construction).
     Discovery re-runs against the delivered 12,196-record library are archived in
     docs/paper/benchmark_rerun_20261027/ with input digests and library fingerprints. -->

# Which reference library and which decision tier decide discovery sensitivity: a benchmarked, offline desktop platform for plant virus detection, quantification and evolutionary analysis

**Running title:** Library completeness sets plant virus discovery sensitivity

**Software version:** VirusPlatform v1.0, analysis engine of 2026-10 (HEAD `964f0bf`); delivered plant-virus reference library build of 2026-10-19 (12,196 records). All benchmark numbers refer to this engine and this library build.

**Wenda Zhang^1,\*^**

^1^ Peking Union Medical College, Chinese Academy of Medical Sciences, Beijing, China

\* Correspondence: Wenda Zhang (correspondence e-mail to be confirmed at submission)

---

## Abstract

**Background:** High-throughput sequencing (HTS) has become the reference method for plant virus discovery, yet the computational path from reads to a defensible answer remains a chain of command-line tools written for Linux servers and cloud infrastructure. Plant quarantine, certification and breeding laboratories work on Windows workstations, frequently on air-gapped networks that may not legally export sequence data of regulated pathogens, and rarely employ bioinformaticians. For these users neither a script collection nor a cloud service is deployable, and — a point that receives less attention than it deserves — the sensitivity of reference-based discovery is routinely attributed to the aligner, although in practice it may be bounded by the completeness of the reference library and by where the decision thresholds are drawn.

**Results:** We present VirusPlatform, an offline desktop platform whose analysis core carries paired-end reads through a 15-stage pipeline — quality control, host removal, known-virus identification and quantification, assembly, two-evidence candidate verification, consensus and variant calling, host prediction, ORF annotation, phylogenetics, primer design, genome plotting and HTML reporting — together with a nine-method recombination workflow (RDP5), dependency-free phylodynamics, and an epidemiological browser over ~199,000 public records. Identification runs against a curated plant-virus library of 12,196 annotated records with two orthogonal quantification engines (probabilistic expectation–maximization and true-alignment counting) and reports three decision tiers: confirmed, review (relaxed statistical gates, for human triage) and discovery (assembly-based rescue). Benchmarking against the VIROMOCK inter-laboratory challenge separated configuration from algorithm. In the reference-present configuration, in which every target is installed in a 61-sequence task library, both engines recovered 46/46 strain-level targets (100%) and 101.8% of designed isolate shares (7 of 8 datasets exact); by construction this is an upper bound and we label it as such. In the discovery configuration with the delivered library, 23/28 species-level truths on the ten semi-artificial and real datasets were confirmed (82.1%), 26/28 with the review tier (92.9%), 27/28 with either the assembly-based rescue or a user-lowered review ratio, and 28/28 with both; all 8/8 targets of the fully artificial mixtures were confirmed. A reference-library audit drove most of that gain: five strain-divergent isolates of one species lifted one dataset from 14% to 100%, and a systematic re-audit of the designed composition of all eight artificial datasets revealed 30 missing references (library 12,161 → 12,196), after which those datasets reached 100% per-isolate recovery. On the Bari small-RNA panel, confirmed detection was 6/11 (54.5%) and two-tier detection 11/11 (100%) at fixed defaults, against a 76% mean across 21 participating laboratories. Processing all 18 VIROMOCK datasets with the probabilistic engine took 9.7 min in total; peak memory rose with library size, from ~0.35 GB to ~1.27 GB. Growing the library increased sensitivity and reduced precision (52 false-positive species instances across the ten discovery datasets), a trade-off we quantify rather than tune away.

**Conclusions:** On a shared, inter-laboratory ground truth the platform's sensitivity was governed less by the alignment engine than by reference-library completeness and by the tier at which a call is made; both are auditable and both are exposed to the user. The platform delivers that behaviour as a single offline Windows application with a documented 248-route interface. Concordance with diagnostic RT-PCR/qPCR results on field samples remains the gate between research and diagnostic use.

**Keywords:** plant viruses; viroids; high-throughput sequencing; virus discovery; reference library; benchmarking; decision thresholds; recombination; phylodynamics; offline deployment

---

## 1. Introduction

Plant viruses constrain crop production worldwide, and their impact is amplified by trade in vegetative planting material, globalized seed supply chains and climate-driven shifts in vector ranges [1,2]. Many infections are latent or low-titre, and mixed infections are the rule rather than the exception, so assays that target one species at a time are increasingly complemented — in discovery, quarantine and certification contexts effectively replaced — by HTS [3,4,5]. Frameworks for judging the biosecurity and regulatory significance of viruses found by HTS now exist [4], and HTS is routine in germplasm indexing and post-entry quarantine [5,6], with diagnostic methodology continuing to advance [7].

The computational side remains the bottleneck. Detection in plant HTS data requires quality control, host subtraction, reference-based identification, assembly, verification, consensus generation and evolutionary analysis, each supplied by a separate command-line utility. Early discovery pipelines such as VirusDetect [8] and VirusSeeker [9] automated parts of the chain but were built either around small RNAs or around cloud execution. Modern virome pipelines such as ViroProfiler [10] cover metagenomic analysis well but presuppose containers and command-line fluency. At the classification layer, k-mer classifiers [11,12] and contig-level virus identifiers [13,14] are strong components that stop short of the downstream evolutionary questions — recombination, temporal signal, phylogeography, intra-host diversity — that plant virology increasingly asks. Desktop tools such as BioAider [15] address post-detection genome analysis rather than the full detection-to-evolution path.

Two further observations frame this work. First, the users who most need an integrated tool are those least served by the prevailing distribution model: quarantine stations, certification laboratories and breeding programmes run Windows workstations, often on air-gapped networks where regulated sequence data may not leave the premises, and seldom have a dedicated bioinformatician. Second, and less discussed in the plant virology literature, reference-based discovery has two failure modes that are routinely conflated. One is algorithmic: an aligner or a statistical filter that mishandles multi-mapping reads or low-level hits. The other is documentary: the species, strain or segment present in the sample is absent from — or represented by a single divergent sequence in — the reference library used for the run. The second mode produces exactly the same symptom as the first, a false negative, and it cannot be fixed by tuning the aligner. Our own benchmark experience, reported here, is that the documentary mode dominated: most of the sensitivity recovered during optimisation came from completing the reference library, not from changing the engine.

Here we present VirusPlatform, an integrated offline platform for plant virus HTS analysis, and a benchmark that separates these effects. The contributions are:

1. **A unified 15-stage pipeline** — quality control, host removal, known-virus identification and quantification, assembly, candidate verification, consensus and variant calling, host prediction, ORF prediction and annotation, phylogenetics, primer design, genome plotting and HTML reporting — with declarative stage registration, dependency-aware ordering, resumable execution, input-fingerprint validation of cached results, and per-project manifests recording every input and parameter (Section 2.2, Figure 1).
2. **A curated known-virus suite** operating on a reference library of 12,196 annotated plant-virus records, with two orthogonal quantification engines, a Poisson-ratio safeguard against spurious low-level hits, segment-completeness enforcement for multipartite viruses, ANI-based confirm/novel classification, and — the feature this paper argues for — **three explicit decision tiers**: confirmed, review and discovery (Section 2.3, Figure 2).
3. **A reference-library audit protocol** that reconciles the library against the designed composition of a benchmark panel, quantifies what each missing record costs in sensitivity, and turns library maintenance into a measured activity rather than an assumption (Sections 2.13, 3.3).
4. **An evolutionary-dynamics module** wrapping the nine recombination-detection methods of RDP5 behind an alignment quality-control workflow, with dependency-free root-to-tip regression, Fitch-parsimony phylogeography and within-sample population genetics (Sections 2.7–2.8, Figure 3).
5. **Public-data traceability**: harmonization of NCBI SRA and NGDC GSA metadata into one schema with local-rules-first AI assistance, and a seven-view epidemiological browser over ~199,000 records; the platform's only networked functionality, separated from the offline core (Sections 2.9–2.10).
6. **Desktop engineering for non-experts**: loopback-only server with CSRF and DNS-rebinding defences, two-tier concurrency (heavy 2, light 4), streaming progress, fully resumable jobs, a bilingual interface and a one-click PyInstaller distribution bundling 30 external tools (Section 2.11).
7. **An optional, read-only analysis-guidance agent** that explains results and drafts actions, with every number in its context computed by deterministic code and its execution authority defaulting to a human-approved ticket (Section 2.12).
8. **A benchmark reported by configuration and by tier** — discovery versus reference-present, confirmed versus review versus discovery-rescued — against two inter-laboratory panels, with the harness, library fingerprints, input digests and machine-readable results archived alongside the manuscript (Sections 2.13, 3.3–3.7; Tables 4–9).

Section 2 describes the implementation and the benchmark protocol; Section 3 reports the inventory, worked demonstrations, the library audit, tiered detection, strain-level recovery, the small-RNA panel and throughput; Section 4 discusses positioning, trade-offs and limitations, the most important of which is that concordance with diagnostic ground truth on field samples has not yet been established.

---

## 2. Materials and methods

### 2.1 Architecture

VirusPlatform v1.0 is a Python 3.12 application: a 135-file core package (90,066 lines) split into an analysis layer (`Virus_Platform_Core`), a Flask web layer and a framework-free single-page front end (25 HTML templates, ~12,900 lines; ~9,500 lines of JavaScript; ~2,396 translation keys per language, Chinese and English). The server binds only to the loopback interface (127.0.0.1), probes a port sequence from 8765 with random fallback, validates Host, Origin and Referer against loopback origins to blunt cross-site request forgery and DNS rebinding, and requires an explicit confirmation token for destructive endpoints. The application is designed for one workstation and one analyst and must not be exposed as a shared service. File access is funnelled through path guards that reject traversal outside the platform root; external commands run from argument lists (never a shell) with a default six-hour watchdog and cooperative cancellation.

Jobs are executed by a two-tier task manager — heavy tasks (classification, assembly, annotation, tree building, exact identity matrices; default maximum 2) and light tasks (conversion, plotting, retrieval; default maximum 4) — with FIFO queuing, JSON-persisted state and recovery of interrupted tasks on restart. The front end follows progress by polling and server-sent events. The interface is reachable in an embedded WebView2 window or a system browser, with automatic fallback. The shipped build exposes 248 HTTP routes (35 page-level and 213 programmatic), a surface locked by an automated route-inventory regression test.

### 2.2 Analytical pipeline

The pipeline (Figure 1) has 15 stages in a declarative table: subsampling → fastp quality control [16] → FASTQ-to-FASTA conversion (seqkit [17]) → host read removal → known-virus identification (Section 2.3) → metagenomic assembly → candidate verification → consensus and variants → host prediction → ORF prediction → functional annotation → phylogenetics → primer design → genome plotting → HTML report. The interface groups them into six workflow modules (preprocessing; identification; assembly; host prediction; downstream analysis; reporting).

Execution follows a dependency registry with a stable topological sort, so any selected subset runs in a valid deterministic order. Completed stages leave sentinel files, permitting resumption; the identification stage additionally validates cached results by input fingerprints (path, size and modification time of reads and reference index), so stale results are not presented as fresh. Each analysis writes a project manifest with all inputs, parameters, completed stages and the last 20 runs. Progress is estimated from an exponentially weighted moving average of per-stage runtimes, normalized per gigabyte for large stages.

Host removal uses kunpeng 0.7.12, a memory-efficient Rust k-mer classifier bundled with the platform and driven by user-built indexes; the index builder fragments long sequences into ≤1 Mb chunks with 34 bp overlaps (k − 1 for the default k = 35), so no k-mer is lost at chunk boundaries while memory stays bounded. Quality control uses fastp [16]; optional deterministic subsampling caps runtime. Identification deliberately does not rely on k-mer abundance: abundance cannot separate a few high-coverage infections from many spurious low-level hits, and multi-mapping reads among close references — common in segmented plant viruses — are apportioned arbitrarily. The platform therefore aligns reads to a curated annotated library and filters per reference (Section 2.3), keeping k-mer classification for host removal and contig triage, where speed dominates.

### 2.3 Known-virus identification and quantification

#### 2.3.1 Reference library

The library is a curated set of plant-virus sequences: 8,464 references (32.5 MB) in the September build used for the first benchmark round, 12,161 after the strain- and segment-level records added during the audit, and **12,196 records (45.8 MB) in the delivered build of 2026-10-19**, whose composition is verified by 12,196 FASTA headers one-to-one with annotation rows. Each record carries 26 metadata columns: accession, taxid, NCBI and ICTV species names, segment, sequence type, topology, molecule type, completeness, geography, host, isolation source, and VMR genus/family/species mappings [18], with taxids cross-linked to NCBI taxonomy [19]. Construction normalizes public records into that schema and harmonizes segment designations — 299 distinct raw spellings were observed — into a controlled vocabulary (DNA-A, RNA2, Segment 1, S/M/L), with family-specific rules for lineage nomenclature (Geminiviridae DNA-A/DNA-B; Nanoviridae DNA-R…DNA-U), preserving the original annotation in a parallel column. The library ships with its quantification indexes and a manifest recording build date and configuration; rebuilding regenerates all artifacts and refreshes ICTV/VMR-derived fields.

Two curation errors found during benchmarking illustrate the governance problem. A bacterial genome (accession `NZ_CP011102.1`) had entered the virus library and produced spurious hits until removed. And a species synonym pair (CGB/HSVd) caused a false negative because the library and the truth table used different names; adding an explicit ICTV-species column, used by the scoring interface, removed the artefact. Both are recorded here because they are the kind of defect that a benchmark surfaces and an unbenchmarked library hides.

#### 2.3.2 Dual quantification engines

Two orthogonal engines are provided (Figure 2). The default **probabilistic engine** builds a k = 31 salmon index [20] and estimates per-reference counts by expectation–maximization, apportioning multi-mapping reads — relevant for segmented viruses and species complexes — while emitting an alignment BAM. The **alignment engine** uses minibwa (a minimal BWA implementation bundled with the platform) to produce true alignments and per-reference unique counts. Three interchangeable coverage backends (pandepth, a samtools-based pipeline [21], and a built-in Python implementation) produce bit-identical coverage and depth on test samples. Since the review tier reports low-coverage rows, the suite also emits counts in two units — estimated reads from the probabilistic model and alignment units from the true-alignment path (`Est_Reads`, `Quant_Units`) — so that a reviewer can see which engine produced a number; on Dataset 1 the expected relation (EM estimate ≈ 2× the paired-end alignment count) was verified.

#### 2.3.3 Three decision tiers

Every candidate must clear a base gate (mapped reads > 0; unique reads ≥ 10; mean depth ≥ 0.5×) and at least one of two statistical tracks, with thresholds user-configurable:

- **Track A (genomic):** coverage ≥ 10%, mean depth ≥ 0.5×, and a Poisson ratio ≥ 0.3. The ratio compares observed coverage with the coverage expected if reads were placed independently and uniformly: with λ = N·L~read~/L~ref~ and expected support P = 1 − e^−λ^, R = (Coverage/100)/P. R near 1 is consistent with random placement; R ≪ 1 marks coverage concentrated on a small fraction of the genome, the signature of a spurious hit. The uniformity assumption is conservative (real genomes under-cover their termini), so the cut-off is a screening heuristic rather than a calibrated test.
- **Track B (compact RNA genomes):** gene-region coverage ≥ 80% and gene-region mean coverage ≥ 5%, rescuing small genomes whose intergenic regions may not be covered.

Passing references are classified by read-level average nucleotide identity, ANI = (aln_len − NM)/aln_len over up to 10,000 sampled alignments: ANI ≥ 95% gives a *confirmed* call, ANI < 95% a *suspected novel* variant, and missing ANI (probabilistic engine) is reported as undetermined rather than excluded. Because ICTV demarcation thresholds vary by family, the 95% line is a pragmatic screening cut for strain novelty, not a species test. For multipartite viruses a species is confirmed only when all segments present in the library are detected; segment-incomplete detections stay visible in the full table.

Above that, the suite reports **three tiers**, and the tier is part of every number this paper reports:

- **Confirmed** — passes the gates above; the tier from which quantitative and diagnostic claims are drawn.
- **Review** — passes a relaxed gate set (Poisson ratio ≥ 0.05, mean depth ≥ 0.05× by default) and is emitted as a separate table with a per-row `Signal_Grade` and, where relevant, the nearest same-genus species (`Same_Genus_Next_Species`); rows are *candidates for human triage*, not calls. A `--review-drop-low` switch discards rows below a configurable grade, and the review table is what made the difference, on our panel, between 23/28 and 26/28 species-level truths.
- **Discovery** — when a truth remains absent after both tiers, the pipeline can concentrate on it: a 371 bp contig assembled at 59.6× mean depth over 94.5% of the reference (Dataset 3) recovered a viroid that read-based tiers could not, and the assembly route writes a `suggest_discovery_assembly.txt` file so that a user can hand the case to the discovery track deliberately.

Outputs comprise the confirmed table, the review table, the best-hit table, the suspected-novel table and discard lists with per-read reasons; in batch mode per-sample checkpoints allow resume, and failed samples are recorded explicitly as "failure ≠ negative".

Consensus sequences come from a dedicated realignment chain (minibwa against a single-reference index, then viral_consensus with quality ≥ 20, depth ≥ 5, frequency ≥ 0.5, ambiguity N); variant calling uses bcftools [22] (mpileup depth ≤ 100,000, base quality ≥ 13, BAQ off; QUAL ≥ 3.5; frequency ≥ 0.05), with SNVs and intra-host SNVs annotated by SnpEff [23]. Viroids, which lack coding sequences, are detected by nucleotide search in the verification stage and carried through gracefully. Viral reads extracted from the identification BAM can feed assembly directly, concentrating it on viral signal.

### 2.4 Assembly and candidate verification

De novo assembly uses SPAdes [24] in metaviral mode by default (rna and meta selectable), with configurable memory (default 64 GB), minimum contig length (default 500 bp) and input choice (host-removed reads or identification-extracted viral reads). Contigs are classified by kunpeng and filtered by confidence. Because classification alone does not prove that a contig is viral, the verification stage applies two independent lines of evidence — translated search with DIAMOND blastx [25] against a bundled RefSeq [26] viral protein set, and conserved-domain confirmation with MMseqs2 [27] against CDD models [28] — combined under a union rule into four verdicts (known, novel, domain_only, unclassified), plus a separate viroid route by nucleotide BLAST [29]. Unclassified contigs are retained as the novel-virus candidate pool rather than discarded.

### 2.5 Host prediction, ORF prediction and annotation

Host range is predicted by a cascade classifier trained on ICTV host associations [18], cross-validated against NCBI host metadata with BLAST fallback, and visualized as Sankey and sunburst diagrams. ORFs are predicted with pyrodigal [30] and the RNA-virus-aware pyrodigal-rv [31] (minimum 100 amino acids by default), and annotated in batch against the RefSeq viral protein set with DIAMOND, MMseqs2 or blastp (database auto-downloaded on first use, ~107 MB), with pyhmmer/CDD confirmation as fallback. Outputs include per-ORF tables, functional-category and family distributions and GFF3 tracks. A genome-diagnostic module reports internal stop codons, frameshifts and completeness defects.

### 2.6 Phylogenetics and comparative genomics

The phylogenetic stage groups sequences by BLAST top-hit species, aligns each group with MAFFT [32] (auto by default, L-INS-i selectable; CJK paths relayed through an ASCII directory), trims with trimAl [33] (automated1, graceful fallback), and infers trees with one of three engines: a dependency-free neighbour-joining implementation over pairwise identity distances, FastTree 2 [34] (GTR+Γ), or IQ-TREE 3 [35,36] with ModelFinder [37] and dual support (1,000 ultrafast bootstrap replicates [38]; 1,000 SH-aLRT replicates [39]). Public NCBI references can be pulled into alignments with hierarchical sampling strategies (macro-area, genus, lineage) adapted from a production quarantine pipeline. Two safeguards keep the stage usable on desktop hardware: a 25,000 bp reference-length ceiling against MAFFT's quadratic memory, and per-species trees that avoid uninformative cross-species alignments.

The primer design module wraps primer3 [40,41] in tiling and conserved-region modes with candidate scoring and optional thermodynamic checks. Comparative genomics provides pairwise whole-genome identity in the exact SDT v1.3 formulation [42] re-implemented in pure Python and validated against the original, nucleotide and amino-acid identity matrices with composite heatmaps, structure comparison, and interactive viewers for alignments, trees and heatmaps.

### 2.7 Recombination detection

Recombination analysis (Figure 3A) is a single workflow wrapping the bundled RDP5 executable [43], which implements nine methods — RDP, GENECONV, Bootscan, MaxChi, Chimaera, SiScan, PhylPro, LARD and 3Seq [43,44]. RDP5 requires a homologous equal-length alignment, so a quality-control pre-filter removes sequences failing any of: un-gapped length ratio < 0.90 against the reference (fragment), gap fraction > 0.10 (poor alignment), ambiguous-base fraction > 0.05 (low quality) and — only when an explicit outgroup reference is designated — pairwise identity < 98% (outlier isolation), computed with the SDT formula [42]. The identity criterion is gated because a 98% line is meaningful only for near-identical sets: on the bundled ten-isolate CMV RNA3 alignment, applying it with the RefSeq isolate as reference removes 6 of 10 biologically valid isolates (identities 75.6–92.9%), whereas length/gap/ambiguity alone remove none (Section 3.2). Alignments of unequal length, or with fewer than four sequences (the RDP5 minimum), are rejected with explicit diagnostics.

RDP5 runs all nine methods and its evidence table is parsed: each event row carries breakpoints, recombinant/minor/major parent assignments and per-method p-values; the significance set is the methods reaching p < 0.05, with the best p-value as the minimum. Following RDP5's guidance [43], detected intervals (including those split across circular-genome ends) are merged, the affected columns masked to N, and the recombination-free alignment exported for tree inference. An earlier in-house implementation of three triplet methods was removed after benchmarking on synthetic chimeras (six 1 kb sequences, true breakpoint at position 500) produced 18 detections against 1 true event; RDP5 is now the sole engine, and we report the negative result as a caution against informal re-implementations.

### 2.8 Temporal signal, phylogeography and population genetics

Three dependency-free tools answer the questions most often asked of plant-virus sequence data without Bayesian runtimes (Figure 3B–D). **Root-to-tip regression** reproduces the TempEst workflow [45] in-process: dated taxa are placed on an unrooted tree, all midpoint rerootings are evaluated, the root maximizing R² is selected, and the slope estimates substitutions/site/year (minimum five dated taxa). **Phylogeographic reconstruction** assigns discrete geographic states by Fitch maximum parsimony [46] — a bottom-up pass computing minimal state sets, a top-down pass fixing internal states, and a tally of parent→child transitions into a migration matrix — yielding an annotated Newick tree, the matrix, per-tip states and a transition list; TreeTime [47] remains available for time calibration. **Within-sample population genetics** computes Ts/Tv spectra, allele-frequency spectra, π~N~/π~S~ per coding region under a pooled-sequencing model, sliding-window π, and Tajima's D from the variant catalogues.

### 2.9 Public metadata integration (optional networked module)

VirusPlatform harmonizes metadata from the NCBI SRA [48,49] (E-utilities; 18 extraction dimensions) and the NGDC Genome Sequence Archive [50] (10 dimensions; HTML and Excel endpoints with retry/backoff and caching) into a 14-column schema, using Genome Warehouse [51] and Europe PMC [52] for project-level and literature provenance; locations are normalized to three levels (country, province, city). This is one of the platform's **two networked components** (the other being the optional agent of Section 2.12), a boundary enforced in code and documentation: the analysis core (Sections 2.2–2.8) performs no network I/O, and retrieval degrades to cached results when offline. Metadata cleaning may be assisted by OpenAI-compatible large-language-model services (DeepSeek and Kimi endpoints preconfigured) **only after the user configures a key** — an act that sends repository metadata, never local sequencing data, to the provider. Locally extracted fields take precedence; the model may fill gaps and reformat only; a sanitizer permits subtraction, relocation and reformatting but never fabrication; and every AI-touched field carries a provenance suffix for manual review. An optional datavzrd report renders cleaned metadata as browsable tables.

### 2.10 Epidemiological browser

A server-rendered explorer provides seven coordinated views over a pre-built database of ~199,000 public plant-virus sequence records: spatiotemporal trends, whole-genome mutation browsing, a filterable record table (capped at 5,000 rows with a truncation notice), a primer database, host-range summaries, vector-transmission summaries and per-virus profiles. The module began as a Dash application with 13 callbacks and was re-engineered as 13 native Flask JSON endpoints, removing the Dash runtime from the package while preserving behaviour.

### 2.11 Distribution, packaging and quality assurance

The platform ships as a PyInstaller onedir bundle (`VirusPlatform.exe`) embedding the Python runtime, the web front end (including the offline Plotly bundle) and 30 precompiled external tools; Table 3 lists the pinned versions of key components. The packaged distribution measured 2.9 GB on disk (program, example data and bundled reference library), installs by extraction on any Windows 10/11 x64 workstation without administrator rights, Python or network access, and runs an automated post-build self-check (`--cli selfcheck`) that verifies tool executability inside the artifact.

Quality assurance is an automated estate of 85 Python scripts — 30 end-to-end integration tests (alignment, annotation, asynchronous UI behaviour, background tasks, comparisons, concurrency, downloads, navigation, phylogenetics, submission), 20 static and dynamic checks (route inventories, database paths, example manifests, console errors, interface language), four unit-test modules — plus audit, probe and route-baseline guards that lock the 248-route surface, and a 219-case pytest suite. Eighteen example inputs and 26 curated example result sets ship with the distribution as documentation and as regression fixtures. Maintaining a 90,066-line core with 30 bundled binaries is a real liability; the mitigation is version-pinned tool manifests, library manifests with build dates, the post-build self-check and the test estate, with a stated policy of rebuilding whenever an upstream tool ships a security or correctness fix.

### 2.12 Optional AI analysis-guidance agent

The optional agent closes the gap between "the pipeline finished" and "I understand the result and know what to do next", under three constraints. **Adviser, not substitute:** all computation is performed by the existing engines and the agent is read-only with respect to results. **Numbers come from code:** every quantity entering the model context is computed by deterministic code from on-disk artifacts; the model interprets but never generates numbers. **Pluggable:** with no key configured nothing is lost; once configured the agent speaks the OpenAI-compatible protocol (DeepSeek, Kimi, Qwen, GLM, OpenAI, local Ollama), with the key stored separately in `run/ai_local.json`.

Two engagement paths exist. *Proactive drive* has three layers: a bilingual knowledge-base card at stage start (no model cost); a digest-driven "readout — cautions — next step" event at key nodes; and an optional decision gate that blocks until the user confirms continue/abort (the agent has an advisory vote, no authority). *Conversational follow-up* uses a read-only tool set (list samples and stage completeness, read a digest, read result tables and tool-run artifacts); no tool can re-run a stage, change configuration or delete data. Execution authority is tiered and defaults to the conservative setting: Tier 1, the **action ticket** (on), drafts an action for approval and executes it through exactly the same code path as the corresponding button; Tier 2, **automatic retry** (off), re-runs a failed stage once with unchanged parameters; Tier 3, **parameter tuning** (off), proposes parameters within a fixed key set and clamped bounds once per sample, falling back to Tier 2. Hard-coded interlocks allow one run per sample at a time, a platform-wide cap of one agent-initiated run, whitelist-and-type clamping, and an action registry containing no deletion, reset or self-configuration. All actions and broadcasts are appended to daily-rotating JSONL files (90-day retention, secret-like fields scrubbed), action tickets are written synchronously, and in-flight actions are marked `interrupted` after restart. The layer spans 9 routes and ~160 bilingual strings; any agent failure degrades silently and never touches the analysis pipeline.

### 2.13 Benchmark design and the library-audit protocol

**Panels.** Performance was evaluated on two public panels whose composition is established independently of our software. The primary panel is the VIROMOCK challenge [54,56,57]: 18 datasets (13.6 GB compressed, ~70 GB decompressed) comprising ten semi-artificial or real datasets built from naturally infected material (Datasets 1–10) and eight fully artificial datasets of viral reads mixed at designed isolate proportions (Datasets 11–18), together with the participants' per-dataset evaluation records (113 records, 98 meeting the challenge objective; [54,57]). The secondary panel is the Bari small-RNA inter-laboratory exercise [55]: two 250,000-read small-RNA libraries (potato and grapevine) with a curated pathogen list and the mapping counts of 21 laboratories.

**Configurations.** Two reference configurations answer different questions. The **discovery configuration** uses the shipped default library (12,196 records) and measures what a user obtains without prior knowledge of the sample. The **reference-present configuration** adds a task-specific library of 61 sequences containing every spike-in and isolate accession of the challenge targets; by construction it asks whether the platform recovers targets it is given, and its detection result is an upper bound, not a sensitivity estimate.

**Tiers scored.** Detection is scored at three tiers. *Confirmed* compares the confirmed table with ground truth. *Two-tier* adds review-tier rows that match a truth species not already confirmed. *Rescue* adds truths recovered by the assembly/discovery route. In the discovery configuration the headline is the confirmed tier because that is the only tier with no human in the loop; the two-tier figure is reported as what a laboratory would reach after triaging a short review list, and we state how many review rows that triage required.

**Audit protocol.** Before scoring, the library was audited against the panels: for each artificial dataset, every designed isolate accession was looked up in the library and in its annotation table; for each semi-artificial dataset, the composition table was reconciled against the delivered read files. Every missing record was logged with its accession, species, dataset and the reason for absence, then added (with the corresponding strain- or segment-level records) and both indexes rebuilt. The audit is reproducible from the archived logs and is the mechanism behind the sensitivity changes reported in Section 3.3.

**Metrics.** Detection is scored per target (species level in discovery, strain/isolate level in reference-present) by comparing the platform's table at the stated tier with the challenge ground truth; a false positive is a reported species absent from the truth set. Quantification accuracy is the absolute deviation of each target's observed share within its same-species reference pool from the designed share, reported as mean absolute error (MAE) in percentage points; shares with a confounded background are excluded and discussed separately. Consensus accuracy and completeness are obtained by re-mapping each dataset's reads against its ground-truth reference subset and comparing the called consensus with the reference (callable breadth = positions with depth ≥ 1× passing Q20; identity over called positions). Wall-clock time and peak process-tree resident memory are sampled at 1 Hz. All thresholds are platform defaults; no parameter was tuned on benchmark data, the one exception (Dataset 10, below) being reported explicitly as a threshold-sensitivity observation.

**Archives.** The harness (library construction, batch runners, comparator, consensus benchmark, library audit) ships in `tests/bench/`; the species- and strain-level results, the four optimisation rounds (threshold scan, decision-tier implementation, precision and engineering work, reference-gap repair), the ten supplementary figures and the plotting scripts are archived under `docs/paper/benchmark_20261019/`; and the discovery-configuration re-runs against the delivered library, with per-dataset input SHA-256 values and library fingerprints, are archived under `docs/paper/benchmark_rerun_20261027/`.

### 2.14 Statistics

All results are exact counts over fixed panels; no sampling-based inference is performed and no p-values are reported. Where a rate is given (sensitivity, precision, MAE) the denominator is stated in the text or table. Agreement between engines and between runs is reported as identity of outputs or as absolute differences, not as correlation coefficients; the single correlation reported (Section 3.6) is used to show that a strong correlation can coexist with a systematic offset, and is interpreted accordingly.

---

## 3. Results

### 3.1 Platform inventory

Table 1 lists the 15 pipeline stages and their principal outputs. The 25 registered standalone tools (Table 2) cover workflows outside the linear pipeline — format conversion, dsRNA design, exact SDT analysis, recombination, phylogeography, temporal-signal testing, and two composite chains (a four-step identification-classification chain and a five-module quantification-consensus chain) that serialize several tools into one reproducible run. Table 3 lists the pinned versions of the key bundled components.

**Table 1.** The 15-stage analytical pipeline.

| Stage | Function | Key tools | Representative outputs |
|---|---|---|---|
| subsample | Deterministic read subsampling | — | sub_R1/R2.fastq.gz |
| fastp | Read QC and filtering | fastp [16] | clean reads, QC report |
| fq2fa | FASTQ→FASTA | seqkit [17] | conv_R1/R2.fa.gz |
| host | Host read removal | kunpeng 0.7.12 | kept reads, host fraction |
| kvsuite | Known-virus identification, quantification, three-tier decision | salmon [20] / minibwa | confirmed, review and discovery tables; BAM; viral reads |
| assembly | Metagenomic assembly and contig classification | SPAdes [24], kunpeng, BLAST [29] | contigs, classification TSV |
| verify | Candidate verification (two-evidence) | DIAMOND [25], MMseqs2 [27], CDD [28] | calls.tsv (known/novel/domain_only/unclassified/viroid) |
| consensus | Consensus and variants | minimap2 [53], viral_consensus, bcftools [22] | consensus.fa, variants.tsv |
| hostana | Viral host prediction | ICTV cascade [18] | host_prediction.tsv, Sankey/sunburst |
| orf | ORF prediction | pyrodigal(-rv) [30,31] | faa/ffn/gff |
| orfa | ORF functional annotation | DIAMOND/MMseqs2/blastp + HMM | orf_annotation.tsv, GFF3 |
| phylo | Alignment, tree, identity matrix | MAFFT [32], trimAl [33], FastTree [34] / IQ-TREE [35] | tree.nwk, SDT matrix |
| primer | Primer design | primer3 [40,41] | primers.tsv |
| gbdraw | Genome plots | gbdraw / DNA Features Viewer | SVG circular/linear plots |
| report | HTML report | Plotly, pycirclize | report.html |

**Table 2.** The 25 standalone analysis tools, grouped by category.

| Category | Tools |
|---|---|
| Read processing | convert, fastp, hostremoval |
| Identification and classification | identify, assemble, contigs, verify, kvsuite; composite chain kvchain |
| Genome analysis | consensus, orf, orfa, genoplot, primer, dsrna |
| Comparative genomics | structcmp, sdt (exact SDT), identity, align, quicktree |
| Evolutionary dynamics | rdp (RDP5, nine methods), rtt (temporal signal), phylogeo (Fitch) |
| Composite chains | virchain (identification–classification), kvchain (quantification–consensus) |

**Table 3.** Pinned versions of selected bundled tools (full manifest in the distribution).

| Tool | Version | Tool | Version |
|---|---|---|---|
| kunpeng | 0.7.12 | bcftools | 1.24 |
| IQ-TREE | 3.0.1 | biopython | 1.88 |
| pyrodigal | 3.7.1 | Flask | 3.1.3 |
| pyrodigal-rv | 0.1.0 | polars | 1.44.2 |
| pyhmmer | 0.12.3 | plotly | 7.0.0 |
| primer3-py | 2.2.0 | pywebview | 6.2.1 |

### 3.2 Worked demonstrations

The distribution bundles worked demonstrations, and the measurements below were made with the shipped code on a documented workstation (Intel Core i7-12700K, 20 logical cores, 64 GB RAM, Windows 11, Python 3.12.10; 8 threads unless noted). All are reproducible from the bundled example data.

**Engineering throughput.** Against the full reference index of the September build (8,464 sequences; 32.5 MB), the alignment engine built its index in 3.73 s and aligned the bundled paired-end example (3,600 read pairs, all 7,200 records mapped) in 0.17 s — about 20,600 read pairs per second at 8 threads. A mock mixed-virus sample completed the five-module known-virus workflow (identification, filtering, consensus, plotting, variant calling) in 7.9 s against the same index. These are engineering measurements of throughput, not diagnostic performance.

**Recombination pre-filter.** On the bundled CMV RNA3 alignment (10 isolates from China, India, Slovenia, South Korea, Australia, Iran, France, Germany, Poland and RefSeq NC_001440.1; 2,292 columns), the default pre-filter (length, gap and ambiguity criteria; identity criterion disabled) retained all 10, whereas explicit-reference mode (NC_001440.1, 98% gate) retained four and removed six isolates at 75.6–92.9% identity — the documented behaviour for non-outbreak datasets, and the reason the identity criterion is reference-gated. The full pairwise SDT matrix over this alignment ranged from 75.6% to 99.2% (mean against the RefSeq reference 89.1%), with within-subgroup identities of 98–99% and approximately 75–77% between subgroups (Figure 4).

**Measurement integrity.** The three coverage backends produced bit-identical coverage and depth on all tested samples; the route guard confirms the 248-route surface; and the post-build self-check verifies executability of all 30 bundled tools inside the packaged artifact.

Section 3.2 reports engineering measurements only. Diagnostic performance is addressed in Sections 3.3–3.6 against two inter-laboratory ground-truth panels; concordance with RT-PCR/qPCR on field samples is not part of this evaluation and is identified in Section 4 as the remaining gate.

### 3.3 Reference-library completeness, not the alignment engine, set discovery sensitivity

The first benchmark round scored the discovery configuration with the September library (8,464 records) and returned 20/28 species-level truths on Datasets 1–10. The four optimisation rounds that followed are summarised in Table 4. Their single largest effect came from library repair rather than from algorithm changes, and the pattern was consistent: where a target was missed, the target (or its closest strain) was absent from, or represented by a single divergent sequence in, the library.

The clearest case is Dataset 17. The dataset was initially recovered at 14% of its designed reads, and the obvious hypotheses (truncated files, a failed host-removal step) were wrong: the reads were complete and the pipeline behaved normally. The composition table specified five isolates of *Little cherry virus 1* whose library representatives were only 73–78% identical to one another; the library held a single record. Reads from the four unrepresented isolates were therefore either unaligned or apportioned to a distant reference and filtered out. Adding five strain-level records raised recovery of that dataset to 100.0% — a documentary fix that no alignment parameter could have produced.

The same audit, applied systematically to Datasets 11–18, compared every designed isolate accession with the library annotation table and found 30 missing records (Dataset 11: 2; 12: 2; 13: 5; 14: 5; 15: 3; 16: 4; 18: 5). Adding them, together with the segment-level records for Tomato spotted wilt virus (L, M and S; taxid 3052585) that had been injected in the September round but were still absent from the snapshot used for the first scoring, moved the library from 12,161 to 12,196 records and required a rebuild of both quantification indexes (salmon k = 31 and k = 15, 10 s and 31 s respectively; minibwa, ~8 s). After the repair, per-isolate recovery on the artificial datasets reached 100% for Datasets 11–17, and the aggregate quantitative recovery — observed share summed over isolates divided by designed share — was 101.8%, with seven of eight datasets at exactly 100.0%.

Two read-file facts discovered during the same audit belong to the benchmark, not the platform, and are reported so that the numbers can be read correctly: the early copy of Dataset 17 available on one drive contained a single isolate (the complete file holds 49,513 read pairs), and the delivered Dataset 18 files contain 16.8% more reads than its composition table implies (46,917 versus 40,185 fragments). The second explains part of the residual deviation on that dataset and would affect any pipeline scored against the table.

Library growth was not free. On Datasets 1–10 the confirmed-tier false-positive instances rose from 41 to 52, and confirmed-tier precision fell from 32.8% to 30.7%: the added near-neighbour records attract reads from background viruses that were previously unrepresented. We regard this as the honest cost of the sensitivity gain and return to it in Section 4.

**Table 4.** Four optimisation rounds, and what each changed in the discovery configuration.

| Round | Change | Effect on discovery configuration |
|---|---|---|
| Baseline | September library (8,464 records); single confirmed tier | 20/28 species-level (71.4%); precision 32.8% |
| 1. Threshold calibration | 16-configuration scan of coverage, depth, read-count, TPM and Poisson thresholds | Poisson ratio is the only effective gate (0.3 → 0.1 adds two targets; single-dimension relaxation of the others changes nothing) |
| 2. Decision tiers | Separate review tier (`Signal_Grade`; relaxed Poisson and depth defaults 0.05/0.05), assembly-based discovery route, explicit `Ictv_Species` linking | 23/28 confirmed; +3 truths at the review tier (26/28); +1 by assembly rescue (27/28); +1 at a user-chosen review ratio (28/28) |
| 3. Precision and engineering | Removal of a bacterial record (`NZ_CP011102.1`) and of a species-synonym artefact (CGB/HSVd); `--review-drop-low`; `Est_Reads`/`Quant_Units` dual reporting; discovery-suggestion files | Review list shortened (Dataset 9 small-RNA: 15 → 7 rows); false positives attributed rather than suppressed |
| 4. Reference-library repair | TSWV L/M/S; five *LChV-1* isolates; 30 accession-level records across Datasets 11–18; index rebuild | Dataset 7: 0/1 → 1/1; Dataset 17 isolate recovery 14% → 100%; per-isolate recovery 101.8% (7/8 datasets exact); library 12,161 → 12,196 |

### 3.4 Tiered detection on the VIROMOCK discovery configuration

With the delivered 12,196-record library and the shipped engine, the confirmed tier recovered **23 of 28** species-level truths on Datasets 1–10 (82.1%; precision 30.7%) and **8 of 8** on the artificial mixtures of Datasets 11–18 (precision 80%) (Tables 5 and 6). Adding review-tier rows that match a truth species raised the semi-artificial and real panel to **26/28 (92.9%)** at a cost of six review rows across three datasets, of which three were true matches: *Grapevine leafroll-associated virus 2* (2.73% coverage, 0.05× depth, Poisson ratio 0.314) and *Grapevine rupestris vein feathering virus* (6.7%, 0.74×, 0.088) in Dataset 3, and *Hop stunt viroid* (45.3% coverage, 1.52× depth, Poisson ratio 0.492) in Dataset 4. Assembly-based rescue added the Dataset 3 viroid from a 371 bp contig (59.6× mean depth over 94.5% of the reference), reaching **27/28** (lowering the review ratio alone reaches the same 27/28 through Dataset 10; the two routes are independent). The last truth, the artificial *Plum pox virus* (PPV) strain of Dataset 10, sits just outside the default review gate (Poisson ratio 0.045 versus the 0.05 default) and is recovered when the review ratio is lowered to 0.04 — the fourth tier, bringing the panel to **28/28** and confirming that the residual gap is a threshold choice, not a detection failure.

Two results on the panel must be read with care, and we state both. Dataset 5 is an 80/20 mixture of a recombinant and its near-identical parent: no participating laboratory reproduced the design, our own estimates are correspondingly displaced, and we treat the dataset as an information-theoretic limit rather than a platform failure — the criterion we hold ourselves to, detection of both strains, is met. Dataset 9 carries a detection we report as a true positive under the challenge's scoring rules but flag as questionable: the reference with 100% coverage is annotated *Pistacia emaravirus*, and the species recorded in the truth set is *Pistacia virus B*, whose library representation is absent; a mislabelled record would convert this target from a success to a library gap, and we prefer to name the possibility rather than bank the point.

**Table 5.** Tiered species-level detection on the VIROMOCK discovery configuration (delivered 12,196-record library, shipped engine).

| Tier | Datasets 1–10 | Datasets 11–18 | What the tier requires |
|---|---|---|---|
| Confirmed (default gates) | 23/28 (82.1%) | 8/8 (100%) | no human in the loop |
| Two-tier (confirmed + review) | 26/28 (92.9%) | 8/8 | triage of 6 review rows (3 true matches) |
| Two-tier + assembly rescue | 27/28 (96.4%) | 8/8 | one assembly run (Dataset 3) |
| Two-tier + rescue + user-lowered review ratio | 28/28 (100%) | 8/8 | one non-default threshold (Dataset 10, 0.04) |

**Table 6.** Per-dataset outcome at the confirmed, review and rescue tiers.

| Dataset | Truths | Confirmed | Review matches | Rescue | Notes |
|---|---|---|---|---|---|
| D1 | 5 | 5 | 0 | 0 | CTV strains + background |
| D2 | 5 | 5 | 0 | 0 | |
| D3 | 5 | 2 | 2 | 1 | GLRaV-2, GRVFV at review; viroid from 371 bp contig |
| D4 | 5 | 4 | 1 | 0 | HSVd at review (~0.5% frequency) |
| D5 | 1 | 1 | 0 | 0 | 80/20 mixture; both strains detected (information limit) |
| D6 | 1 | 1 | 0 | 0 | novel *Potato virus Y* strain recovered |
| D7 | 1 | 1 | 0 | 0 | TSWV recovered after L/M/S library repair |
| D8 | 2 | 2 | 0 | 0 | CqMV1 ~29× depth at ~0.5% frequency |
| D9 | 1 | 1 | 0 | 0 | *Pistacia* record flagged as possible mislabel |
| D10 | 2 | 1 | 1* | 0 | PPV requires review ratio 0.04 |
| D11–18 | 8 | 8 | 0 | 0 | designed mixtures |

*Recovered only at the user-lowered review ratio; outside the default gate.

**Precision.** At the confirmed tier the ten discovery datasets produced 52 false-positive species instances against 23 true positives; at Datasets 11–18 the figures are 2 false positives against 8 true positives. The false positives cluster in three classes: close-relative spill-over (reads from a background virus aligning to a congener, which the segment-completeness and ANI layers mitigate but do not remove); a recurring cross-background hit (*Citrus exocortis Yucatan viroid*, 21 instances across datasets, flagged for library curation); and unrelated low-level hits that pass the Poisson gate by chance. Precision is therefore the platform's weakest published number, and it is the one most sensitive to library size — a point we develop in Section 4.

**Engine comparison.** Holding the library and gates fixed, the alignment engine confirmed 20 of 28 species-level truths on Datasets 1–10 (71.4%; precision 29.0%, 49 false-positive species instances) and 8 of 8 on the artificial mixtures, against 23/28 and 8/8 for the probabilistic engine. The three-truth difference is confined to Datasets 1–3 — the datasets in which multi-mapping among close references and among segments is the binding constraint, where unique-alignment counting sees the reads but cannot apportion them and the probabilistic model does. The engine therefore matters at the margin, and in the direction the design anticipates (Section 2.3.2): the library and the tier move the result by tens of percentage points, the engine by a few species. The alignment path is chosen when countable, auditable evidence is worth its cost, not when maximum sensitivity is wanted.

### 3.5 Reference-present configuration: an explicit upper bound, and quantification accuracy

When every scored target is installed in the library, both engines detected all 46 strain-level targets across 12 datasets (Table 7), and the challenge laboratories' overall success rate on the same evaluation records was 98/113 (86.7%). We stress what this does and does not show. Because every target is present by construction, 46/46 is a **reference-present upper bound**: it measures whether the platform recovers targets it is given, not whether it discovers targets absent from the library. It is informative in that light — the novel PVY strain of Dataset 6, missed by one laboratory and reconstructed by two, is recovered as a reference; all eight *Pisacia virus B* segments (99.9–100% breadth), PFBV, the cryptic mitovirus CqMV1 (~29× depth at ~0.5% frequency) and the spike-in viroid GYSVd-2 of Dataset 4 are recovered — but it must never be quoted as a discovery rate, and Section 3.4 is where the discovery number lives.

Quantification on designed mixtures deviated from the design by 0.00–1.15 pp (probabilistic engine) and 0.00–1.78 pp (alignment engine) across 39 isolates; excluding the confounded Dataset 5, pooled MAE was ≈0.18 pp and ≈0.44 pp over 44 detected checkpoints. The largest single deviation (4.8 pp, Dataset 14) reflects read sharing among eight same-species potato virus Y (PVY) references rather than estimation error. On the three-strain Citrus tristeza virus mixture of Dataset 1, quantified in the presence of a homologous background, our estimates (salmon 70.16/28.56/1.28%; minibwa 69.70/28.83/1.47%) agree with both reference laboratories (ILVO 69.79/29.18/1.03%; USDA-APHIS 69.64/29.24/1.07%) and with the design (69.75/29.22/1.03%) to within 0.7 pp. Two further checks support the layer: on Dataset 10 the alignment engine's 10.0% share of the artificial PPV strain coincides with the two laboratories that reconstructed it at full length (9.85% and 10.08%), and on Dataset 8 the CqMV1 share (0.51%) falls inside the laboratory range (0.48–1.5%).

**Table 7.** Reference-present configuration: strain-level detection and quantification (upper bound by construction).

| Measure | Result |
|---|---|
| Strain-level detection, 12 datasets | 46/46 both engines (challenge laboratories: 86.7%, 98/113 records) |
| Quantification MAE, 39 isolates | 0.00–1.15 pp (probabilistic); 0.00–1.78 pp (alignment) |
| Pooled MAE excluding the confounded Dataset 5 | ≈0.18 pp / ≈0.44 pp over 44 checkpoints |
| Aggregate quantitative recovery, Datasets 11–18 | 101.8%; 7 of 8 datasets exactly 100.0% |
| Dataset 1 CTV strains (design 69.75/29.22/1.03) | 70.16/28.56/1.28 (probabilistic); 69.70/28.83/1.47 (alignment) |
| Dataset 5 80/20 mixture | 6.5/93.5% and 14.6/85.4%; no laboratory reproduced the design |

Consensus sequences, obtained by re-mapping to ground-truth references, were reconstructed for the 39 virus-only isolate genomes and the Dataset 4 viroid at 100% identity, zero SNVs and 99.5–99.9% callable breadth. Completeness is depth-bound: the 1.03% CTV strain of Dataset 1 showed 99.6% breadth but only 34.4% of positions callable at a 10× threshold (99.6% at 1×) — detection and callability are different gates, and both are reported. Consensus of natural isolates diverged from their references as biology predicts (TSWV 98.2–98.8%; PFBV 97.4%; PiVB segments 95.4–100%), and the defective TSWV L segment of Dataset 7 was localized from the per-site depth profile (positions 760–7060 at 321× mean depth versus 10,216× in the flanks). In 200 error-free simulations the module reproduced the template at 100% identity, establishing that the partial callability of real low-depth data reflects depth rather than a computational defect. One real defect was found and is reported rather than omitted: consensus called on a library with zero mapped reads returns an all-N sequence without raising an error (no zero-mapping guard); the case is registered as a platform fix and no benchmark claim here depends on it.

### 3.6 The Bari small-RNA panel: a different regime, and a tier that matters

The Bari panel [55] probes a regime outside the platform's design envelope — 21–24 nt small RNAs rather than 100–150 nt reads — and the result is correspondingly weaker (Table 8). With a small-RNA-adapted k = 15 index, confirmed detection was 6/11 (54.5%; six true positives, four false positives, five false negatives; precision 60%); the review tier recovered the remaining five pathogens, for 11/11 (100%) after triage. Two controls bound the result: the standard k = 31 configuration recovered 0/11, and the alignment engine declined to run because its 50 bp read-length floor is incompatible with 21–24 nt reads. The assembly-plus-BLAST route with a 60 bp contig floor recovered 4/11 (36.4%), confirming that short small RNAs carry enough information for contig-level assembly but not enough for the read-based route at default thresholds. Sensitivity degraded with depth (Sample 9: 44.4% at 250,000 reads to 33.3% at 50,000). For context, the 21 laboratories in that study [55] reported a mean detection rate of 76% (range 35–100%) at 250,000 reads while combining host filtering, study-specific thresholds and expert curation; our confirmed figure comes from fixed defaults and no expert review, and the two-tier figure comes from fixed defaults plus a short triage list, so the comparison is one of magnitude and of what a tier buys, not of like-for-like pipelines.

Read-share estimates on the same panel correlated strongly with expected values (Pearson r = 0.968, n = 17) yet were systematically low: median observed/expected ratio 0.43, log–log intercept −0.56. The cause is a unit mismatch — the platform's TPM-style shares are normalized per million paired-end reads while the reference shares are per viral read — and we report the correlation and the offset together, because r alone indicates agreement in direction and strength, not in absolute level.

**Table 8.** Bari small-RNA panel, fixed defaults.

| Configuration | Detection | Notes |
|---|---|---|
| Small-RNA k = 15, confirmed tier | 6/11 (54.5%) | 6 TP, 4 FP, 5 FN; precision 60% |
| Small-RNA k = 15, two-tier | 11/11 (100%) | after review-tier triage |
| Assembly + BLAST, 60 bp contig floor | 4/11 (36.4%) | contig route |
| Standard k = 31 | 0/11 | control |
| Alignment engine | declined | 50 bp read-length floor |
| Depth titration (Sample 9) | 44.4% → 33.3% | 250,000 → 50,000 reads |
| Laboratory frame (21 labs) | 76% (35–100%) | host filtering + expert curation |

### 3.7 Throughput, memory and end-to-end integration

Selecting the default engine is a design decision with a measurable cost. On the delivered library and shipped engine, the probabilistic suite processed all 18 discovery datasets in 9.7 min at 19 threads; the ten semi-artificial and real datasets dominate that total (Dataset 1: 117.1 s; Datasets 2–4: 111.0, 75.7 and 24.4 s), while the artificial mixtures need 9–19 s each. Peak process-tree memory was ~0.35 GB on most datasets and ~1.27 GB on the two 1 GB single-end libraries (Datasets 5 and 6). Compared with the September snapshot, wall time fell (Dataset 1: 127.4 s → 117.1 s) while peak memory rose with the reference library (0.23 → 0.35 GB), because a 45% larger index is held in memory: the probabilistic engine trades memory for a larger search space, not time. The alignment engine remains the slower, heavier and more auditable path: re-run against the delivered library it needed 302.4 s and 1.28 GB on Dataset 1 and 187.0 s and 2.80 GB on Dataset 6, and 30.4 min for the full 18-dataset suite against 9.7 min — a 3.1× total wall-time ratio (median per-dataset 2.4×, range 0.9–5.7×) and 2.2–3.9× more peak memory on the large datasets. Both engines were faster than in the September snapshot (Dataset 1: 127.4 → 117.1 s and 384.9 → 302.4 s); peak memory moved in opposite directions, rising for the probabilistic engine (233.6 → 349.5 MB) with the larger index and falling for the alignment engine (2,474 → 1,276 MB), a change we record rather than explain. Mapping-based consensus added 4–142 s per dataset (~9 min for 16 datasets). The probabilistic engine is therefore the default, and the alignment engine is retained as the countable, true-alignment audit path for near-identical references and low-frequency strains.

End-to-end, a 20-sample run of the complete 15-stage pipeline on Datasets 1–10 recovered 23 of 28 truths and 31 of 36 when Datasets 11–18 are included, at 2.2 GB peak process memory, with no run failures. Identification alone now reaches the same 23/28, so the gap that the earlier end-to-end chain exposed was library coverage, not stage coupling — the same lesson as Section 3.3, measured through a different route.

**Table 9.** Throughput and peak process-tree memory (delivered 12,196-record library; both engines re-run 2026-10-27; September values in parentheses).

| Run | Probabilistic engine | Alignment engine |
|---|---|---|
| All 18 discovery datasets, identification + filtering (19 threads) | 9.7 min total; 0.35 GB (≤1.27 GB on the 1 GB single-end sets) | 30.4 min total; ≤2.82 GB (Datasets 5–6) |
| Dataset 1 | 117.1 s; 349.4 MB (September: 127.4 s; 233.6 MB) | 302.4 s; 1,276.0 MB (September: 384.9 s; 2,474.2 MB) |
| Dataset 3 | 75.7 s; 349.8 MB | 358.9 s; 1,270.6 MB |
| Dataset 6 | 37.5 s; 1,271.3 MB (September: 35.9 s; 619.4 MB) | 187.0 s; 2,795.1 MB (September: 274.0 s; 8,783.7 MB) |
| Mapping-based consensus (16 datasets) | — | 4–142 s per dataset (~9 min total) |

### 3.8 Quality assurance and distribution

The automated estate of 85 scripts (30 integration, 20 check, four unit, plus audit/probe/guard scripts and a 219-case pytest suite) runs against both source and packaged builds; the 26 curated example result sets are regenerated by scripts and exposed through a read-only API, doubling as visual regression fixtures. Because the platform is meant to support auditable decisions, every identification threshold, filtering decision and discard reason is emitted as a machine-readable table, with per-sample failure records that state explicitly that failure is not a negative. Growing the reference library is a maintenance activity with a measured cost (Section 3.3), and the library manifests — build date, record count, per-index configuration — are shipped so that a published number and a laboratory's own run can be tied to the same library.

---

---

## 4. Discussion

VirusPlatform shows that the complete plant-virus HTS path — reads to recombination-aware, phylogeographically contextualised inference — can be delivered as one offline Windows application, and its benchmark shows something less expected: on an inter-laboratory ground truth, what limited discovery sensitivity was the completeness of the reference library and the tier at which a call was drawn, not the alignment engine. Both findings matter for how plant virology evaluates detection software, and both are the reason this paper reports configuration and tier with every number.

**Positioning.** Relative to discovery pipelines such as VirusDetect [8] and VirusSeeker [9], VirusPlatform extends from detection through evolutionary analysis; relative to containerized virome pipelines such as ViroProfiler [10], it trades multi-tenant scalability for zero-infrastructure operability, a deliberate choice because locked-down government workstations often prohibit container runtimes and virtualization; relative to desktop tools such as BioAider [15], it adds the upstream detection and quantification path and a job-managed, sample-centric data model. At the component level it complements rather than competes with k-mer classifiers [11,12] and contig-level identifiers [13,14]: it uses k-mer classification where speed dominates (host removal, contig triage) and reference alignment where statistical defensibility dominates (detection and quantification). The dual-engine arrangement — a probabilistic engine that apportions multi-mapping reads among close references, an alignment engine that yields countable alignments, and a Poisson-ratio safeguard that turns low-level-hit ambiguity into an explicit criterion — is, to our knowledge, not available as such in plant-virus platforms; on our panel it is also arbitrated by measurement, since the probabilistic engine finished the suite in roughly one-eighth of the alignment engine's wall time at about one-third of its memory, which is why it is the default.

The benchmark literature in this field rarely reports shared, per-target ground truth at this granularity, and we treat the configuration- and tier-explicit reporting as part of the contribution. It also reframes a debate: when two platforms disagree on a sample, the difference may be documentary (one library holds a strain-divergent isolate, the other does not) and cannot be settled by comparing aligners on the same library; library manifests that record build date and record count, as shipped here, are the minimum needed to make such comparisons meaningful. Our audit makes the same point quantitatively: five strain-level records moved one dataset from 14% to 100% recovery, and 30 accession-level records across eight designed mixtures moved per-isolate recovery to 101.8%.

**The tier model.** The three tiers are an explicit answer to a real dilemma. A single confirmed table must either accept many spurious low-level hits (and lose precision) or reject true low-titre infections (and lose sensitivity); both errors are common in plant virology, where infections are frequently mixed and low-titre. Splitting the output lets the machine do what it does well — present candidates with their statistics, graded, and with a nearest same-genus species when the reference is imperfect — while keeping the decision with a human. The cost is that the two-tier number is not a fully automated one, and we report it as such: 26/28 required triaging six rows, three of which were true. We consider this the honest way to report a decision-support tool, but it also means that the headline depends on how one counts a "detection", and Section 3.4 gives all four tiers so that a reader can choose the definition that matches their use.

**Design trade-offs.** Three choices merit discussion. First, recombination detection deliberately wraps RDP5's nine methods [43] rather than reimplementing them: our in-house prototype of triplet-based methods produced 18 detections against one true event on synthetic chimeras and was removed — a negative result reported as a caution against informal re-implementations. Second, phylodynamics favours parsimony and regression over Bayesian inference: Fitch reconstruction and root-to-tip regression answer operational questions (rate, direction of spread) in seconds on desktop hardware, whereas Bayesian phylogeography imposes runtimes and configuration expertise incompatible with the target users; TreeTime remains available for calibration. Third, the platform ships as a loopback-only single-user application without authentication, designed for one workstation and one analyst; we state this explicitly to prevent misdeployment as a shared service. Networked components — public metadata retrieval with optional AI cleaning, and the optional agent — are separated from the analysis core, require explicit configuration, and never transmit raw sequencing data.

**The AI analysis-guidance layer.** The optional agent is scoped as interpreter and drafter, not decision-maker: every quantity in its context is computed by deterministic code from on-disk artifacts, so its output can err in wording but not in numbers; its execution authority defaults to a human-approved action ticket; its action registry contains no destructive operation. This suits a diagnostic-adjacent tool, where the agent can accelerate comprehension while each conclusion remains traceable to code and artifacts, and the platform's capability is unchanged when no model service is configured.

**Limitations.** Five boundaries are stated plainly. First, this is an inter-laboratory benchmark, not a field diagnostic study. The VIROMOCK and Bari panels combine designed mixtures with real isolates whose truth values were established at design time or by participating laboratories [55,56]; no field sample here carries RT-PCR or qPCR confirmation, so concordance with diagnostic practice and the limit of detection on natural low-titre material remain unmeasured — the single most important next step before diagnostic use. Second, the tiers must not be conflated when quoting numbers. The reference-present figure (46/46) measures recovery of targets installed in the library and is an upper bound; the discovery figure (23/28, 82.1%) describes a laboratory that opens the platform as delivered; the two-tier figure (26/28) includes a human triage step; and the 28/28 figure uses a non-default threshold. Any published summary should name configuration, tier and library build. Third, precision is the weakest published property: 30.7% at the confirmed tier on the ten discovery datasets, with 52 false-positive species instances, and it deteriorated as the library grew from 41 to 52 instances as near-neighbour sensitivity improved. The Poisson-ratio safeguard, the two-track cut-offs and the 95% ANI line remain screening heuristics rather than per-family calibrated statistics, and calibration against ring-trial data is future work. Fourth, the September snapshot library no longer exists, so no number here can be re-derived from that build. Both discovery configurations have since been re-run against the delivered library and archived with input digests and library fingerprints (probabilistic 23/28, alignment 20/28 at the confirmed tier; the September run voided one alignment dataset and scored 19 of 27). The strain-level reference-present figures still come from the 2026-09-15 task library, so a complete archival chain exists for the discovery configuration only. Fifth, scope: the platform targets short-read data. Native long-read (ONT) workflows are not implemented; the small-RNA regime is supported but weak (6/11 confirmed, 11/11 after triage), limited by both read length and the host-removal k-mer fixed at 35, which is uninformative for 21–24 nt reads; and no head-to-head comparison with other software on the same data was performed, so the benchmark establishes absolute performance against truth, not relative standing.

**Future work.** Priorities follow directly from the limitations: a field-sample concordance study against RT-PCR/qPCR on quarantine-relevant material; an engine-level head-to-head on a common library, to separate aligner from library effects that this study separated only operationally; completion of the alignment-engine discovery re-runs so that every number can be re-derived in place; a small-RNA host-depletion and threshold path that does not depend on the fixed k = 35 index; a validated ONT workflow; automated reconciliation of the library against ICTV releases with per-record provenance; per-family calibration of the Poisson and coverage gates from ring-trial data; a server mode with authentication for multi-user laboratories; and a structured usability evaluation with quarantine and certification staff, so far supported only by bundled examples and bilingual documentation.

## 5. Conclusion

VirusPlatform consolidates detection, quantification, verification, comparative genomics and evolutionary dynamics for plant-virus HTS data into a single offline Windows application, and its benchmark — reported by reference configuration and by decision tier — shows that on a shared inter-laboratory ground truth the sensitivity of reference-based discovery was governed by the completeness of the reference library and by where the decision threshold was drawn: 23/28 species-level truth recovered at the confirmed tier with the delivered library, 26/28 after triage of six review rows, 28/28 when the assembly route and a deliberately lowered gate were allowed, against a reference-present ceiling of 46/46 that we label as an upper bound. Library repair, not aligner tuning, produced most of the gain, and it came at a measurable precision cost that we report. Concordance with diagnostic RT-PCR/qPCR results on field samples is the identified gate between research use and diagnostic use.

---

## Figure legends

**Figure 1. VirusPlatform architecture and the 15-stage pipeline.** Paired or single-end FASTQ input enters six stage groups (preprocessing; identification; assembly; verification; host and annotation; downstream and reporting) executed by a dependency-aware, resumable stage engine with input-fingerprint validation. The loopback-only Flask shell exposes 248 routes in a bilingual interface; a two-tier task manager (heavy 2, light 4 concurrent jobs) provides queued, streaming, restart-safe execution; the PyInstaller distribution bundles 30 external tools and, in the measured package, 2.9 GB of program, examples and reference library.

**Figure 2. Known-virus identification and quantification suite.** Reads are quantified against the 12,196 curated plant-virus records of the delivered library by either the probabilistic engine (salmon k = 31 expectation–maximization, multi-mapping reads apportioned probabilistically) or the alignment engine (minibwa, true alignments with per-reference unique counts); three coverage backends yield bit-identical output. Candidates pass a base gate and at least one of two statistical tracks (Track A: coverage, depth and Poisson-ratio safeguard; Track B: gene-region rescue for compact RNA genomes), segment completeness for multipartite viruses, and ANI classification (≥ 95% confirmed; < 95% suspected novel), and are reported in three tiers — confirmed, review (relaxed gates, graded rows, for human triage) and discovery (assembly-based rescue, with a suggestion file). Outputs include per-decision machine-readable tables, consensus (viral_consensus) and variant catalogues (bcftools + SnpEff).

**Figure 3. Evolutionary-dynamics module.** (A) Recombination: alignment quality-control pre-filter (length, gap and ambiguity criteria; identity criterion only with an explicit reference), RDP5 nine-method detection, evidence table (methods with p < 0.05; best p-value as minimum), interval merging including circular split ends, and masking to a clean alignment. Inset: measured behaviour on the bundled ten-isolate CMV RNA3 alignment. (B) Root-to-tip regression: midpoint rerooting search maximizing R²; slope estimates substitutions/site/year. (C) Fitch-parsimony phylogeography: bottom-up/top-down state reconstruction yielding an annotated tree, migration matrix and transition list. (D) Within-sample population genetics: Ts/Tv spectra, allele-frequency spectra, π~N~/π~S~ per coding region, sliding-window π and Tajima's D under a pooled-sequencing model.

**Figure 4. Pairwise SDT identity matrix of the bundled CMV RNA3 demonstration set (measured).** Computed with the platform's own formulation from the bundled alignment (ten global isolates; 2,292 columns). The matrix shows block structure (75.6–99.2%; mean against the RefSeq reference 89.1%) consistent with known CMV subgroup diversity on RNA3 — within-subgroup identities of 98–99% (RefSeq, France, Germany, Slovenia; Australia, Iran, Poland), approximately 75–77% between subgroups, and intermediate 91–93% identities for the China, India and South Korea isolates. The demonstration also shows why the 98% identity criterion is gated on an explicit reference: used with the RefSeq isolate, it would remove the six cross-subgroup isolates.

**Supplementary figures.** Ten benchmark figures — engine-neutral detection comparison, per-dataset quantification error ranking, consensus breadth and depth at the detection threshold, Poisson-ratio and coverage distributions, throughput and memory, contig-threshold sweep, and the design-versus-observed comparison — are archived with the benchmark materials (`docs/paper/benchmark_20261019/figs/`, Figures S1–S10, PNG and SVG) and are not embedded in the printed manuscript.

---

## Availability and requirements

- **Project name:** VirusPlatform v1.0 (analysis engine of 2026-10; delivered plant-virus reference library build of 2026-10-19, 12,196 records).
- **Operating system:** Windows 10/11 x64; Python 3.12 for source deployment (dependencies pinned in `requirements.lock`).
- **Programming languages:** Python 3.12; framework-free JavaScript front end; 30 bundled precompiled third-party tools.
- **Source-code availability:** The complete source (analysis core, web shell, test suite, `tests/bench/` benchmark harness) is deposited in a public GitHub repository (https://github.com/zhangwenda0518/PlantVirusAnalysisPlatform; release tags v1.0.0–v1.2.0) under the MIT license, with archival on Zenodo pending DOI minting. Bundled third-party binaries under `3rd/` retain their upstream licenses and are indexed in `THIRD_PARTY_NOTICES.md`; the library build manifest ships with the distribution.
- **Benchmark data and materials:** VIROMOCK challenge data are public [56,57] (archived at Dryad, doi:10.5061/dryad.0zpc866z8, CC0-1.0) and the Bari small-RNA panel is published with its inter-laboratory evaluation [55]. The 2026-10-19 benchmark archive — four optimisation rounds, per-dataset scores, machine-readable results, ten supplementary figures and the plotting scripts — is provided as `docs/paper/benchmark_20261019/`; the discovery-configuration re-runs against the delivered library, for both engines, are archived as `docs/paper/benchmark_rerun_20261027/` (driver, scorer, library fingerprints, per-dataset input SHA-256 values, results and comparison tables); the task-specific reference-present library `viromock_kv` (61 sequences with manifest, 2026-09-15) is included under `databases/virusref_db/`.
- **Evaluation environment:** runs used the v1.0 engine and the libraries documented in Sections 2.13 and 3.3–3.7; datasets were processed sequentially on a single workstation with 19 threads, wall time and peak process-tree memory sampled at 1 Hz.
- **Example data:** 18 example inputs and 26 curated example result sets ship with the distribution; the CMV RNA3 demonstration set uses public RefSeq/GenBank sequences (NC_001440.1 and accessions PP942736.1, OQ514051.1, OL472039.1, OM621808.1, ON013883.1, PP928859.1, PP928864.1, ON013890.1, MG882749.1).
- **Any restrictions to use by non-academics:** none; the project's own code is MIT-licensed and bundled third-party tools remain under their upstream licenses (see `THIRD_PARTY_NOTICES.md`).

## Abbreviations

HTS: high-throughput sequencing; ANI: average nucleotide identity; ORF: open reading frame; SDT: Sequence Demarcation Tool; RTT: root-to-tip; SRA: Sequence Read Archive; GSA: Genome Sequence Archive; VMR: Virus Metadata Resource; ICTV: International Committee on Taxonomy of Viruses; SNV/iSNV: single nucleotide variant / intra-host SNV; EM: expectation–maximization; MAE: mean absolute error; pp: percentage points; PPV: plum pox virus; PVY: potato virus Y; CTV: citrus tristeza virus; TSWV: tomato spotted wilt virus.

## Acknowledgements

The author thanks the organisers and participants of the VIROMOCK inter-laboratory challenge and the authors of the Bari small-RNA inter-laboratory study for making their datasets, evaluation records and laboratory counts publicly available.

## Author contributions

**Wenda Zhang:** Conceptualization, Methodology, Software, Validation, Formal analysis, Investigation, Data curation, Writing – original draft, Writing – review & editing, Visualization, Supervision, Project administration. The author read and approved the final manuscript.

## Conflict of interest

The author declares that the research was conducted in the absence of any commercial or financial relationships that could be construed as a potential conflict of interest.

## References

1. Anderson PK, Cunningham AA, Patel NG, Morales FJ, Epstein PR, Daszak P. Emerging infectious diseases of plants: pathogen pollution, climate change and agrotechnology drivers. Trends Ecol Evol. 2004;19(10):535-544. doi:10.1016/j.tree.2004.07.021
2. Jones RAC, Naidu RA. Global dimensions of plant virus diseases: current status and future perspectives. Annu Rev Virol. 2019;6(1):387-409. doi:10.1146/annurev-virology-092818-015606
3. Hadidi A, Flores R, Candresse T, Barba M. Next-generation sequencing and genome editing in plant virology. Front Microbiol. 2016;7:1325. doi:10.3389/fmicb.2016.01325
4. Massart S, Candresse T, Gil J, Lacomme C, Predajna L, Ravnikar M, et al. A framework for the evaluation of biosecurity, commercial, regulatory, and scientific impacts of plant viruses and viroids identified by NGS technologies. Front Microbiol. 2017;8:45. doi:10.3389/fmicb.2017.00045
5. Villamor DEV, Ho T, Al Rwahnih M, Martin RR, Tzanetakis IE. High throughput sequencing for plant virus detection and discovery. Phytopathology. 2019;109(5):716-725. doi:10.1094/PHYTO-07-18-0257-RVW
6. Maliogka VI, Minafra A, Saldarelli P, Ruiz-Garcia AB, Glasa M, Katis N, Olmos A. Recent advances on detection and characterization of fruit tree viruses using high-throughput sequencing technologies. Viruses. 2018;10(8):436. doi:10.3390/v10080436
7. Kanapiya A, Amanbayeva U, Tulegenova Z, et al. Recent advances and challenges in plant viral diagnostics. Front Plant Sci. 2024;15:1451790. doi:10.3389/fpls.2024.1451790
8. Zheng Y, Gao S, Padmanabhan C, et al. VirusDetect: an automated pipeline for efficient virus discovery using deep sequencing of small RNAs. Virology. 2017;500:130-138. doi:10.1016/j.virol.2016.10.017
9. Zhao G, Wu G, Lim ES, Droit L, Krishnamurthy S, Barouch DH, Virgin HW, Wang D. VirusSeeker, a computational pipeline for virus discovery and virome composition analysis. Virology. 2017;503:21-30. doi:10.1016/j.virol.2017.01.005
10. Ru J, Khan Mirzaei M, Xue J, Peng X, Deng L. ViroProfiler: a containerized bioinformatics pipeline for viral metagenomic data analysis. Gut Microbes. 2023;15(1):2192522. doi:10.1080/19490976.2023.2192522
11. Wood DE, Lu J, Langmead B. Improved metagenomic analysis with Kraken 2. Genome Biol. 2019;20(1):257. doi:10.1186/s13059-019-1891-0
12. Menzel P, Ng KL, Krogh A. Fast and sensitive taxonomic classification for metagenomics with Kaiju. Nat Commun. 2016;7:11257. doi:10.1038/ncomms11257
13. Guo J, Bolduc B, Zayed AA, et al. VirSorter2: a multi-classifier, expert-guided approach to detect diverse DNA and RNA viruses in metagenomic samples. Microbiome. 2021;9(1):37. doi:10.1186/s40168-020-00990-y
14. Camargo AP, Roux S, Schulz F, et al. Identification of mobile genetic elements with geNomad. Nat Biotechnol. 2024;42(8):1303-1312. doi:10.1038/s41587-023-01953-y
15. Zhou ZJ, Qiu Y, Pu Y, Huang X, Ge XY. BioAider: an efficient tool for viral genome analysis and its application in tracing SARS-CoV-2 transmission. Sustain Cities Soc. 2020;63:102466. doi:10.1016/j.scs.2020.102466
16. Chen S, Zhou Y, Chen Y, Gu J. fastp: an ultra-fast all-in-one FASTQ preprocessor. Bioinformatics. 2018;34(17):i884-i890. doi:10.1093/bioinformatics/bty560
17. Shen W, Le S, Li Y, Hu F. SeqKit: a cross-platform and ultrafast toolkit for FASTA/Q file processing. PLoS One. 2016;11(10):e0163962. doi:10.1371/journal.pone.0163962
18. Lefkowitz EJ, Dempsey DM, Hendrickson RC, Orton RJ, Siddell SG, Smith DB. Virus taxonomy: the database of the International Committee on Taxonomy of Viruses (ICTV). Nucleic Acids Res. 2018;46(D1):D708-D717. doi:10.1093/nar/gkx932
19. Hatcher EL, Zhdanov SA, Bao Y, et al. Virus Variation Resource - improved response to emergent viral outbreaks. Nucleic Acids Res. 2017;45(D1):D482-D490. doi:10.1093/nar/gkw1065
20. Patro R, Duggal G, Love MI, Irizarry RA, Kingsford C. Salmon provides fast and bias-aware quantification of transcript expression. Nat Methods. 2017;14(4):417-419. doi:10.1038/nmeth.4197
21. Li H, Handsaker B, Wysoker A, et al. The Sequence Alignment/Map format and SAMtools. Bioinformatics. 2009;25(16):2078-2079. doi:10.1093/bioinformatics/btp352
22. Danecek P, Bonfield JK, Liddle J, et al. Twelve years of SAMtools and BCFtools. Gigascience. 2021;10(2):giab008. doi:10.1093/gigascience/giab008
23. Cingolani P, Platts A, Wang LL, et al. A program for annotating and predicting the effects of single nucleotide polymorphisms, SnpEff. Fly (Austin). 2012;6(2):80-92. doi:10.4161/fly.19695
24. Bankevich A, Nurk S, Antipov D, et al. SPAdes: a new genome assembly algorithm and its applications to single-cell sequencing. J Comput Biol. 2012;19(5):455-477. doi:10.1089/cmb.2012.0021
25. Buchfink B, Xie C, Huson DH. Fast and sensitive protein alignment using DIAMOND. Nat Methods. 2015;12(1):59-60. doi:10.1038/nmeth.3176
26. O'Leary NA, Wright MW, Brister JR, et al. Reference sequence (RefSeq) database at NCBI: current status, taxonomic expansion, and functional annotation. Nucleic Acids Res. 2016;44(D1):D733-D745. doi:10.1093/nar/gkv1189
27. Steinegger M, Soding J. MMseqs2 enables sensitive protein sequence searching for the analysis of massive data sets. Nat Biotechnol. 2017;35(11):1026-1028. doi:10.1038/nbt.3988
28. Lu S, Wang J, Chitsaz F, et al. CDD/SPARCLE: the conserved domain database in 2020. Nucleic Acids Res. 2020;48(D1):D265-D268. doi:10.1093/nar/gkz991
29. Camacho C, Coulouris G, Avagyan V, et al. BLAST+: architecture and applications. BMC Bioinformatics. 2009;10:421. doi:10.1186/1471-2105-10-421
30. Larralde M. Pyrodigal: Python bindings and interface to Prodigal, an efficient method for gene prediction in prokaryotes. J Open Source Softw. 2022;7(72):4296. doi:10.21105/joss.04296
31. De Coninck L. pyrodigal-rv: RNA-virus-aware gene prediction based on Pyrodigal [computer software, v0.1.0]. GitHub. https://github.com/LanderDC/pyrodigal-rv
32. Katoh K, Standley DM. MAFFT multiple sequence alignment software version 7: improvements in performance and usability. Mol Biol Evol. 2013;30(4):772-780. doi:10.1093/molbev/mst010
33. Capella-Gutierrez S, Silla-Martinez JM, Gabaldon T. trimAl: a tool for automated alignment trimming in large-scale phylogenetic analyses. Bioinformatics. 2009;25(15):1972-1973. doi:10.1093/bioinformatics/btp348
34. Price MN, Dehal PS, Arkin AP. FastTree 2 - approximately maximum-likelihood trees for large alignments. PLoS One. 2010;5(3):e9490. doi:10.1371/journal.pone.0009490
35. Nguyen LT, Schmidt HA, von Haeseler A, Minh BQ. IQ-TREE: a fast and effective stochastic algorithm for estimating maximum-likelihood phylogenies. Mol Biol Evol. 2015;32(1):268-274. doi:10.1093/molbev/msu300
36. Minh BQ, Schmidt HA, Chernomor O, et al. IQ-TREE 2: new models and efficient methods for phylogenetic inference in the genomic era. Mol Biol Evol. 2020;37(5):1530-1534. doi:10.1093/molbev/msaa015
37. Kalyaanamoorthy S, Minh BQ, Wong TKF, von Haeseler A, Jermiin LS. ModelFinder: fast model selection for accurate phylogenetic estimates. Nat Methods. 2017;14(6):587-589. doi:10.1038/nmeth.4285
38. Hoang DT, Chernomor O, von Haeseler A, Minh BQ, Vinh LS. UFBoot2: improving the ultrafast bootstrap approximation. Mol Biol Evol. 2018;35(2):518-522. doi:10.1093/molbev/msx281
39. Guindon S, Dufayard JF, Lefort V, Anisimova M, Hordijk W, Gascuel O. New algorithms and methods to estimate maximum-likelihood phylogenies: assessing the performance of PhyML 3.0. Syst Biol. 2010;59(3):307-321. doi:10.1093/sysbio/syq010
40. Koressaar T, Remm M. Enhancements and modifications of primer design program Primer3. Bioinformatics. 2007;23(10):1289-1291. doi:10.1093/bioinformatics/btm091
41. Untergasser A, Cutcutache I, Koressaar T, Ye J, Faircloth BC, Remm M, Rozen SG. Primer3 - new capabilities and interfaces. Nucleic Acids Res. 2012;40(15):e115. doi:10.1093/nar/gks596
42. Muhire BM, Varsani A, Martin DP. SDT: a virus classification tool based on pairwise sequence alignment and identity calculation. PLoS One. 2014;9(9):e108277. doi:10.1371/journal.pone.0108277
43. Martin DP, Varsani A, Roumagnac P, Botha G, Maslamoney S, Schwab T, Kelz Z, Kumar V, Murrell B. RDP5: a computer program for analyzing recombination in, and removing signals of recombination from, nucleotide sequence datasets. Virus Evol. 2021;7(1):veaa087. doi:10.1093/ve/veaa087
44. Martin DP, Murrell B, Golden M, Khoosal A, Muhire B. RDP4: detection and analysis of recombination patterns in virus genomes. Virus Evol. 2015;1(1):vev003. doi:10.1093/ve/vev003
45. Rambaut A, Lam TT, Carvalho LM, Pybus OG. Exploring the temporal structure of heterochronous sequences using TempEst (formerly Path-O-Gen). Virus Evol. 2016;2(1):vew007. doi:10.1093/ve/vew007
46. Fitch WM. Toward defining the course of evolution: minimum change for a specific tree topology. Syst Zool. 1971;20(4):406-416. doi:10.2307/2412116
47. Sagulenko P, Puller V, Neher RA. TreeTime: maximum-likelihood phylodynamic analysis. Virus Evol. 2018;4(1):vex042. doi:10.1093/ve/vex042
48. Leinonen R, Sugawara H, Shumway M; International Nucleotide Sequence Database Collaboration. The Sequence Read Archive. Nucleic Acids Res. 2011;39(Database issue):D19-D21. doi:10.1093/nar/gkq1019
49. Katz K, Shutov O, Lapoint R, Kimelman M, Brister JR, O'Sullivan C. The Sequence Read Archive: a decade more of explosive growth. Nucleic Acids Res. 2022;50(D1):D387-D390. doi:10.1093/nar/gkab1053
50. CNCB-NGDC Members and Partners. Database resources of the National Genomics Data Center, China National Center for Bioinformation in 2026. Nucleic Acids Res. 2026;54(D1):D28-D47. doi:10.1093/nar/gkaf1172
51. Chen M, Ma Y, Wu S, et al. Genome Warehouse: a public repository housing genome-scale data. Genomics Proteomics Bioinformatics. 2021;19(4):584-589. doi:10.1016/j.gpb.2021.04.001
52. Europe PMC Consortium. Europe PMC: a full-text literature database for the life sciences and platform for innovation. Nucleic Acids Res. 2015;43(D1):D1042-D1048. doi:10.1093/nar/gku1061
53. Li H. Minimap2: pairwise alignment for nucleotide sequences. Bioinformatics. 2018;34(18):3094-3100. doi:10.1093/bioinformatics/bty191
54. Tamisier L, Haegeman A, Foucart Y, Fouillien N, Al Rwahnih M, Buzkan N, Candresse T, Chiumenti M, De Jonghe K, Lefebvre M, Margaria P, Reynard JS, Stevens K, Kutnjak D, Massart S. Semi-artificial datasets as a resource for validation of bioinformatics pipelines for plant virus detection. Peer Community Journal. 2021;1:e53. doi:10.24072/pcjournal.62
55. Massart S, Chiumenti M, De Jonghe K, Glover R, Haegeman A, Koloniuk I, Komínek P, Kreuze J, Kutnjak D, Lotos L, Maclot F, Maliogka V, Maree HJ, Olivier T, Olmos A, Pooggin MM, Reynard JS, Ruiz-García AB, Safarova D, Schneeberger PHH, Sela N, Turco S, Vainio EJ, Varallyay E, Verdin E, Westenberg M, Brostaux Y, Candresse T. Virus detection by high-throughput sequencing of small RNAs: large-scale performance testing of sequence analysis strategies. Phytopathology. 2019;109(3):488-497. doi:10.1094/PHYTO-02-18-0067-R
56. Tamisier L, Haegeman A, Foucart Y, Fouillien N, Al Rwahnih M, Buzkan N, Candresse T, Chiumenti M, De Jonghe K, Lefebvre M, Margaria P, Reynard JS, Stevens K, Kutnjak D, Massart S. VIROMOCK challenge datasets [data set]. Dryad. doi:10.5061/dryad.0zpc866z8
57. Institute for Agricultural and Fisheries Research (ILVO). VIROMOCK challenge: datasets, truth tables, and scoring rules. GitLab repository. https://gitlab.com/ilvo/VIROMOCKchallenge
