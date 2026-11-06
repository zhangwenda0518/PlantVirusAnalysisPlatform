# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# vendored from suvtk 0.2.0 -- https://github.com/LanderDC/suvtk
# upstream file: suvtk/comments.py (334 lines at commit d8ef68f)
# extracted: 2026-11-02 by 28.ncbi_submission (ncbisub)
# license: GNU General Public License v3 (see vendor_suvtk/LICENSE)
#
# This file is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It is distributed WITHOUT ANY WARRANTY; without even
# the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License (LICENSE) for more details.
#
# Modifications are marked with "# [ncbisub-vendor]" and listed in
# vendor_suvtk/PATCHES.md. This copy is NOT the upstream distribution.
# ---------------------------------------------------------------------------
"""
comments.py
===========

This script generates structured comment files based on MIUVIG standards.
It validates input files, merges data from multiple sources, and ensures compliance
with predefined standards for submission to GenBank.

Functions
---------
comments(taxonomy, features, miuvig, assembly, checkv, output)
    Generate a structured comment file based on MIUVIG standards.
"""

import os

import pandas as pd

# [ncbisub-vendor] upstream: from suvtk import utils
if __package__:  # imported as vendor_suvtk.comments (python -m / engine)
    from . import utils
else:  # pragma: no cover - direct script debugging: python vendor_suvtk/comments.py
    # [vp-vendor] 2026-11-03：改 importlib 动态导入——绝对 import 语句会被
    # 平台依赖审计（dev_tools/audit_deps）当成第三方缺失（vendor_suvtk 是
    # 仓库内子包、不进 requirements.txt），本平台也不支持裸脚本直调。
    import importlib as _il
    utils = _il.import_module('vendor_suvtk.utils')


# [ncbisub-vendor] upstream used click.ClickException for CLI error reporting;
# the vendored core raises a plain exception so it can be called in-process.
class CommentsError(ValueError):
    """Invalid MIUVIG input (replaces click.ClickException)."""


# Allowed value lists for various parameters
uvig_source_allowed = [
    "metagenome (not viral targeted)",
    "viral fraction metagenome (virome)",
    "sequence-targeted metagenome",
    "metatranscriptome (not viral targeted)",
    "viral fraction RNA metagenome (RNA virome)",
    "sequence-targeted RNA metagenome",
    "microbial single amplified genome (SAG)",
    "viral single amplified genome (vSAG)",
    "isolate microbial genome",
    "other",
]

pred_genome_type_allowed = [
    "DNA",
    "dsDNA",
    "ssDNA",
    "RNA",
    "dsRNA",
    "ssRNA",
    "ssRNA(+)",
    "ssRNA(-)",
    "mixed",
    "uncharacterized",
]

pred_genome_struc_allowed = [
    "segmented",
    "non-segmented",
    "undetermined",
]

detec_type_allowed = [
    "independent sequence (UViG)",
    "provirus (UpViG)",
]

assembly_qual_allowed = [
    "Finished genome",
    "High-quality draft genome",
    "Genome fragment(s)",
]

virus_enrich_appr_allowed = [
    "filtration",
    "ultrafiltration",
    "centrifugation",
    "ultracentrifugation",
    "PEG Precipitation",
    "FeCl Precipitation",
    "CsCl density gradient",
    "DNAse",
    "RNAse",
    "targeted sequence capture",
    "other",
    "none",
]

wga_amp_appr_allowed = [
    "pcr based",
    "mda based",
    "none",
]

# For parameters that come from the merged files (features and miuvig)
# we expect them to be one of the following allowed values (if present).
allowed_values = {
    "source_uvig": uvig_source_allowed,
    "detec_type": detec_type_allowed,
    "assembly_qual": assembly_qual_allowed,
    "virus_enrich_appr": virus_enrich_appr_allowed,
    "wga_amp_appr": wga_amp_appr_allowed,
    # Note: pred_genome_type and pred_genome_struc come from the taxonomy file
    # and are checked separately.
}


