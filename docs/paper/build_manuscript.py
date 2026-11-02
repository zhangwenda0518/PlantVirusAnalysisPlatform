# -*- coding: utf-8 -*-
"""Resolve [@key] citations in manuscript_body.md into numbered references
(order of first appearance) and emit manuscript_revised_v2.md.

All reference strings verified against public bibliographic databases (PubMed/
DOI/publisher pages) on 2026-09-15; corrections applied per verification report.
"""
import re, os

HERE = os.path.dirname(os.path.abspath(__file__))

REFS = {
    "edgar2022": "Edgar RC, Taylor B, Lin V, Altman T, Barbera P, Meleshko D, Lohr D, Novakovsky G, Buchfink B, Al-Shayeb B, Banfield JF, de la Pena M, Korobeynikov A, Chikhi R, Babaian A. Petabase-scale sequence alignment catalyses viral discovery. Nature. 2022;602(7895):142-147. doi:10.1038/s41586-021-04332-2",
    "langmead2012": "Langmead B, Salzberg SL. Fast gapped-read alignment with Bowtie 2. Nat Methods. 2012;9(4):357-359. doi:10.1038/nmeth.1923",
    "kim2019": "Kim D, Paggi JM, Park C, Bennett C, Salzberg SL. Graph-based genome alignment and genotyping with HISAT2 and HISAT-genotype. Nat Biotechnol. 2019;37(8):907-915. doi:10.1038/s41587-019-0201-4",
    "anderson2004": "Anderson PK, Cunningham AA, Patel NG, Morales FJ, Epstein PR, Daszak P. Emerging infectious diseases of plants: pathogen pollution, climate change and agrotechnology drivers. Trends Ecol Evol. 2004;19(10):535-544. doi:10.1016/j.tree.2004.07.021",
    "jones2019": "Jones RAC, Naidu RA. Global dimensions of plant virus diseases: current status and future perspectives. Annu Rev Virol. 2019;6(1):387-409. doi:10.1146/annurev-virology-092818-015606",
    "hadidi2016": "Hadidi A, Flores R, Candresse T, Barba M. Next-generation sequencing and genome editing in plant virology. Front Microbiol. 2016;7:1325. doi:10.3389/fmicb.2016.01325",
    "massart2017": "Massart S, Candresse T, Gil J, Lacomme C, Predajna L, Ravnikar M, et al. A framework for the evaluation of biosecurity, commercial, regulatory, and scientific impacts of plant viruses and viroids identified by NGS technologies. Front Microbiol. 2017;8:45. doi:10.3389/fmicb.2017.00045",
    "villamor2019": "Villamor DEV, Ho T, Al Rwahnih M, Martin RR, Tzanetakis IE. High throughput sequencing for plant virus detection and discovery. Phytopathology. 2019;109(5):716-725. doi:10.1094/PHYTO-07-18-0257-RVW",
    "maliogka2018": "Maliogka VI, Minafra A, Saldarelli P, Ruiz-Garcia AB, Glasa M, Katis N, Olmos A. Recent advances on detection and characterization of fruit tree viruses using high-throughput sequencing technologies. Viruses. 2018;10(8):436. doi:10.3390/v10080436",
    "kanapiya2024": "Kanapiya A, Amanbayeva U, Tulegenova Z, et al. Recent advances and challenges in plant viral diagnostics. Front Plant Sci. 2024;15:1451790. doi:10.3389/fpls.2024.1451790",
    "zheng2017": "Zheng Y, Gao S, Padmanabhan C, et al. VirusDetect: an automated pipeline for efficient virus discovery using deep sequencing of small RNAs. Virology. 2017;500:130-138. doi:10.1016/j.virol.2016.10.017",
    "zhao2017": "Zhao G, Wu G, Lim ES, Droit L, Krishnamurthy S, Barouch DH, Virgin HW, Wang D. VirusSeeker, a computational pipeline for virus discovery and virome composition analysis. Virology. 2017;503:21-30. doi:10.1016/j.virol.2017.01.005",
    "ru2023": "Ru J, Khan Mirzaei M, Xue J, Peng X, Deng L. ViroProfiler: a containerized bioinformatics pipeline for viral metagenomic data analysis. Gut Microbes. 2023;15(1):2192522. doi:10.1080/19490976.2023.2192522",
    "zhou2020": "Zhou ZJ, Qiu Y, Pu Y, Huang X, Ge XY. BioAider: an efficient tool for viral genome analysis and its application in tracing SARS-CoV-2 transmission. Sustain Cities Soc. 2020;63:102466. doi:10.1016/j.scs.2020.102466",
    "wood2019": "Wood DE, Lu J, Langmead B. Improved metagenomic analysis with Kraken 2. Genome Biol. 2019;20(1):257. doi:10.1186/s13059-019-1891-0",
    "menzel2016": "Menzel P, Ng KL, Krogh A. Fast and sensitive taxonomic classification for metagenomics with Kaiju. Nat Commun. 2016;7:11257. doi:10.1038/ncomms11257",
    "guo2021": "Guo J, Bolduc B, Zayed AA, et al. VirSorter2: a multi-classifier, expert-guided approach to detect diverse DNA and RNA viruses in metagenomic samples. Microbiome. 2021;9(1):37. doi:10.1186/s40168-020-00990-y",
    "camargo2024": "Camargo AP, Roux S, Schulz F, et al. Identification of mobile genetic elements with geNomad. Nat Biotechnol. 2024;42(8):1303-1312. doi:10.1038/s41587-023-01953-y",
    "shen2016": "Shen W, Le S, Li Y, Hu F. SeqKit: a cross-platform and ultrafast toolkit for FASTA/Q file processing. PLoS One. 2016;11(10):e0163962. doi:10.1371/journal.pone.0163962",
    "chen2018": "Chen S, Zhou Y, Chen Y, Gu J. fastp: an ultra-fast all-in-one FASTQ preprocessor. Bioinformatics. 2018;34(17):i884-i890. doi:10.1093/bioinformatics/bty560",
    "patro2017": "Patro R, Duggal G, Love MI, Irizarry RA, Kingsford C. Salmon provides fast and bias-aware quantification of transcript expression. Nat Methods. 2017;14(4):417-419. doi:10.1038/nmeth.4197",
    "lefkowitz2018": "Lefkowitz EJ, Dempsey DM, Hendrickson RC, Orton RJ, Siddell SG, Smith DB. Virus taxonomy: the database of the International Committee on Taxonomy of Viruses (ICTV). Nucleic Acids Res. 2018;46(D1):D708-D717. doi:10.1093/nar/gkx932",
    "hatcher2017": "Hatcher EL, Zhdanov SA, Bao Y, et al. Virus Variation Resource - improved response to emergent viral outbreaks. Nucleic Acids Res. 2017;45(D1):D482-D490. doi:10.1093/nar/gkw1065",
    "bankevich2012": "Bankevich A, Nurk S, Antipov D, et al. SPAdes: a new genome assembly algorithm and its applications to single-cell sequencing. J Comput Biol. 2012;19(5):455-477. doi:10.1089/cmb.2012.0021",
    "buchfink2015": "Buchfink B, Xie C, Huson DH. Fast and sensitive protein alignment using DIAMOND. Nat Methods. 2015;12(1):59-60. doi:10.1038/nmeth.3176",
    "oleary2016": "O'Leary NA, Wright MW, Brister JR, et al. Reference sequence (RefSeq) database at NCBI: current status, taxonomic expansion, and functional annotation. Nucleic Acids Res. 2016;44(D1):D733-D745. doi:10.1093/nar/gkv1189",
    "steinegger2017": "Steinegger M, Soding J. MMseqs2 enables sensitive protein sequence searching for the analysis of massive data sets. Nat Biotechnol. 2017;35(11):1026-1028. doi:10.1038/nbt.3988",
    "lu2020": "Lu S, Wang J, Chitsaz F, et al. CDD/SPARCLE: the conserved domain database in 2020. Nucleic Acids Res. 2020;48(D1):D265-D268. doi:10.1093/nar/gkz991",
    "camacho2009": "Camacho C, Coulouris G, Avagyan V, et al. BLAST+: architecture and applications. BMC Bioinformatics. 2009;10:421. doi:10.1186/1471-2105-10-421",
    "larralde2022": "Larralde M. Pyrodigal: Python bindings and interface to Prodigal, an efficient method for gene prediction in prokaryotes. J Open Source Softw. 2022;7(72):4296. doi:10.21105/joss.04296",
    "deconinck2022": "De Coninck L. pyrodigal-rv: RNA-virus-aware gene prediction based on Pyrodigal [Computer software, v0.1.0]. GitHub. https://github.com/LanderDC/pyrodigal-rv",
    "koressaar2007": "Koressaar T, Remm M. Enhancements and modifications of primer design program Primer3. Bioinformatics. 2007;23(10):1289-1291. doi:10.1093/bioinformatics/btm091",
    "untergasser2012": "Untergasser A, Cutcutache I, Koressaar T, Ye J, Faircloth BC, Remm M, Rozen SG. Primer3 - new capabilities and interfaces. Nucleic Acids Res. 2012;40(15):e115. doi:10.1093/nar/gks596",
    "katoh2013": "Katoh K, Standley DM. MAFFT multiple sequence alignment software version 7: improvements in performance and usability. Mol Biol Evol. 2013;30(4):772-780. doi:10.1093/molbev/mst010",
    "capella2009": "Capella-Gutierrez S, Silla-Martinez JM, Gabaldon T. trimAl: a tool for automated alignment trimming in large-scale phylogenetic analyses. Bioinformatics. 2009;25(15):1972-1973. doi:10.1093/bioinformatics/btp348",
    "price2010": "Price MN, Dehal PS, Arkin AP. FastTree 2 - approximately maximum-likelihood trees for large alignments. PLoS One. 2010;5(3):e9490. doi:10.1371/journal.pone.0009490",
    "nguyen2015": "Nguyen LT, Schmidt HA, von Haeseler A, Minh BQ. IQ-TREE: a fast and effective stochastic algorithm for estimating maximum-likelihood phylogenies. Mol Biol Evol. 2015;32(1):268-274. doi:10.1093/molbev/msu300",
    "minh2020": "Minh BQ, Schmidt HA, Chernomor O, et al. IQ-TREE 2: new models and efficient methods for phylogenetic inference in the genomic era. Mol Biol Evol. 2020;37(5):1530-1534. doi:10.1093/molbev/msaa015",
    "kalyaanamoorthy2017": "Kalyaanamoorthy S, Minh BQ, Wong TKF, von Haeseler A, Jermiin LS. ModelFinder: fast model selection for accurate phylogenetic estimates. Nat Methods. 2017;14(6):587-589. doi:10.1038/nmeth.4285",
    "hoang2018": "Hoang DT, Chernomor O, von Haeseler A, Minh BQ, Vinh LS. UFBoot2: improving the ultrafast bootstrap approximation. Mol Biol Evol. 2018;35(2):518-522. doi:10.1093/molbev/msx281",
    "guindon2010": "Guindon S, Dufayard JF, Lefort V, Anisimova M, Hordijk W, Gascuel O. New algorithms and methods to estimate maximum-likelihood phylogenies: assessing the performance of PhyML 3.0. Syst Biol. 2010;59(3):307-321. doi:10.1093/sysbio/syq010",
    "muhire2014": "Muhire BM, Varsani A, Martin DP. SDT: a virus classification tool based on pairwise sequence alignment and identity calculation. PLoS One. 2014;9(9):e108277. doi:10.1371/journal.pone.0108277",
    "martin2015": "Martin DP, Murrell B, Golden M, Khoosal A, Muhire B. RDP4: detection and analysis of recombination patterns in virus genomes. Virus Evol. 2015;1(1):vev003. doi:10.1093/ve/vev003",
    "martin2021": "Martin DP, Varsani A, Roumagnac P, Botha G, Maslamoney S, Schwab T, Kelz Z, Kumar V, Murrell B. RDP5: a computer program for analyzing recombination in, and removing signals of recombination from, nucleotide sequence datasets. Virus Evol. 2021;7(1):veaa087. doi:10.1093/ve/veaa087",
    "fitch1971": "Fitch WM. Toward defining the course of evolution: minimum change for a specific tree topology. Syst Zool. 1971;20(4):406-416. doi:10.2307/2412116",
    "rambaut2016": "Rambaut A, Lam TT, Carvalho LM, Pybus OG. Exploring the temporal structure of heterochronous sequences using TempEst (formerly Path-O-Gen). Virus Evol. 2016;2(1):vew007. doi:10.1093/ve/vew007",
    "sagulenko2018": "Sagulenko P, Puller V, Neher RA. TreeTime: maximum-likelihood phylodynamic analysis. Virus Evol. 2018;4(1):vex042. doi:10.1093/ve/vex042",
    "leinonen2011": "Leinonen R, Sugawara H, Shumway M; International Nucleotide Sequence Database Collaboration. The Sequence Read Archive. Nucleic Acids Res. 2011;39(Database issue):D19-D21. doi:10.1093/nar/gkq1019",
    "katz2022": "Katz K, Shutov O, Lapoint R, Kimelman M, Brister JR, O'Sullivan C. The Sequence Read Archive: a decade more of explosive growth. Nucleic Acids Res. 2022;50(D1):D387-D390. doi:10.1093/nar/gkab1053",
    "ngdc2026": "CNCB-NGDC Members and Partners. Database resources of the National Genomics Data Center, China National Center for Bioinformation in 2026. Nucleic Acids Res. 2026;54(D1):D28-D47. doi:10.1093/nar/gkaf1172",
    "chen2021": "Chen M, Ma Y, Wu S, et al. Genome Warehouse: a public repository housing genome-scale data. Genomics Proteomics Bioinformatics. 2021;19(4):584-589. doi:10.1016/j.gpb.2021.04.001",
    "europepmc2015": "Europe PMC Consortium. Europe PMC: a full-text literature database for the life sciences and platform for innovation. Nucleic Acids Res. 2015;43(D1):D1042-D1048. doi:10.1093/nar/gku1061",
    "li2018": "Li H. Minimap2: pairwise alignment for nucleotide sequences. Bioinformatics. 2018;34(18):3094-3100. doi:10.1093/bioinformatics/bty191",
    "li2009": "Li H, Handsaker B, Wysoker A, et al. The Sequence Alignment/Map format and SAMtools. Bioinformatics. 2009;25(16):2078-2079. doi:10.1093/bioinformatics/btp352",
    "danecek2021": "Danecek P, Bonfield JK, Liddle J, et al. Twelve years of SAMtools and BCFtools. Gigascience. 2021;10(2):giab008. doi:10.1093/gigascience/giab008",
    "cingolani2012": "Cingolani P, Platts A, Wang LL, et al. A program for annotating and predicting the effects of single nucleotide polymorphisms, SnpEff: SNPs in the genome of Drosophila melanogaster strain w1118; iso-2; iso-3. Fly (Austin). 2012;6(2):80-92. doi:10.4161/fly.19695",
}

