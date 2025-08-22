import os
import json
import yaml
import hashlib
from argparse import ArgumentParser

def file_hash(file_path):
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def is_audio_file(file_path):
    return file_path.lower().endswith((".wav", ".aiff"))

def collect_files(directory):
    files = []
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            if filename.startswith('.'):  # Skip hidden files
                continue
            file_path = os.path.join(root, filename)
            if is_audio_file(file_path):
                files.append(file_path)
    return files

def load_config(config_path):
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    return config

def main():
    parser = ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to YAML config")
    args = parser.parse_args()

    config = load_config(args.config)
    source_dir = config["source_dir"]
    dest_base = config["dest_base"]
    source_name = os.path.basename(os.path.normpath(source_dir))
    dest_dir = os.path.join(dest_base, f"{source_name}-48")

    rejects_log = os.path.join(dest_dir, "rejects.json")
    progress_log = os.path.join(dest_dir, "progress.json")

    # Collect all source audio files
    source_files = set(collect_files(source_dir))
    print(f"Found {len(source_files)} source audio files.")

    # Load logs if they exist
    if os.path.exists(progress_log):
        with open(progress_log, "r") as f:
            progress = json.load(f)
    else:
        progress = {}
    if os.path.exists(rejects_log):
        with open(rejects_log, "r") as f:
            rejects = [entry["path"] for entry in json.load(f)]
    else:
        rejects = []

    # Converted rel_paths to full source paths
    converted_sources = {os.path.join(source_dir, rel): data for rel, data in progress.items()}

    # Rejected full paths
    rejected_sources = set(rejects)

    # All processed sources
    processed = set(converted_sources.keys()) | rejected_sources

    # 1. Check for unprocessed sources
    unprocessed = source_files - processed
    if unprocessed:
        print("\nWARNING: Unprocessed source files (not in progress or rejects):")
        for f in sorted(unprocessed):
            print(f"  - {f}")
    else:
        print("\nAll source files accounted for in logs.")

    # 2. Verify converted files exist in dest and hashes match (optional integrity check)
    missing_dest = []
    hash_mismatch = []
    for source_path, data in converted_sources.items():
        rel_path = os.path.relpath(source_path, source_dir)
        base, ext = os.path.splitext(rel_path)
        dest_path = os.path.join(dest_dir, f"{base}-48{ext}")
        if not os.path.exists(dest_path):
            missing_dest.append(source_path)
        elif file_hash(source_path) != data.get("source_hash"):
            hash_mismatch.append(source_path)

    if missing_dest:
        print("\nERROR: Missing destination files for converted sources:")
        for f in sorted(missing_dest):
            print(f"  - Source: {f}")
    else:
        print("\nAll converted sources have matching destination files.")

    if hash_mismatch:
        print("\nWARNING: Hash mismatches (source changed since conversion?):")
        for f in sorted(hash_mismatch):
            print(f"  - {f}")
    else:
        print("\nAll converted sources have matching hashes.")

    # Summary
    print(f"\nSummary:\n- Converted: {len(converted_sources)}\n- Rejected: {len(rejected_sources)}\n- Unprocessed: {len(unprocessed)}\n- Missing dest: {len(missing_dest)}\n- Hash mismatches: {len(hash_mismatch)}")

if __name__ == "__main__":
    main()