# [ncbisub-vendor] upstream was a @click.command with click.option decorators;
# it is now a plain function. CLI parsing lives in _build_parser/_cli below.
def comments(taxonomy, features, miuvig, assembly, checkv, output):
    """
    Generate a structured comment file based on MIUVIG standards.

    Parameters
    ----------
    taxonomy, features, miuvig, assembly : str
        Input TSV paths (same contract as the upstream CLI options).
    checkv : str or None
        Optional CheckV quality_summary.tsv path.
    output : str
        Output prefix; the file written is ``output + ".cmt"``.

    Returns
    -------
    str
        Path of the written ``.cmt`` file (vendored addition).
    """
    # Read the taxonomy file.
    taxonomy_df = pd.read_csv(taxonomy, sep="\t")

    # Early check of taxonomy file columns (these come from taxonomy file itself)
    # Check pred_genome_type
    if "pred_genome_type" in taxonomy_df.columns:
        invalid = taxonomy_df[
            ~taxonomy_df["pred_genome_type"].isin(pred_genome_type_allowed)
        ]
        if not invalid.empty:
            invalid_vals = ", ".join(map(str, invalid["pred_genome_type"].unique()))
            raise CommentsError(
                f"Invalid value(s) in column 'pred_genome_type': {invalid_vals}. Allowed values: {', '.join(pred_genome_type_allowed)}."
            )
    # Check pred_genome_struc
    if "pred_genome_struc" in taxonomy_df.columns:
        invalid = taxonomy_df[
            ~taxonomy_df["pred_genome_struc"].isin(pred_genome_struc_allowed)
        ]
        if not invalid.empty:
            invalid_vals = ", ".join(map(str, invalid["pred_genome_struc"].unique()))
            raise CommentsError(
                f"Invalid value(s) in column 'pred_genome_struc': {invalid_vals}. Allowed values: {', '.join(pred_genome_struc_allowed)}."
            )

    # Read the features and miuvig files (key/value format) into dictionaries.
    features_dict = (
        pd.read_csv(features, sep="\t")
        .set_index("MIUVIG_parameter")["value"]
        .to_dict()
    )
    miuvig_dict = (
        pd.read_csv(miuvig, sep="\t")
        .set_index("MIUVIG_parameter")["value"]
        .to_dict()
    )

    assembly_dict = (
        pd.read_csv(assembly, sep="\t")
        .set_index("Assembly_parameter")["value"]
        .to_dict()
    )

    # Merge the dictionaries (miuvig values take precedence).
    structured_comment_dict = {
        "StructuredCommentPrefixM": "MIUVIG:5.0-Data",  # Suffix M to not have duplicate keys in merged dictionary
        # "StructuredCommentSuffix": "MIUVIG:5.0-Data",
    }

    merged_params = {
        **structured_comment_dict,
        **features_dict,
        **miuvig_dict,
        **assembly_dict,
    }

    # Early check on merged parameters:
    # Check formatting for software columns.
    for key in ["assembly_software", "vir_ident_software"]:
        if key in merged_params:
            value = merged_params[key]
            if not isinstance(value, str):
                raise CommentsError(
                    f"Value for {key} must be a string in the format 'software;version;parameters'. Got: {value}"
                )
            if value.count(";") != 2:
                raise CommentsError(
                    f"Invalid format for {key}: {value}. Expected format: 'software;version;parameters'."
                )

    # For any merged parameter that should have a restricted set of values,
    # check its value against the allowed list.
    for key, allowed in allowed_values.items():
        if key in merged_params:
            value = merged_params[key]
            if key == "virus_enrich_appr":
                vea = list(map(str.strip, value.split("+")))
                invalid_values = [
                    v for v in vea if v not in allowed
                ]  # Collect invalid values
                if invalid_values:  # If there's any invalid value, raise an error
                    raise CommentsError(
                        f"Invalid value(s) for {key}: {', '.join(invalid_values)}.\n"
                        f"Allowed values: {', '.join(allowed)}.\n"
                        f"Allowed values should be separated by a '+'."
                    )

                # Update merged_params with cleaned value
                merged_params[key] = " + ".join(vea)
            else:
                if value not in allowed:
                    raise CommentsError(
                        f"Invalid value for {key}: {value}.\nAllowed values: {', '.join(allowed)}."
                    )

    # Add merged parameters as new constant columns to the taxonomy DataFrame.
    if checkv:
        checkv_df = pd.read_csv(checkv, sep="\t")
        checkv_df = checkv_df[
            ["contig_id", "miuvig_quality", "completeness", "provirus"]
        ]
        checkv_df.rename(
            columns={
                "contig_id": "contig",
                "miuvig_quality": "assembly_qual",
                "completeness": "compl_score",
            },
            inplace=True,
        )
        checkv_df["detec_type"] = checkv_df["provirus"].map(
            lambda x: (
                "provirus (UpViG)" if x == "Yes" else "independent sequence (UViG)"
            )
        )
        checkv_df = checkv_df[["contig", "assembly_qual", "compl_score", "detec_type"]]
        taxonomy_df = pd.merge(taxonomy_df, checkv_df, on="contig", how="left")
        merged_params["compl_software"] = "CheckV"
        for param, val in merged_params.items():
            taxonomy_df[param] = val
    else:
        for param, val in merged_params.items():
            taxonomy_df[param] = val

    # If missing values in 'assembly_qual' and 'detec_type', fill with default values.
    # Ensure the columns exist before filling missing values
    default_values = {
        "assembly_qual": "Genome fragment(s)",
        "detec_type": "independent sequence (UViG)",
    }

    for col, default in default_values.items():
        if col not in taxonomy_df:
            taxonomy_df[col] = default
        else:
            taxonomy_df[col] = taxonomy_df[col].fillna(default)

    # Reorder columns.
    desired_order = [
        "contig",
        "StructuredCommentPrefixM",
        "source_uvig",
        "assembly_software",
        "vir_ident_software",
        "pred_genome_type",
        "pred_genome_struc",
        "detec_type",
        "assembly_qual",
        "number_contig",
        "compl_score",
        "compl_software",
        "feat_pred",
        "ref_db",
        "sim_search_meth",
        "size_frac",
        "virus_enrich_appr",
        "nucl_acid_ext",
        "wga_amp_appr",
        # "StructuredCommentSuffix",
    ]
    desired_existing = [col for col in desired_order if col in taxonomy_df.columns]
    remaining_cols = [col for col in taxonomy_df.columns if col not in desired_existing]
    taxonomy_df = taxonomy_df[desired_existing + remaining_cols]
    taxonomy_df.rename(
        columns={
            "contig": "Sequence_ID",
            "StructuredCommentPrefixM": "StructuredCommentPrefix",
        },
        inplace=True,
    )

    # Ensure the output directory exists, but only if a directory is specified
    output_dir = os.path.dirname(output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Write the combined DataFrame to a TSV file.
    taxonomy_df.to_csv(output + ".cmt", sep="\t", index=False, encoding="utf-8")
    # [ncbisub-vendor] click.echo -> print
    print(f"Combined file written to {output}.cmt")
    # [ncbisub-vendor] return the written path for in-process callers.
    return output + ".cmt"


# [ncbisub-vendor] upstream had no standalone entry point beyond click; this
# argparse block replaces the click command for direct debugging:
#   python -m vendor_suvtk.comments -t ... -f ... -m ... -a ... -o prefix
def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate structured comment file based on MIUVIG standards.")
    parser.add_argument("-t", "--taxonomy", required=True,
                        help="MIUVIG TSV file generated by taxonomy/segment-info.")
    parser.add_argument("-f", "--features", required=True,
                        help="MIUVIG TSV file generated by the features subcommand.")
    parser.add_argument("-m", "--miuvig", required=True,
                        help="TSV file with MIUVIG information.")
    parser.add_argument("-a", "--assembly", required=True,
                        help="TSV file with Genbank Assembly information.")
    parser.add_argument("-c", "--checkv", required=False, default=None,
                        help="CheckV's quality_summary.tsv file.")
    parser.add_argument("-o", "--output", required=True,
                        help="Output filename prefix.")
    return parser


def _cli(argv=None):
    args = _build_parser().parse_args(argv)
    for label, path in (("taxonomy", args.taxonomy),
                        ("features", args.features),
                        ("miuvig", args.miuvig),
                        ("assembly", args.assembly),
                        ("checkv", args.checkv)):
        if path and not os.path.isfile(path):
            raise SystemExit("%s file not found: %s" % (label, path))
    return comments(args.taxonomy, args.features, args.miuvig,
                    args.assembly, args.checkv, args.output)


if __name__ == "__main__":
    _cli()