import sys
BODY = sys.argv[1] if len(sys.argv) > 1 else 'manuscript_body.md'
OUTNAME = sys.argv[2] if len(sys.argv) > 2 else 'manuscript_revised_v2.md'
body = open(os.path.join(HERE, BODY), encoding='utf-8').read()

order = {}          # key -> number
missing = []

def repl(m):
    keys = [k.strip() for k in m.group(1).split(';')]
    nums = []
    for k in keys:
        k = k.lstrip('@').strip()
        if k not in REFS:
            missing.append(k)
            nums.append('?')
            continue
        if k not in order:
            order[k] = len(order) + 1
        nums.append(str(order[k]))
    return '[' + ','.join(nums) + ']'

resolved = re.sub(r'\[(@[^\]]+)\]', repl, body)

lines = ['', '## References', '']
for k, n in sorted(order.items(), key=lambda kv: kv[1]):
    lines.append(f'{n}. {REFS[k]}')
resolved = resolved.replace('{{REFERENCES}}', '\n'.join(lines))

unused = sorted(set(REFS) - set(order))
out = os.path.join(HERE, OUTNAME)
open(out, 'w', encoding='utf-8').write(resolved)
print('written:', out)
print('references used:', len(order), '| total in db:', len(REFS))
print('missing keys:', sorted(set(missing)) or 'none')
print('uncited refs :', unused or 'none')
words = len(re.findall(r'\b\w+\b', resolved.split('## References')[0]))
print('body word count:', words)
