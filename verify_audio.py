"""
Ghost Processing — verification script

Audits processed output to confirm all source files are accounted for.
Checks for unprocessed files, missing destination files, and hash mismatches.

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
            if filename.startswith("."):  # Skip macOS resource forks and hidden files
                continue
            file_path = os.path.join(root, filename)
            if is_audio_file(file_path):
                files.append(file_path)
    return files


def load_config(config_path):
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="Ghost Processing — verification")
    parser.add_argument("--config", required=True, help="Path to YAML config file")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON summary")
    args = parser.parse_args()

    config = load_config(args.config)
    source_dir = config["source_dir"]
    dest_base = config["dest_base"]
    source_name = os.path.basename(os.path.normpath(source_dir))
    target_rate = config.get("target_sample_rate", 48000)
    bit_depth   = config.get("bit_depth", 24)
    is_float    = str(bit_depth) == "32f"
    depth_part  = "32f" if is_float else f"{bit_depth}b"
    suffix      = f"{target_rate // 1000}k{depth_part}"
    dest_dir    = os.path.join(dest_base, f"{source_name}_{suffix}")
    progress_log = os.path.join(dest_dir, "progress.json")
    rejects_log = os.path.join(dest_dir, "rejects.json")

    source_files = set(collect_files(source_dir))

    # Load logs
    progress = {}
    if os.path.exists(progress_log):
        with open(progress_log) as f:
            try:
                progress = json.load(f)
            except json.JSONDecodeError:
                print("WARNING: progress.json is corrupted")

    rejects = []
    if os.path.exists(rejects_log):
        with open(rejects_log) as f:
            try:
                rejects = [e["path"] for e in json.load(f) if isinstance(e, dict) and "path" in e]
            except json.JSONDecodeError:
                print("WARNING: rejects.json is corrupted")

    converted_sources = {os.path.join(source_dir, rel): data for rel, data in progress.items()}
    rejected_sources = set(rejects)
    processed = set(converted_sources) | rejected_sources
    unprocessed = source_files - processed

    # Check destination files and hashes
    # Cache hashes to avoid re-reading the same source file twice
    hash_cache = {}
    missing_dest = []
    hash_mismatches = []

    for source_path, data in converted_sources.items():
        if data.get("status") not in ("converted", "copied"):
            continue
        rel = os.path.relpath(source_path, source_dir)
        base, ext = os.path.splitext(rel)
        dest_ext  = ".wav" if (is_float and ext.lower() in (".aif", ".aiff")) else ext
        dest_path = os.path.join(dest_dir, f"{base}_{suffix}{dest_ext}")
        if not os.path.exists(dest_path):
            missing_dest.append(source_path)
        else:
            if source_path not in hash_cache:
                hash_cache[source_path] = file_hash(source_path)
            if hash_cache[source_path] != data.get("source_hash"):
                hash_mismatches.append(source_path)

    if args.json:
        summary = {
            "source_files": len(source_files),
            "converted": len(converted_sources),
            "rejected": len(rejected_sources),
            "unprocessed": len(unprocessed),
            "missing_dest": len(missing_dest),
            "hash_mismatches": len(hash_mismatches),
            "unprocessed_files": sorted(unprocessed),
            "missing_dest_files": sorted(missing_dest),
            "hash_mismatch_files": sorted(hash_mismatches),
        }
        print(json.dumps(summary, indent=2))
        return

    print(f"Source files found: {len(source_files)}")

    if unprocessed:
        print(f"\nWARNING: {len(unprocessed)} unprocessed files:")
        for f in sorted(unprocessed):
            print(f"  - {f}")
    else:
        print("\nAll source files accounted for.")

    if missing_dest:
        print(f"\nERROR: {len(missing_dest)} missing destination files:")
        for f in sorted(missing_dest):
            print(f"  - {f}")
    else:
        print("All converted files have matching destinations.")

    if hash_mismatches:
        print(f"\nWARNING: {len(hash_mismatches)} hash mismatches (source changed since conversion?):")
        for f in sorted(hash_mismatches):
            print(f"  - {f}")
    else:
        print("All hashes match.")

    print(f"\nSummary:")
    print(f"  Converted:       {len(converted_sources)}")
    print(f"  Rejected:        {len(rejected_sources)}")
    print(f"  Unprocessed:     {len(unprocessed)}")
    print(f"  Missing dest:    {len(missing_dest)}")
    print(f"  Hash mismatches: {len(hash_mismatches)}")


if __name__ == "__main__":
    main()
