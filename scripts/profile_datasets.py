"""
Read-only dataset profiler for the Continuous KYC Autonomous Auditor project.

Discovers CSV/JSON files under data/kyc_profiles, data/aml_transactions, data/sanctions,
data/articles, and data/ubo, and prints shape, columns, null counts, duplicate/key checks,
date ranges, and numeric summaries. Never writes to, moves, or deletes any file under data/.

Large files (SAML-D.csv, opensanctions_targets.csv) are profiled via chunked scans so the
whole file is measured without loading it into memory at once.

Usage:
    python scripts/profile_datasets.py
    python scripts/profile_datasets.py --data-dir path/to/data
    python scripts/profile_datasets.py --full   # also chunk-scan large files (slower)
"""

import argparse
import os
import sys

import pandas as pd

LARGE_FILE_BYTES = 20 * 1024 * 1024  # files above this size are profiled in chunks
CHUNK_ROWS = 1_000_000

CORE_CSVS = [
    "kyc_profiles/clients_with_fatf_ofac.csv",
    "kyc_profiles/client_account_mapping.csv",
    "kyc_profiles/transactions_with_fatf_ofac.csv",
    "aml_transactions/SAML-D.csv",
    "sanctions/ofac_sdn.csv",
    "sanctions/ofac_alt.csv",
    "sanctions/ofac_add.csv",
    "sanctions/opensanctions_targets.csv",
    "sanctions/sample_ofac_sdn.csv",
    "sanctions/sample_ofac_alt.csv",
    "sanctions/sample_opensanctions.csv",
]

# Files with no header row in the source data; column names recovered from the matching
# sample_* fixture (see docs/data-dictionary.md). Position-indexed.
NO_HEADER_COLUMNS = {
    "sanctions/ofac_sdn.csv": [
        "ent_num", "SDN_Name", "SDN_Type", "Program", "Title", "Call_Sign",
        "Vess_type", "Tonnage", "GRT", "Vess_flag", "Vess_owner", "Remarks",
    ],
    "sanctions/ofac_alt.csv": ["ent_num", "alt_num", "alt_type", "alt_name", "alt_remarks"],
    "sanctions/ofac_add.csv": [
        "ent_num", "add_num", "address", "city_state_zip", "country", "add_remarks",
    ],
}

OUT_OF_SCOPE_DIRS = ["opp115", "privacy_qa", "gdpr_text"]
OUT_OF_SCOPE_FILES = ["gdpr.json", "gdpr_articles.csv", "gcapi.dll"]


def human_size(num_bytes):
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def looks_like_date(series_name):
    lowered = series_name.lower()
    return any(token in lowered for token in ("date", "time", "timestamp", "seen", "change"))


def profile_small_file(path, rel_path):
    header = "infer"
    names = NO_HEADER_COLUMNS.get(rel_path)
    if names is not None:
        header = None

    # on_bad_lines='skip': some fixture files (e.g. sample_ofac_sdn.csv) carry a trailing
    # malformed sentinel row; skip rather than fail, and note it below. Read-only — the
    # source file itself is never modified.
    read_kwargs = dict(header=header, names=names, low_memory=False, on_bad_lines="skip")
    df = pd.read_csv(path, dtype=str, **read_kwargs)
    # Re-read numeric-looking columns with inference for describe(); keep it simple and safe.
    df_typed = pd.read_csv(path, **read_kwargs)

    print(f"  shape: {df.shape[0]:,} rows x {df.shape[1]} cols")
    print(f"  columns: {list(df.columns)}")

    nulls = df_typed.isnull().sum()
    nonzero_nulls = nulls[nulls > 0]
    if len(nonzero_nulls):
        print(f"  nulls (nonzero only): {dict(nonzero_nulls)}")
    else:
        print("  nulls: none")

    dup_rows = df_typed.duplicated().sum()
    print(f"  fully duplicated rows: {dup_rows}")

    first_col = df_typed.columns[0]
    if df_typed[first_col].is_unique:
        print(f"  candidate primary key: '{first_col}' (unique across {len(df_typed)} rows)")

    for col in df_typed.columns:
        if looks_like_date(col):
            try:
                parsed = pd.to_datetime(df_typed[col], errors="coerce")
                if parsed.notna().any():
                    print(f"  date range [{col}]: {parsed.min()} -> {parsed.max()}")
            except Exception:
                pass

    numeric_cols = df_typed.select_dtypes(include="number").columns
    for col in numeric_cols:
        desc = df_typed[col].describe()
        print(
            f"  numeric [{col}]: min={desc['min']:.2f} mean={desc['mean']:.2f} "
            f"max={desc['max']:.2f}"
        )

    for col in df_typed.columns:
        if df_typed[col].dtype == object and df_typed[col].nunique() <= 15:
            print(f"  categorical [{col}]: {dict(df_typed[col].value_counts().head(15))}")


