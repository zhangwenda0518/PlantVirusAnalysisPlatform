# VirusPlatform: an integrated, offline-deployable web platform for end-to-end plant virus detection, quantification, and evolutionary analysis on desktop workstations

**Running title:** VirusPlatform — offline plant virus analytics

**[Author One]<sup>1</sup>, [Author Two]<sup>2</sup>, [Author Three]<sup>1,\*</sup>**

<sup>1</sup> [Affiliation 1], [City], China
<sup>2</sup> [Affiliation 2], [City], China
\* Correspondence: [corresponding.author@email]

---

## Abstract

**Background:** Plant viruses cause severe, increasingly globalized crop losses, and high-throughput sequencing (HTS) has become the method of choice for their detection and characterization. However, the computational workflows used in plant virology remain fragmented across dozens of command-line tools that are typically designed for Linux servers or cloud environments, leaving many plant quarantine, certification, and breeding laboratories without practical access to end-to-end analysis.

**Results:** Here we present VirusPlatform, an integrated, locally deployable analysis platform that runs entirely offline on Windows desktop workstations and packages the full analytical path from raw HTS reads to publication-ready evolutionary inferences. The platform combines (i) a declarative 15-stage pipeline covering quality control, host read removal, known-virus identification and quantification, metagenomic assembly, candidate verification, consensus and variant calling, host prediction, ORF prediction and annotation, phylogenetics, primer design, and genome visualization; (ii) a curated reference database of 8,464 plant-virus sequences with dual quantification engines (salmon expectation–maximization and true-alignment counting) and a Poisson-based statistical safeguard against spurious identifications; (iii) an evolutionary-dynamics module that wraps the nine recombination-detection methods of RDP5, implements root-to-tip regression, Fitch-parsimony phylogeographic reconstruction, and within-population genetics metrics; (iv) an epidemiological browser covering approximately 199,000 publicly available plant-virus sequence records with seven analytical views; and (v) AI-assisted harmonization of public metadata from NCBI SRA and NGDC GSA. The Flask-based web application exposes 212 documented API routes and 24 pages in a bilingual (Chinese/English) interface, manages concurrent analysis jobs through a two-tier semaphore task engine, and is distributed as a one-click PyInstaller package (~1 GB program, 3.6 GB optional database bundle) bundling 30 external bioinformatics tools.

**Conclusions:** VirusPlatform consolidates the complete plant-virus bioinformatics workflow — detection, quantification, verification, evolutionary dynamics, and public-data traceability — into a single offline desktop application, lowering the infrastructure and expertise barriers for plant virology laboratories.

**Keywords:** plant viruses; viroids; high-throughput sequencing; virus identification; recombination; phylodynamics; phylogeography; web platform; offline deployment

---

## 1. Introduction

Plant viruses are among the most consequential pathogens of cultivated crops: together with their viroid relatives they reduce yield and quality across essentially all major crops, and their spread has been amplified by international trade in vegetative planting material, globalization of seed supply chains, and climate-driven shifts in vector distributions [1]. Because a large fraction of plant viruses persist latently or at low titers in symptomless hosts, and because mixed infections are the norm rather than the exception, molecular diagnostic assays targeting single species are increasingly complemented — and in discovery, quarantine, and certification contexts effectively replaced — by high-throughput sequencing (HTS) [2,3]. International frameworks for the evaluation of viruses discovered by HTS now exist, and HTS has become routine in germplasm indexing, certification schemes, and post-entry quarantine [3,4].

The analytical side of HTS, however, remains a substantial bottleneck. Detection of viruses in plant HTS data requires a long chain of computational steps — read quality control, host read subtraction, reference-based identification, de novo assembly, candidate verification, consensus generation, and evolutionary analyses — that are dispersed across command-line utilities written for Linux servers. Early discovery pipelines such as VirusDetect [5] and VirusSeeker [6] automated parts of this chain but were designed either for specific input types (small RNAs) or for cloud deployment, and none cover the downstream evolutionary analyses (recombination, temporal signal, phylogeography, within-population genetics) that modern plant virology increasingly demands. Desktop graphical tools for virus genomics exist — BioAider being a prominent example [7] — but focus on post-detection genome analysis rather than the full detection-to-evolution path, and integrated platforms covering the entire workflow are typically server software that presupposes dedicated infrastructure and command-line proficiency.

This gap is particularly acute for plant quarantine stations, certification laboratories, and breeding programs, which are overwhelmingly equipped with Windows workstations, often operate on air-gapped networks (sequencing data of regulated pathogens may not legally leave the premises), and rarely employ dedicated bioinformaticians. For these users, neither a collection of Linux scripts nor a cloud service is a viable solution; what is needed is a single application that installs by double-clicking, runs entirely offline, and produces defensible results.

Here we present VirusPlatform, an integrated, offline-deployable web-platform application for plant virus analysis that addresses this gap. Its main contributions are:

1. **A unified 15-stage analytical pipeline** spanning quality control, host removal, known-virus identification and quantification, assembly, candidate verification, consensus and variant calling, host prediction, ORF prediction and functional annotation, phylogenetic analysis, primer design, genome plotting, and HTML reporting, with declarative stage registration, resumable execution, and self-learning progress estimation.
2. **A curated known-virus identification suite** built on a reference database of 8,464 plant-virus sequences annotated with 26 metadata fields, offering two orthogonal quantification engines (probabilistic and alignment-based), a two-track statistical filtering model with a Poisson expectation safeguard, segment-completeness enforcement for multipartite viruses, and ANI-based confirm/novel classification.
3. **An evolutionary-dynamics module** that wraps all nine recombination-detection methods of RDP5 behind a quality-controlled web workflow, implements dependency-free root-to-tip regression and Fitch-parsimony phylogeographic reconstruction, and provides within-population population-genetic estimates (πN/πS, Tajima's D, sliding-window diversity).
4. **Public-data traceability**, including harmonization of NCBI SRA and NGDC GSA metadata into a unified 14-column schema with AI-assisted cleaning under a local-rules-first arbitration policy, and a seven-tab epidemiological browser over ~199,000 public plant-virus sequence records.
5. **Desktop-scale engineering** for non-expert users: a loopback-only Flask server with CSRF/DNS-rebinding protections, a two-tier semaphore task engine with live streaming progress, fully resumable jobs, a bilingual interface, and one-click PyInstaller distribution bundling 30 external bioinformatics tools.

---

## 2. Materials and Methods

### 2.1 Architecture overview

VirusPlatform is implemented as a Python 3.12 application comprising a 96-file core package (51,733 lines of code) organized into an analysis layer (`Virus_Platform_Core`), a Flask-based web layer, and a single-page web front end (25 HTML templates, ~12,900 lines; ~9,500 lines of framework-free JavaScript). The server binds exclusively to the loopback interface (127.0.0.1), probes a predefined port sequence starting at 8765 with random fallback, and validates the Host, Origin, and Referer headers against loopback origins to mitigate cross-site request forgery and DNS-rebinding attacks; destructive endpoints additionally require an explicit confirmation token. All file operations are funneled through path-guarding helpers that reject path traversal outside the platform root, and all external commands execute with argument lists (never a shell), UTF-8/GBK-safe decoding, a default six-hour timeout watchdog, and cooperative cancellation. The user interface is accessible either through an embedded desktop window (pywebview/WebView2) or through the system browser, with automatic fallback, and the entire interface is bilingual (Chinese/English, ~1,856 translation keys per language).

Analysis jobs are executed by a task manager that enforces two concurrency tiers — heavy tasks (classification, assembly, annotation, tree building, exact identity matrices; default maximum 2) and light tasks (conversion, plotting, retrieval; default maximum 4) — with FIFO queuing, JSON-persisted task state, and automatic recovery of interrupted tasks on restart. The front end monitors tasks through polling and server-sent events (SSE). The application exposes 212 HTTP routes (24 page-level routes and 188 API routes), a count locked by an automated route-inventory regression test.

### 2.2 Analytical pipeline

The end-to-end pipeline (Figure 1) consists of 15 stages registered in a declarative stage table: read subsampling → fastp quality control [9] → FASTQ-to-FASTA conversion (seqkit [8]) → host read removal → known-virus identification (Section 2.3) → metagenomic assembly → candidate verification → consensus and variants → host prediction → ORF prediction → ORF functional annotation → phylogenetics → primer design → genome plotting → HTML report assembly. Stages are grouped in the interface into six workflow modules (preprocessing; virus identification; assembly; host prediction; downstream analysis; reporting).

Stage execution is governed by a dependency registry combined with a stable topological sort, so that any user-selected subset of stages runs in a valid, deterministic order without modification of the executor. Completed stages are marked with sentinel files, enabling resumption of partially completed analyses; the known-virus stage additionally validates reuse of previous results through input fingerprints (path, size, and modification time of reads and reference index), so stale results are never presented as fresh. Every analysis persists a project manifest recording all input files, all parameter values, completed stages, and the last 20 runs, providing auditability and reproducibility at the project level. Progress reporting uses an exponentially-weighted moving-average model of per-stage runtimes (with per-gigabyte normalization for large stages) to display a global progress bar and an estimated time to completion that improves with use.

Host read removal is performed by kunpeng (v0.7.12), a memory-efficient Rust k-mer classifier bundled with the platform, using user-built host indexes; the host index builder fragments long sequences into ≤1 Mb chunks with 34 bp overlaps (k − 1 for the default k = 35), guaranteeing zero k-mer loss at chunk boundaries while keeping memory bounded. Read quality control uses fastp [9]; optional deterministic subsampling takes the first N read pairs to bound runtimes during method development.

### 2.3 Known-virus identification and quantification suite

The known-virus suite (`kvsuite`) identifies and quantifies previously characterized plant viruses by aligning reads — prior to assembly — against a curated reference database of 8,464 plant-virus sequences. Each reference carries 26 metadata columns, including accession, taxid, NCBI and ICTV species names, segment designation, sequence type, topology, molecule type, completeness, geographic location, host, isolation source, and VMR (Virus Metadata Resource) genus/family/species mappings [39]. Segment designations are harmonized from 299 raw spelling variants observed in public records into a controlled vocabulary (e.g., DNA-A, RNA2, Segment 1, S/M/L), with context-aware rules for families with lineage-specific nomenclature (e.g., Geminiviridae DNA-A/DNA-B; Nanoviridae DNA-R…DNA-U), preserving the original annotation in a parallel column.

Two orthogonal quantification engines are provided. The default **probabilistic engine** builds a k = 31 salmon [10] index and estimates per-reference read counts by expectation–maximization, correctly apportioning multi-mapping reads — a property that matters for segmented and closely related viral genomes — while simultaneously emitting an alignment BAM for downstream use. The **alignment engine** uses minibwa (a minimal BWA implementation bundled with the platform) to produce true alignments and counts per-reference uniquely mapped reads directly from the alignment index. Three interchangeable coverage backends (pandepth, samtools-based, and a built-in Python implementation) were verified to produce bit-identical coverage and depth estimates on test samples, eliminating tool-dependency drift.

Candidate viruses must satisfy a base gate (total mapped reads > 0; unique reads ≥ 10; mean depth ≥ 0.5×) and at least one of two statistical tracks:

- **Track A (genomic):** genome coverage ≥ 10%, mean depth ≥ 0.5×, and a Poisson ratio ≥ 0.3. The Poisson ratio compares observed coverage with the coverage expected if reads were distributed independently at random along the reference. With λ = N·L<sub>read</sub>/L<sub>ref</sub> and expected support P = 1 − e<sup>−λ</sup>, the ratio is R = (Coverage/100)/P; R near 1 is consistent with random placement, whereas R ≪ 1 indicates coverage concentrated in a small fraction of the genome, a signature of spurious low-level hits.
- **Track B (rescue for RNA genomes):** gene-region total coverage ≥ 80% and average coverage ≥ 5%, allowing detection of compact RNA viruses whose intergenic regions may not be covered.

For multipartite viruses, a species is reported only if all of its segments present in the reference database are detected, preventing segment-incomplete false positives. Every passing reference is classified by read-level average nucleotide identity computed from the NM tag as ANI = (aln_len − NM)/aln_len over up to 10,000 sampled alignments per reference: ANI ≥ 95% yields a "confirmed" call, ANI < 95% flags a "suspected novel" variant, and missing ANI (probabilistic engine) is reported as undetermined rather than excluded. Outputs comprise a full summary table, a best-hit table, an unclassified/suspected-novel table, and discard lists with per-read reasons. For batch analyses, per-sample checkpoint files enable resume, and failed samples are recorded explicitly as "failure ≠ negative".

Consensus sequences for identified viruses are generated through a dedicated realignment chain (minibwa against a single-reference index, followed by viral_consensus with quality ≥ 20, depth ≥ 5, frequency ≥ 0.5, ambiguity = N); variant calling uses bcftools [19] (mpileup with max depth 100,000, base quality ≥ 13, BAQ disabled; QUAL ≥ 3.5; minimum frequency 0.05), and SNVs/iSNVs are annotated per reference with SnpEff [20], with viroids (no CDS) handled gracefully. A development benchmark on a mock mixed-virus sample (GQMIX.q06) against the full 8,464-reference index completed the alignment step in 5.1 s and the complete five-module known-virus workflow (identification → filtering → consensus → plotting → variant calling) in 7.9 s. Viral reads extracted from the identification BAM can serve directly as assembly input, concentrating de novo assembly on viral signal.

### 2.4 Assembly and candidate verification

De novo assembly uses SPAdes [11] in metaviral mode by default (rna and meta modes selectable), with configurable memory (default 64 GB), minimum contig length (default 500 bp), and assembly input (host-removed reads or kvsuite-extracted viral reads). Contigs are classified by kunpeng against a plant-virus protein/k-mer database and filtered by confidence.

Because classification alone does not establish that a contig is a virus, a dedicated verification stage applies two independent lines of evidence: (i) translated search (DIAMOND blastx [13]) against a bundled RefSeq [16] viral protein database, and (ii) conserved-domain confirmation (MMseqs2 [14] against the CDD [15] domain models), combined under a union rule into a four-tier verdict — known, novel (viral but without clear reference match), domain_only (viral domains without full-length support), and unclassified — plus a separate viroid detection route by blastn. Unclassified contigs are retained explicitly as the novel-virus candidate pool rather than silently discarded.

### 2.5 Host prediction, ORF prediction, and annotation

Viral host range is predicted by a cascade classifier trained on ICTV [39] host associations, cross-validated against NCBI-hosted host metadata with BLAST [12] fallback; results are visualized as Sankey and sunburst diagrams. ORFs are predicted with pyrodigal and its RNA-virus-aware variant pyrodigal-rv [21] (minimum 100 amino acids by default), and ORF sets from many samples can be functionally annotated in batch against the RefSeq viral protein database using DIAMOND, MMseqs2, or blastp (database auto-downloaded on first use, ~107 MB), with HMM-based (pyhmmer/CDD) confirmation as fallback; annotation outputs include per-ORF tables, functional category and family distributions, and a GFF3 track. A genome-diagnostic module reports internal stop codons, frameshifts, and completeness issues for annotated genomes.

### 2.6 Phylogenetics and comparative genomics

The phylogenetic stage groups sequences by BLAST top-hit species and, for each group, aligns references with MAFFT [24] (auto strategy by default, L-INS-i selectable; CJK-path handling via an ASCII relay directory), trims with trimAl [25] (automated1, with graceful fallback), and infers trees with one of three engines: a dependency-free neighbor-joining implementation over pairwise identity distances, FastTree 2 [26] (GTR+Γ), or IQ-TREE 3 [27] with ModelFinder [28] plus dual branch support (ultrafast bootstrap, 1,000 replicates [29]; SH-aLRT, 1,000 replicates). Public NCBI references can be pulled into alignments automatically, with hierarchical reference sampling strategies (by macro-area, genus, or lineage) adapted from a production quarantine tree pipeline. Two safeguards make the stage robust on desktop hardware: a 25,000 bp reference-length ceiling guards against MAFFT's quadratic memory behavior, and per-species trees prevent biologically uninformative cross-species alignments.

Comparative genomics tools provide pairwise whole-genome identity in the exact SDT v1.3 formulation [30] (re-implemented in pure Python for per-pair MAFFT alignment with gap-deleted identity, validated against the original), NT/AA identity matrices with composite heatmaps, structure comparison with identity/distance matrices, and interactive viewers for multiple alignments, trees, and heatmap matrices.

### 2.7 Recombination detection

Recombination analysis (Figure 2) is exposed as a single web workflow that wraps the RDP5 command-line executable (RDP5CL.exe) [31], which is bundled natively for Windows. Because RDP5 requires a homologous, equal-length alignment, an alignment quality-control pre-filter removes sequences failing any of four criteria: un-gapped length ratio < 0.90 relative to the reference (incomplete fragment); gap fraction > 0.10 (poisoned alignment); ambiguous-base fraction > 0.05 (low quality); and, only when an explicit outgroup reference is designated, pairwise identity < 98% (outlier isolation). The identity criterion follows the SDT formula and is intentionally gated on explicit reference designation: on a test set of ten CMV RNA3 isolates, applying it without a designated reference would have incorrectly removed 7 of 10 biologically valid isolates. Alignments failing minimum requirements (fewer than four sequences, per the RDP5 recommendation) are rejected with diagnostics rather than silently processed.

RDP5 is invoked with its default nine recombination-detection methods — RDP, GENECONV, Bootscan, MaxChi, Chimaera, SiScan, PhylPro, LARD, and 3Seq [31] — and results are parsed from the evidence table: every event row carries breakpoint coordinates, recombinant/minor/major parent assignments, and per-method p-values, with the event-level significance set defined as the methods reaching p < 0.05 and the best p-value defined as their minimum. All event intervals (including those split across circular-genome ends) are merged and the corresponding alignment columns masked to N, producing a recombination-free "clean" alignment that can be exported or fed directly into tree inference, reflecting the "detect recombination before building trees" principle. The module ships with a demonstration alignment of globally sampled CMV RNA3 isolates. An earlier in-house implementation of three triplet-based methods was removed after benchmarking on synthetic chimeras (six 1 kb sequences, true breakpoint at position 500) showed 18 detections against 1 true event; RDP5 is now the sole recombination engine.

### 2.8 Temporal signal, phylodynamics, and phylogeography

Three lightweight, dependency-free phylodynamic tools address the questions most frequently asked of plant-virus sequence data — how fast is it evolving, where did it come from, and how is diversity structured — without requiring the computational cost of Bayesian inference frameworks.

**Root-to-tip regression** quantifies temporal signal and estimates evolutionary rate. Sequences with sampling dates (parsed from CSV/TSV metadata or FASTA headers of the form `accession|location|year`) are placed on a tree viewed as an unrooted graph; the algorithm evaluates all possible midpoint rerootings and selects the root position maximizing R². The regression slope estimates the substitution rate (substitutions/site/year) and R² measures the strength of temporal structure, reproducing the TempEst [33] workflow in-process. At least five dated taxa are required.

**Phylogeographic reconstruction** assigns discrete geographic states to a tree using Fitch maximum-parsimony [32]: a bottom-up pass computes the minimal state set at each internal node, a top-down pass fixes internal states, and all parent→child transitions are tallied into a migration matrix. Outputs comprise an annotated Newick tree (internal nodes labeled with inferred regions), the transition matrix, a per-tip state table, and a full transition list, which together support spread-history narratives without the multi-day runtimes of Bayesian phylogeographic inference; downstream TreeTime [34] analysis remains available for users requiring time calibration.

**Within-population population genetics** computes, from the per-sample variant catalogs, transition/transversion spectra, allele-frequency spectra, SNPGenie-style [35] πN/πS (dN/dS) per coding region, sliding-window nucleotide diversity π, and Tajima's D under a pooled-sequencing model, enabling selective-pressure and demographic history screening of intra-host viral populations.

### 2.9 Public metadata integration and traceability

Interpreting viral sequence diversity requires sample metadata that public repositories store heterogeneously. VirusPlatform harmonizes metadata from two repositories — NCBI SRA [36] (E-utilities; 18 extraction dimensions including run, release/collection dates, location, source, tissue, host age/growth stage, library source, BioProject, and PMID) and China National GeneBank's NGDC GSA [37] (10 dimensions; HTML and Excel endpoints parsed with retry/backoff and on-disk caching) — into a unified 14-column schema, with Genome Warehouse [38] and Europe PMC used for BioProject-level and literature provenance, respectively.

Location strings are normalized to a three-level `country, province, city` format. Cleaning is performed by large-language-model services (OpenAI-compatible APIs; DeepSeek and Kimi endpoints are preconfigured) under a strict arbitration policy: locally extracted fields always take precedence over model output, the model may only fill gaps and reformat, and a sanitizer layer permits only subtraction, relocation, and reformatting operations — never fabrication — with every AI-touched field suffixed to preserve provenance. An optional interactive report generator (datavzrd) renders cleaned metadata as browsable tables.

### 2.10 The seven-tab epidemiological browser

A server-rendered virus explorer provides seven coordinated analytical views over a pre-built database of approximately 199,000 publicly available plant-virus sequence records: spatiotemporal trend analysis (Plotly time series by taxonomy and geography), whole-genome mutation browsing, a filterable record table (capped at 5,000 rows with explicit truncation notice), a primer database, host-range summaries, vector-transmission summaries, and per-virus profile pages. The module originated as a Dash application with 13 callbacks and was re-engineered as 13 native Flask JSON endpoints, eliminating the Dash runtime from the packaged application while preserving behavior.

### 2.11 Distribution, packaging, and quality assurance

The platform is distributed as a PyInstaller onedir bundle (`VirusPlatform.exe`) that embeds the Python runtime, the web front end (including the Plotly bundle for offline charting and tree/heatmap viewers), and 30 precompiled external tools (kunpeng, SPAdes, BLAST+, MAFFT, FastTree, IQ-TREE 3, trimAl, Gblocks, ClustalW, MUSCLE, seqkit, crabz, DIAMOND, MMseqs2, minimap2, minibwa, fastp, samtools, bcftools, salmon, pandepth, viral_consensus, aria2, sracha, table2asn, and others). Distribution follows a three-part layout — program (~1 GB), example datasets (~1 MB), and optional reference databases (~3.6 GB) — with an automated post-build self-check (`--cli selfcheck`) that verifies tool executability inside the packaged artifact; aggressive exclusion of scientific-Python transitive dependencies reduced the first-build artifact from 2.05 GB.

Quality assurance relies on an automated suite of 85 Python test scripts: 30 end-to-end integration tests (covering alignment, annotation, asynchronous UI behavior, background tasks, comparisons, concurrency, downloads, navigation, phylogenetics, submission, and more), 20 static/dynamic check scripts (route inventories, database paths, example manifests, console errors, i18n verification), 4 unit-test modules, plus audit, probe, and route-baseline guard scripts. A route-inventory guard locks the 212-endpoint surface against accidental changes. Eighteen example inputs (paired-end reads, CMV/PVY/TMV/viroid genomes, multi-isolate sets, a CMV RNA3 global recombination set, Newick trees) and 26 curated example result sets are bundled and browsable in the interface, providing both documentation and regression fixtures.

---

## 3. Results

### 3.1 Platform inventory

Table 1 summarizes the platform's analytical surface. The 15-stage pipeline covers the complete detection-to-evolution path; the 25 registered standalone tools (Table 2) cover workflows outside the linear pipeline, including format conversion, dsRNA design, exact SDT analysis, recombination, phylogeography, temporal-signal testing, and two one-click composite chains (a four-step identification-classification chain and a five-module quantification-consensus chain) that serialize multiple tools into single reproducible runs with symbolic-link result aggregation.

**Table 1.** The 15-stage analytical pipeline.

| Stage | Function | Key tools | Representative outputs |
|---|---|---|---|
| subsample | Deterministic read subsampling | — | sub_R1/R2.fastq.gz |
| fastp | Read QC and filtering | fastp [9] | clean reads, QC report |
| fq2fa | FASTQ→FASTA | seqkit [8] | conv_R1/R2.fa.gz |
| host | Host read removal | kunpeng | kept reads, host fraction |
| kvsuite | Known-virus ID and quantification | salmon/minibwa | identification tables, BAM, viral reads |
| assembly | Metagenomic assembly and contig classification | SPAdes [11], kunpeng, BLAST [12] | contigs, classification TSV |
| verify | Candidate verification (2-evidence) | DIAMOND [13], MMseqs2 [14], CDD [15] | calls.tsv (known/novel/domain_only/unclassified/viroid) |
| consensus | Consensus and variants | minimap2 [17], viral_consensus, bcftools [19] | consensus.fa, variants.tsv |
| hostana | Viral host prediction | ICTV cascade [39] | host_prediction.tsv, Sankey/sunburst |
| orf | ORF prediction | pyrodigal(-rv) [21] | faa/ffn/gff |
| orfa | ORF functional annotation | DIAMOND/MMseqs2/blastp + HMM | orf_annotation.tsv, GFF3 |
| phylo | Alignment, tree, identity matrix | MAFFT [24], trimAl [25], FastTree [26]/IQ-TREE [27] | tree.nwk, SDT matrix |
| primer | Primer design | primer3 [22,23] | primers.tsv |
| gbdraw | Genome plots | gbdraw / DNA Features Viewer | SVG circular/linear plots |
| report | HTML report | Plotly, pycirclize | report.html |

**Table 2.** The 25 standalone analysis tools (abridged by category).

| Category | Tools |
|---|---|
| Read processing | convert, fastp, hostremoval |
| Identification & classification | identify, assemble, contigs, verify, kvsuite (one-click kvchain) |
| Genome analysis | consensus, orf, orfa, genoplot, primer, dsrna |
| Comparative genomics | structcmp, sdt (exact SDT), identity, align, quicktree |
| Evolutionary dynamics | rdp (RDP5 nine methods), rtt (temporal signal), phylogeo (Fitch) |
| Composite chains | virchain (ID-classification chain), kvchain (quantification-consensus chain) |

### 3.2 Curated reference database and epidemiological browser

The bundled known-virus reference database contains 8,464 plant-virus sequences with 26 harmonized metadata columns linking NCBI taxonomy to ICTV/VMR species concepts [39]; segment designations are normalized from 299 raw public spellings into a controlled vocabulary. The accompanying epidemiological browser indexes approximately 199,000 public sequence records with seven analytical views (Section 2.10), enabling quarantine users to contextualize local findings — temporal trends, mutation prevalence, primer coverage, host and vector ranges — without leaving the application.

### 3.3 Engineering performance

Two development benchmarks quantify desktop-scale performance. On a mock mixed-virus sample (GQMIX.q06), alignment against the full 8,464-reference index (minibwa engine) completed in 5.1 s and the complete five-module known-virus workflow in 7.9 s. Three independent coverage backends produced bit-identical coverage and depth outputs on all tested samples, and the bundled sracha converter converts NGDC GSA `.sra` files to FASTQ.gz 5–13× faster than the SRA Toolkit's fasterq-dump as measured during integration. The route-inventory guard confirms a stable 212-endpoint API surface.

### 3.4 Quality assurance

The automated test estate comprises 85 scripts (30 integration, 20 check, 4 unit, plus audit/probe/guard scripts) that run against both source and packaged builds; 26 curated example result sets are regenerated by scripts and exposed through a read-only API, doubling as visual regression fixtures. The packaging self-check (`--cli selfcheck`) verifies every bundled tool's executability after build. Because VirusPlatform is intended as a defensible diagnostic aid, all identification thresholds, filtering decisions, and discard reasons are emitted as machine-readable tables (including per-sample failure records with "failure ≠ negative" semantics), supporting downstream audit.

### 3.5 Distribution footprint

The three-part distribution (program ~1 GB; examples ~1 MB; databases ~3.6 GB) installs by extraction on any Windows 10/11 x64 workstation without administrator rights, Python installation, or network access. The application starts in desktop-window or browser mode, probes ports automatically, and self-verifies on first run.

---

## 4. Discussion

VirusPlatform demonstrates that the complete plant-virus HTS analysis path — from raw reads to recombination-aware, phylogeographically contextualized inferences — can be packaged as a single offline desktop application. Its design responds to four constraints that distinguish plant quarantine and certification laboratories from the sequencing centers for which most viroinformatics software is written: Windows-only workstation estates, air-gapped networks, absence of dedicated bioinformaticians, and regulatory demands for auditable, reproducible decisions.

**Positioning.** Relative to discovery pipelines such as VirusDetect [5] and VirusSeeker [6], VirusPlatform extends coverage from detection through evolutionary analysis; relative to desktop tools such as BioAider [7], it adds the entire upstream detection/quantification path and a job-managed, sample-centric data model; relative to cloud platforms, it sacrifices multi-tenancy for legality, privacy, and operability on quarantine premises. The known-virus suite's dual-engine design is, to our knowledge, unusual: the probabilistic engine apportions multi-mapping reads among close references (critical for segmented viruses), while the alignment engine provides count auditability, and the Poisson-ratio safeguard converts the classic "low-level BLAST hit" ambiguity into an explicit statistical criterion.

**Design trade-offs.** Three choices merit discussion. First, recombination detection deliberately wraps RDP5's nine methods rather than reimplementing them: our in-house prototype of triplet-based methods produced 18 detections against one true event on synthetic chimeras and was removed, a decision we report here as a negative result illustrating the difficulty of reproducing published heuristics faithfully. Second, phylodynamics favors parsimony and regression over Bayesian inference: Fitch reconstruction and root-to-tip regression answer the operational questions (rate, direction of spread) in seconds, whereas Bayesian phylogeography would impose runtimes and configuration expertise incompatible with the target users; TreeTime integration remains available for time calibration. Third, the platform ships as a loopback-only single-user application without authentication: it is designed for one workstation, one analyst, and air-gapped data, which we state explicitly to prevent misdeployment as a shared web service.

**Limitations.** The evaluation presented here is an implementation evaluation: the performance figures derive from development benchmarks and the test suite, not from a multi-dataset biological validation campaign, and we regard independent benchmarking on real plant-virus HTS datasets (sensitivity/specificity against diagnostic PCR ground truth, and against other pipelines) as the most important future work. The platform currently targets short-read data; native long-read (ONT) workflows, PCR-free assembly assessment, and viral quasispecies reconstruction beyond SNV-level variant calling are not yet integrated. Reference-based identification is bounded by database coverage — 8,464 curated references and continuous updates to ICTV/VMR mappings mitigate but do not eliminate the discovery bias against unstudied viral lineages. Finally, AI-assisted metadata cleaning, although arbitration-guarded and subtraction-only, can in principle propagate upstream metadata errors; all AI-touched fields remain provenance-flagged for manual review.

**Future work.** Planned extensions include a validated ONT pipeline, integration of time-calibrated phylogenetic inference behind the existing task engine, server mode with authentication for multi-user laboratory deployments, expansion of the reference database with automated monthly ICTV reconciliation, and publication of the biological benchmark study.

---

## 5. Conclusion

VirusPlatform integrates detection, quantification, verification, comparative genomics, and evolutionary dynamics for plant virus HTS data into a single, offline, bilingual desktop application with a defensible statistical identification model, wrapped RDP5 recombination analysis, dependency-free phylodynamics, harmonized public metadata, and one-click distribution. By matching the actual infrastructure and expertise constraints of plant quarantine, certification, and research laboratories, it lowers the barrier between HTS data and defensible virological conclusions.

---

## Availability and requirements

- **Project name:** VirusPlatform (植物病毒分析平台).
- **Operating system:** Windows 10/11 x64; Python 3.12 for source deployment.
- **Programming languages:** Python 3.12; JavaScript (framework-free front end); bundled Rust/C++ binaries.
- **License and source availability:** [To be completed upon publication.]
- **Example data:** 18 example inputs and 26 example result sets ship with the distribution.
- **Any restrictions to use by non-academics:** [To be completed upon publication.]

## Abbreviations

HTS: high-throughput sequencing; ANI: average nucleotide identity; ORF: open reading frame; SDT: Sequence Demarcation Tool; RTT: root-to-tip; SRA: Sequence Read Archive; GSA: Genome Sequence Archive; VMR: Virus Metadata Resource; ICTV: International Committee on Taxonomy of Viruses; SNV/iSNV: single nucleotide variant / intra-host SNV.

## Acknowledgements

[To be completed.]

## Author contributions

[To be completed.]

## Conflict of interest

The authors declare that the research was conducted in the absence of any commercial or financial relationships that could be construed as a potential conflict of interest.

---

## References

1. Anderson PK, Cunningham AA, Patel NG, Morales FJ, Epstein PR, Daszak P. Emerging infectious diseases of plants: pathogen pollution, climate change and agrotechnology drivers. Trends Ecol Evol. 2004;19(10):535–544. doi:10.1016/j.tree.2004.07.021
2. Hadidi A, Flores R, Candresse T, Barba M. Next-generation sequencing and genome editing in plant virology. Front Microbiol. 2016;7:1325. doi:10.3389/fmicb.2016.01325
3. Massart S, Candresse T, Gil J, Lacomme C, Predajňa L, Ravnikar M, et al. A framework for the evaluation of biosecurity, commercial, regulatory, and scientific impacts of plant viruses and viroids identified by high-throughput sequencing technologies. Front Microbiol. 2017;8:53. doi:10.3389/fmicb.2017.00053
4. Maliogka VI, Minafra A, Saldarelli P, Ruiz-García AB, Glasa M, Katis NI, et al. Recent advances on detection and characterization of fruit tree viruses using high-throughput sequencing technologies. Front Plant Sci. 2018;9:1267. doi:10.3389/fpls.2018.01267
5. Zheng Y, Gao S, Padmanabhan C, Li R, Galvez M, Gutiérrez D, et al. VirusDetect: an automated pipeline for efficient virus discovery using deep sequencing of small RNAs. Virology. 2017;500:130–138. doi:10.1016/j.virol.2016.10.017
6. Zhao G, Wu G, Lim ES, Droit L, Krishnamurthy S, Barouch DH, et al. VirusSeeker, a computational pipeline for virus discovery and virome composition analysis. Virology. 2017;503:21–30. doi:10.1016/j.virol.2016.12.018
7. [BioAider — pending verification] Jin Z, et al. BioAider: an efficient tool for viral genome analysis. [journal/volume pending]
8. Shen W, Le S, Li Y, Hu F. SeqKit: a cross-platform and ultrafast toolkit for FASTA/Q file processing. PLoS One. 2016;11(10):e0163962. doi:10.1371/journal.pone.0163962
9. Chen S, Zhou Y, Chen Y, Gu J. fastp: an ultra-fast all-in-one FASTQ preprocessor. Bioinformatics. 2018;34(17):i884–i890. doi:10.1093/bioinformatics/bty560
10. Patro R, Duggal G, Love MI, Irizarry RA, Kingsford C. Salmon provides fast and bias-aware quantification of transcript expression. Nat Methods. 2017;14(4):417–419. doi:10.1038/nmeth.4197
11. Bankevich A, Nurk S, Antipov D, Gurevich AA, Dvorkin M, Kulikov AS, et al. SPAdes: a new genome assembly algorithm and their applications to single-cell sequencing. J Comput Biol. 2012;19(5):455–477. doi:10.1089/cmb.2012.0021
12. Camacho C, Coulouris G, Avagyan V, Ma N, Papadopoulos J, Bealer K, Madden TL. BLAST+: architecture and applications. BMC Bioinformatics. 2009;10:421. doi:10.1186/1471-2105-10-421
13. Buchfink B, Xie C, Huson DH. Fast and sensitive protein alignment using DIAMOND. Nat Methods. 2015;12(1):59–60. doi:10.1038/nmeth.3176
14. Steinegger M, Söding J. MMseqs2 enables sensitive protein sequence searching for the analysis of massive data sets. Nat Biotechnol. 2017;35(11):1026–1028. doi:10.1038/nbt.3702
15. Lu S, Wang J, Chitsaz F, Derbyshire MK, Geer RC, Gonzales NR, et al. CDD/SPARCLE: the conserved domain database in 2020. Nucleic Acids Res. 2020;48(D1):D265–D268. doi:10.1093/nar/gkz991
16. O'Leary NA, Wright MW, Brister JR, Ciufo S, Haddad D, McVeigh R, et al. Reference sequence (RefSeq) database at NCBI: current status, taxonomic expansion, and functional annotation. Nucleic Acids Res. 2016;44(D1):D733–D745. doi:10.1093/nar/gkv1189
17. Li H. Minimap2: pairwise alignment for nucleotide sequences. Bioinformatics. 2018;34(18):3094–3100. doi:10.1093/bioinformatics/bty191
18. Li H, Handsaker B, Wysoker A, Fennell T, Ruan J, Homer N, et al. The Sequence Alignment/Map format and SAMtools. Bioinformatics. 2009;25(16):2078–2079. doi:10.1093/bioinformatics/btp352
19. Danecek P, Bonfield JK, Liddle J, Marshall J, Ohan V, Pollard MO, et al. Twelve years of SAMtools and BCFTools. Gigascience. 2021;10(2):giab008. doi:10.1093/gigascience/giab008
20. Cingolani P, Platts A, Wang LL, Coon M, Nguyen T, Wang L, et al. A program for annotating and predicting the effects of single nucleotide polymorphisms, SnpEff: SNPs in the genome of Drosophila melanogaster strain w1118; iso-2; iso-3. Fly (Austin). 2012;6(2):80–92. doi:10.4161/fly.19695
21. [Pyrodigal — pending verification] Larralde M, et al. Pyrodigal: [full citation pending]. Plus: pyrodigal-rv [citation pending].
22. Koressaar T, Remm M. Enhancements and modifications of primer design program Primer3. Bioinformatics. 2007;23(10):1289–1291. doi:10.1093/bioinformatics/btm091
23. Untergasser A, Cutcutache I, Koressaar T, Ye J, Faircloth BC, Remm M, Rozen SG. Primer3—new capabilities and interfaces. Nucleic Acids Res. 2012;40(15):e115. doi:10.1093/nar/gks596
24. Katoh K, Standley DM. MAFFT multiple sequence alignment software version 7: improvements in performance and usability. Mol Biol Evol. 2013;30(4):772–780. doi:10.1093/molbev/mst010
25. Capella-Gutiérrez S, Silla-Martínez JM, Gabaldón T. trimAl: a tool for automated alignment trimming in large-scale phylogenetic analyses. Bioinformatics. 2009;25(15):1972–1973. doi:10.1093/bioinformatics/btp348
26. Price MN, Dehal PS, Arkin AP. FastTree 2—approximately maximum-likelihood trees for large alignments. PLoS One. 2010;5(3):e9490. doi:10.1371/journal.pone.0009490
27. Nguyen LT, Schmidt HA, von Haeseler A, Minh BQ. IQ-TREE: a fast and effective stochastic algorithm for estimating maximum-likelihood phylogenies. Mol Biol Evol. 2015;32(1):268–274. doi:10.1093/molbev/msu300
28. Kalyaanamoorthy S, Minh BQ, Wong TKF, von Haeseler A, Jermiin LS. ModelFinder: fast model selection for accurate phylogenetic estimates. Nat Methods. 2017;14(6):587–589. doi:10.1038/nmeth.4285
29. Hoang DT, Chernomor O, von Haeseler A, Minh BQ, Vinh LS. UFBoot2: improving the ultrafast bootstrap approximation. Mol Biol Evol. 2018;35(2):518–522. doi:10.1093/molbev/msx281
30. Muhire BM, Varsani A, Martin DP. SDT: a virus classification tool based on pairwise sequence alignment and identity calculation. PLoS One. 2014;9(10):e108277. doi:10.1371/journal.pone.0108277
31. Martin DP, Murrell B, Golden M, Khoosal A, Muhire B. RDP4: detection and analysis of recombination patterns in virus genomes. Virus Evol. 2015;1(1):vev003. doi:10.1093/ve/vev003 [RDP5-specific citation pending verification]
32. Fitch WM. Toward defining the course of evolution: minimum change for a specific tree topology. Syst Zool. 1971;20(4):406–416. doi:10.2307/2412110
33. Rambaut A, Lam TT, Max Carvalho L, Pybus OG. Exploring the temporal structure of heterochronous sequences using TempEst (formerly Path-O-Gen). Virus Evol. 2016;2(1):vew007. doi:10.1093/ve/vew007
34. Sagulenko P, Puller V, Neher RA. TreeTime: maximum-likelihood phylodynamic analysis. Virus Evol. 2018;4(1):vex042. doi:10.1093/ve/vex042
35. [SNPGenie — pending verification] Nelson CW, et al. SNPGenie: [full citation pending]
36. Leinonen R, Sugawara H, Shumway M; International Nucleotide Sequence Database Collaboration. The Sequence Read Archive. Nucleic Acids Res. 2011;39(Database):D19–D21. doi:10.1093/nar/gkq1019
37. [NGDC GSA — pending verification] Database resources of the National Genomics Data Center [full citation pending]
38. [Genome Warehouse — pending verification] [full citation pending]
39. Lefkowitz EJ, Dempsey DM, Hendrickson RC, Orton RJ, Siddell SG, Smith DB. Virus taxonomy: the database of the International Committee on Taxonomy of Viruses (ICTV). Nucleic Acids Res. 2018;46(D1):D708–D717. doi:10.1093/nar/gkx932
40. Hatcher EL, Zhdanov SA, Bao Y, Blinkova O, Nawrocki EP, Ostapchuck Y, et al. Virus Variation Resource — improved response to emergent viral outbreaks. Nucleic Acids Res. 2017;45(D1):D592–D596. doi:10.1093/nar/gkw1065

---

*Manuscript draft v1 — generated from verified codebase facts; entries marked "pending verification" are being checked against public bibliographic databases and will be finalized before submission.*
