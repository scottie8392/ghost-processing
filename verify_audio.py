"""
Ghost Processing — verification script

Audits processed output to confirm all source files are accounted for
and that every dest file's MD5 hash matches what was recorded at write time.

Usage:
    python verify_audio.py --config config.local.yaml
    python verify_audio.py --config config.local.yaml --json
"""

import os
import json
import hashlib
import argparse
import yaml


def file_hash(file_path):
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_audio_file(file_path):
    return file_path.lower().endswith((".wav", ".aif", ".aiff"))


def collect_files(directory):
    files = []
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            if filename.startswith("."):
                continue
            file_path = os.path.join(root, filename)
            if is_audio_file(file_path):
                files.append(file_path)
    return files


def load_config(config_path):
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def run_verification(config, stream=False):
    """
    Core verification logic. Returns a result dict.
    If stream=True, prints per-file progress lines as it runs.
    """
    source_dir  = config["source_dir"]
    dest_base   = config["dest_base"]
    source_name = os.path.basename(os.path.normpath(source_dir))
    target_rate = config.get("target_sample_rate", 48000)
    bit_depth   = config.get("bit_depth", 24)
    is_float    = str(bit_depth) == "32f"
    depth_part  = "32f" if is_float else f"{bit_depth}b"
    suffix      = f"{target_rate // 1000}k{depth_part}"
    dest_dir    = os.path.join(dest_base, f"{source_name}_{suffix}")
    progress_log = os.path.join(dest_dir, "progress.json")
    rejects_log  = os.path.join(dest_dir, "rejects.json")

    source_files = set(collect_files(source_dir))

    progress = {}
    if os.path.exists(progress_log):
        with open(progress_log) as f:
            try:
                progress = json.load(f)
            except json.JSONDecodeError:
                if stream:
                    print("WARNING: progress.json is corrupted", flush=True)

    rejects = []
    if os.path.exists(rejects_log):
        with open(rejects_log) as f:
            try:
                rejects = [e["path"] for e in json.load(f) if isinstance(e, dict) and "path" in e]
            except json.JSONDecodeError:
                if stream:
                    print("WARNING: rejects.json is corrupted", flush=True)

    all_progress     = {os.path.join(source_dir, rel): data for rel, data in progress.items()}
    rejected_sources = set(rejects)
    converted_sources = {p: d for p, d in all_progress.items() if d.get("status") in ("converted", "copied")}
    merged_sources    = {p: d for p, d in all_progress.items() if d.get("status") == "merged"}
    unpaired_sources  = {p: d for p, d in all_progress.items() if d.get("status") == "unpaired"}
    processed         = set(all_progress) | rejected_sources
    unprocessed       = source_files - processed

    missing_dest     = []
    hash_mismatches  = []
    passed           = []

    total = len(converted_sources)
    if stream and total > 0:
        print(f"Verifying {total} output file{'s' if total != 1 else ''}...", flush=True)

    for i, (source_path, data) in enumerate(sorted(converted_sources.items()), 1):
        rel      = os.path.relpath(source_path, source_dir)
        base, ext = os.path.splitext(rel)
        dest_ext  = ".wav" if (is_float and ext.lower() in (".aif", ".aiff")) else ext
        dest_path = os.path.join(dest_dir, f"{base}_{suffix}{dest_ext}")

        if not os.path.exists(dest_path):
            missing_dest.append(source_path)
            if stream:
                print(f"Verify: {rel}  ✗ MISSING", flush=True)
            continue

        stored_dest_hash = data.get("dest_hash")
        if stored_dest_hash:
            # New format: compare dest file against its recorded hash
            actual = file_hash(dest_path)
            if actual != stored_dest_hash:
                hash_mismatches.append(source_path)
                if stream:
                    print(f"Verify: {rel}  ✗ HASH MISMATCH (file may be corrupted)", flush=True)
            else:
                passed.append(source_path)
                if stream:
                    print(f"Verify: {rel}  ✓", flush=True)
        else:
            # Legacy format (no dest_hash stored): just confirm dest exists
            passed.append(source_path)
            if stream:
                print(f"Verify: {rel}  ✓ (no hash on record — re-run job to enable full check)", flush=True)

    return {
        "source_files":        len(source_files),
        "converted":           len(converted_sources),
        "merged":              len(merged_sources),
        "unpaired":            len(unpaired_sources),
        "rejected":            len(rejected_sources),
        "unprocessed":         len(unprocessed),
        "missing_dest":        len(missing_dest),
        "hash_mismatches":     len(hash_mismatches),
        "passed":              len(passed),
        "unprocessed_files":   sorted(unprocessed),
        "missing_dest_files":  sorted(missing_dest),
        "hash_mismatch_files": sorted(hash_mismatches),
    }


def main():
    parser = argparse.ArgumentParser(description="Ghost Processing — verification")
    parser.add_argument("--config",  required=True, help="Path to YAML config file")
    parser.add_argument("--json",    action="store_true", help="Output machine-readable JSON summary")
    parser.add_argument("--stream",  action="store_true", help="Stream per-file progress lines (for UI log panel)")
    args = parser.parse_args()

    config = load_config(args.config)
    result = run_verification(config, stream=(args.stream or not args.json))

    if args.json:
        print(json.dumps(result, indent=2), flush=True)
        return

    # Human-readable summary (always printed in non-JSON mode)
    total   = result["converted"]
    passed  = result["passed"]
    issues  = result["missing_dest"] + result["hash_mismatches"]
    all_ok  = result["unprocessed"] == 0 and result["missing_dest"] == 0 and result["hash_mismatches"] == 0

    print("", flush=True)
    if all_ok:
        print(f"Verification passed — {passed}/{total} files OK", flush=True)
    else:
        print(f"Verification found issues — {passed}/{total} files OK", flush=True)

    if result["unprocessed"]:
        print(f"  {result['unprocessed']} unprocessed source files", flush=True)
        for f in result["unprocessed_files"]:
            print(f"    - {f}", flush=True)
    if result["missing_dest"]:
        print(f"  {result['missing_dest']} missing dest files", flush=True)
        for f in result["missing_dest_files"]:
            print(f"    - {f}", flush=True)
    if result["hash_mismatches"]:
        print(f"  {result['hash_mismatches']} hash mismatches (corrupted output)", flush=True)
        for f in result["hash_mismatch_files"]:
            print(f"    - {f}", flush=True)
    if result["merged"]:
        print(f"  {result['merged']} merged L/R source files ({result['merged'] // 2} pair{'s' if result['merged'] // 2 != 1 else ''})", flush=True)
    if result["unpaired"]:
        print(f"  {result['unpaired']} unpaired L/R source file{'s' if result['unpaired'] != 1 else ''} (blocked)", flush=True)


if __name__ == "__main__":
    main()