def profile_large_file(path, rel_path):
    size = os.path.getsize(path)
    print(f"  size {human_size(size)} exceeds threshold -> chunked full scan")

    header = "infer"
    names = NO_HEADER_COLUMNS.get(rel_path)
    if names is not None:
        header = None

    rows = 0
    nulls = None
    columns = None
    numeric_summary = {}

    for chunk in pd.read_csv(
        path, header=header, names=names, chunksize=CHUNK_ROWS,
        low_memory=False, on_bad_lines="skip",
    ):
        rows += len(chunk)
        if columns is None:
            columns = list(chunk.columns)
        n = chunk.isnull().sum()
        nulls = n if nulls is None else nulls + n

        for col in chunk.select_dtypes(include="number").columns:
            lo, hi = chunk[col].min(), chunk[col].max()
            if col not in numeric_summary:
                numeric_summary[col] = [lo, hi]
            else:
                numeric_summary[col][0] = min(numeric_summary[col][0], lo)
                numeric_summary[col][1] = max(numeric_summary[col][1], hi)

    print(f"  rows scanned: {rows:,}")
    print(f"  columns: {columns}")
    nonzero_nulls = nulls[nulls > 0] if nulls is not None else {}
    if len(nonzero_nulls):
        print(f"  nulls (nonzero only): {dict(nonzero_nulls)}")
    else:
        print("  nulls: none")
    for col, (lo, hi) in numeric_summary.items():
        print(f"  numeric [{col}]: min={lo} max={hi}")


def profile_articles(data_dir):
    articles_dir = os.path.join(data_dir, "articles")
    if not os.path.isdir(articles_dir):
        return
    print("\n=== data/articles/ (text fixtures) ===")
    for fname in sorted(os.listdir(articles_dir)):
        fpath = os.path.join(articles_dir, fname)
        if not fname.endswith(".txt"):
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            text = f.read()
        print(f"  {fname}: {len(text)} bytes, {len(text.split())} words")


def profile_ubo(data_dir):
    import json

    ubo_dir = os.path.join(data_dir, "ubo")
    if not os.path.isdir(ubo_dir):
        return
    print("\n=== data/ubo/ (ownership graph fixtures) ===")
    for fname in sorted(os.listdir(ubo_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(ubo_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            graph = json.load(f)
        n_entities = len(graph.get("entities", []))
        n_edges = len(graph.get("ownership_edges", []))
        print(f"  {fname}: {n_entities} entities, {n_edges} ownership edges")


def note_out_of_scope(data_dir):
    print("\n=== Out-of-scope files/dirs (listed, not profiled) ===")
    for d in OUT_OF_SCOPE_DIRS:
        full = os.path.join(data_dir, d)
        if os.path.isdir(full):
            print(f"  {d}/  (present, unrelated privacy/GDPR corpus)")
    for f in OUT_OF_SCOPE_FILES:
        full = os.path.join(data_dir, f)
        if os.path.isfile(full):
            print(f"  {f}  (present, unrelated)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data", help="Path to the data directory")
    parser.add_argument(
        "--full", action="store_true",
        help="Chunk-scan large files fully (SAML-D.csv, opensanctions_targets.csv). Slower.",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.data_dir):
        print(f"Data directory not found: {args.data_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Profiling dataset under: {os.path.abspath(args.data_dir)}\n")

    for rel_path in CORE_CSVS:
        full_path = os.path.join(args.data_dir, rel_path)
        print(f"=== {rel_path} ===")
        if not os.path.isfile(full_path):
            print("  NOT FOUND")
            continue
        size = os.path.getsize(full_path)
        print(f"  size: {human_size(size)}")
        if size > LARGE_FILE_BYTES:
            if args.full:
                profile_large_file(full_path, rel_path)
            else:
                print("  large file -- pass --full to chunk-scan it completely")
        else:
            profile_small_file(full_path, rel_path)
        print()

    profile_articles(args.data_dir)
    profile_ubo(args.data_dir)
    note_out_of_scope(args.data_dir)


if __name__ == "__main__":
    main()
