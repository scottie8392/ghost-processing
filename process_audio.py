"""
Ghost Processing — audio conversion pipeline

Converts audio stems to a target sample rate (default 48kHz) using SoX with
shaped dithering. Detects and rejects silent files. Supports batch and watch
modes, parallel processing, NFS retry logic, and auto-verification.

Usage:
    python process_audio.py --config config.local.yaml
    python process_audio.py --config config.docker.yaml
"""

import os
import sys
import time
import logging
import multiprocessing
import hashlib
import json
import shutil
import signal
import argparse
import tempfile
import subprocess
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError


import psutil
import yaml
from pydub import AudioSegment, silence
from pydub.exceptions import CouldntDecodeError
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Globals — initialized in __main__ after set_start_method to avoid fork issues
shutdown_event = None
file_lock = None


def ignore_sigint():
    """Worker initializer: ignore SIGINT so the parent process handles shutdown."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def prune_logs(log_dir, keep=10):
    """Keep only the most recent `keep` run_*.log files; delete the rest."""
    try:
        logs = sorted(
            [f for f in os.listdir(log_dir) if f.startswith("run_") and f.endswith(".log")],
            reverse=True,
        )
        for old in logs[keep:]:
            try:
                os.remove(os.path.join(log_dir, old))
            except OSError:
                pass
    except OSError:
        pass


def setup_logging(log_dir, verbose=False, dry_run=False):
    run_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
    logger = logging.getLogger()
    logger.setLevel(level)
    handlers = [logging.StreamHandler()]
    if not dry_run:
        os.makedirs(log_dir, exist_ok=True)
        prune_logs(log_dir)
        run_log = os.path.join(log_dir, f"run_{run_ts}.log")
        main_log = os.path.join(log_dir, "process_audio.log")
        handlers += [logging.FileHandler(main_log), logging.FileHandler(run_log)]
    else:
        run_log = None
    for handler in handlers:
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    if run_log:
        logging.info(f"Run log: {run_log}")
    return run_log, run_ts


def load_config(config_path):
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    # Environment variable overrides (useful for Docker)
    config["source_dir"] = os.getenv("SOURCE_DIR", config.get("source_dir"))
    config["dest_base"] = os.getenv("DEST_BASE", config.get("dest_base"))
    config["log_dir"] = os.getenv("LOG_DIR", config.get("log_dir", "/tmp/logs"))
    # Defaults for optional keys
    config.setdefault("max_workers", max(1, multiprocessing.cpu_count() // 2))
    config.setdefault("target_sample_rate", 48000)
    config.setdefault("bit_depth", 24)
    config.setdefault("silence_thresh", -50.0)
    config.setdefault("min_silence_len", 200)
    config.setdefault("min_non_silent_len", 10)
    config.setdefault("stability_wait_sec", 60)
    config.setdefault("dry_run", False)
    config.setdefault("watch_mode", False)
    config.setdefault("verbose", False)
    config.setdefault("mount_type", None)
    config.setdefault("nfs_server_path", None)
    config.setdefault("mount_point", None)
    config.setdefault("enable_script_remount", False)
    return config


# ---------------------------------------------------------------------------
# Network remount
# ---------------------------------------------------------------------------

def remount_network(config):
    """
    Attempt to remount the network share. Only called when enable_script_remount
    is true — otherwise AutoMounter handles it automatically.
    """
    mount_point = config.get("mount_point")
    mount_type = config.get("mount_type")
    if not mount_point or not mount_type:
        return False
    try:
        subprocess.run(["umount", "-f", mount_point], check=False)
        os.makedirs(mount_point, exist_ok=True)
        if mount_type == "nfs":
            server_path = config.get("nfs_server_path")
            if not server_path:
                return False
            subprocess.run(
                ["mount", "-t", "nfs", "-o", "vers=4", server_path, mount_point],
                check=True,
            )
        elif mount_type == "smb":
            smb_url = config.get("smb_url")
            if not smb_url:
                return False
            subprocess.run(["mount", "-t", "smbfs", smb_url, mount_point], check=True)
        else:
            return False
        logging.info(f"Remounted {mount_type} at {mount_point}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Remount failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Audio utilities
# ---------------------------------------------------------------------------

def file_hash(file_path):
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_audio_file(file_path):
    return file_path.lower().endswith((".wav", ".aif", ".aiff"))


def get_sample_rate(file_path):
    """Return the sample rate in Hz using soxi, or None on failure."""
    try:
        result = subprocess.run(
            ["soxi", "-r", file_path], capture_output=True, text=True, check=True
        )
        return int(result.stdout.strip())
    except Exception:
        return None


def get_bit_depth(file_path):
    """Return the bit depth as an integer using soxi, or None on failure."""
    try:
        result = subprocess.run(
            ["soxi", "-b", file_path], capture_output=True, text=True, check=True
        )
        return int(result.stdout.strip())
    except Exception:
        return None


def fmt_rate(rate):
    """Format a sample rate for display: 44100 → '44.1k', 48000 → '48k'."""
    if rate is None:
        return "?k"
    k = rate / 1000
    return f"{k:g}k"


def fmt_depth(depth):
    """Format bit depth for display: 24 → '24b', '32f' → '32f'."""
    if depth is None:
        return "?b"
    return "32f" if str(depth) == "32f" else f"{depth}b"


def output_suffix(target_rate, bit_depth):
    """Return the combined rate+depth suffix used in dest dir and file names.

    Examples:
        48000, 24   → "48k-24b"
        44100, 16   → "44k-16b"
        96000, "32f" → "96k-32f"
    """
    rate_part  = f"{target_rate // 1000}k"
    depth_part = "32f" if str(bit_depth) == "32f" else f"{bit_depth}b"
    return f"{rate_part}-{depth_part}"


def check_silence(audio_file, silence_thresh, min_silence_len, min_non_silent_len):
    """
    Returns (is_silent, peak_db).
    peak_db is a float (dBFS), or None on decode error.
    Loads audio once and returns both results to avoid a second file read.
    """
    try:
        audio = AudioSegment.from_file(audio_file)
        peak_db = audio.max_dBFS  # -inf for truly silent files
        non_silent = silence.detect_nonsilent(
            audio, min_silence_len=min_non_silent_len, silence_thresh=silence_thresh
        )
        return not bool(non_silent), peak_db
    except CouldntDecodeError as e:
        logging.error(f"Decode error for {audio_file}: {e}")
        return False, None  # Treat as non-silent to avoid false rejects
    except Exception as e:
        logging.error(f"Silence detection error for {audio_file}: {e}")
        return False, None


def resample_audio(audio_file, dest_file, target_rate, bit_depth, dry_run):
    if dry_run:
        return True
    try:
        is_float = str(bit_depth) == "32f"
        depth_str = "32" if is_float else str(bit_depth)
        fmt_opts = ["-b", depth_str]
        if is_float:
            fmt_opts += ["-e", "floating-point"]
        # Dithering only applies to integer output; skip for float
        effects = ["rate", "-v", str(target_rate)]
        if not is_float:
            effects += ["dither", "-s"]
        cmd = ["sox", audio_file] + fmt_opts + [dest_file] + effects
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"SoX error for {audio_file}: {e.stderr.decode()}")
        return False
    except Exception as e:
        logging.error(f"Resample error for {audio_file}: {e}")
        return False


# ---------------------------------------------------------------------------
# JSON log management
# ---------------------------------------------------------------------------

def append_log(log_path, data, is_list=False):
    """Atomic read-modify-write for JSON logs. Thread-safe via file_lock."""
    with file_lock:
        if os.path.exists(log_path):
            try:
                with open(log_path, "r") as f:
                    logs = json.load(f)
            except json.JSONDecodeError as e:
                logging.error(f"Corrupt {os.path.basename(log_path)}: {e} — resetting")
                logs = [] if is_list else {}
        else:
            logs = [] if is_list else {}
        if is_list:
            if not isinstance(logs, list):
                logs = []
            logs.append(data)
        else:
            logs.update(data)
        dir_name = os.path.dirname(log_path) or "."
        with tempfile.NamedTemporaryFile(mode="w", dir=dir_name, delete=False) as tmp:
            json.dump(logs, tmp, indent=4)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.rename(tmp.name, log_path)


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def process_file(file_path, config, rejects_log, progress_log, dest_dir):
    if shutdown_event and shutdown_event.is_set():
        return None

    target_rate = config.get("target_sample_rate", 48000)

    rel_path = os.path.relpath(file_path, config["source_dir"])

    for attempt in range(3):
        try:
            # Reject zero-byte files immediately
            if os.path.getsize(file_path) == 0:
                logging.warning(f"Rejected: {rel_path}  (zero-byte)")
                append_log(
                    rejects_log,
                    {"path": file_path, "reason": "Zero-byte file", "timestamp": str(datetime.now())},
                    is_list=True,
                )
                return None
            base, ext = os.path.splitext(rel_path)
            bit_depth = config.get("bit_depth", 24)
            suffix    = output_suffix(target_rate, bit_depth)
            # AIFF can't encode floating-point PCM — SoX silently falls back to
            # 32i. When 32f is requested for an AIF/AIFF source, output as .wav
            # which reliably supports float.
            is_float = str(bit_depth) == "32f"
            force_wav = config.get("force_wav", False)
            dest_ext  = ".wav" if (force_wav or (is_float and ext.lower() in (".aif", ".aiff"))) else ext
            dest_path = os.path.join(dest_dir, f"{base}-{suffix}{dest_ext}")
            if not config.get("dry_run"):
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            # Skip if previously converted or copied and source hasn't changed
            with file_lock:
                try:
                    progress = (
                        json.load(open(progress_log)) if os.path.exists(progress_log) else {}
                    )
                except json.JSONDecodeError:
                    progress = {}
            if rel_path in progress and progress[rel_path].get("status") in ("converted", "copied"):
                if os.path.exists(dest_path) and file_hash(file_path) == progress[rel_path].get("source_hash"):
                    logging.info(f"Already processed, skipping: {rel_path}")
                    return "skipped"

            sample_rate = get_sample_rate(file_path)
            source_bits = get_bit_depth(file_path)
            is_float_target = str(bit_depth) == "32f"
            target_bits_int = 32 if is_float_target else int(bit_depth)
            already_right_rate  = sample_rate is not None and sample_rate == target_rate
            already_right_depth = source_bits is not None and source_bits == target_bits_int
            src_fmt  = f"{fmt_rate(sample_rate)}/{fmt_depth(source_bits)}"
            dest_fmt = f"{fmt_rate(target_rate)}/{fmt_depth(bit_depth)}"
            logging.info(f"Checking: {rel_path}  ({src_fmt})")

            # Silence check runs on ALL files before copy or convert.
            # A silent file never reaches the destination regardless of format.
            is_silent, peak_db = check_silence(
                file_path,
                config["silence_thresh"],
                config["min_silence_len"],
                config["min_non_silent_len"],
            )
            peak_str = f"  peak {peak_db:.1f}dBFS" if peak_db is not None and peak_db != float("-inf") else "  peak -∞dBFS" if peak_db is not None else ""
            if is_silent:
                logging.info(f"Rejected: {rel_path}  ({src_fmt}, silent,{peak_str})")
                if not config.get("dry_run"):
                    append_log(
                        rejects_log,
                        {"path": file_path, "reason": "Entirely silent", "timestamp": str(datetime.now())},
                        is_list=True,
                    )
                return None

            # Already at target rate and bit depth — copy, don't resample
            if already_right_rate and already_right_depth:
                if config["dry_run"]:
                    logging.info(f"[DRY RUN] Would copy: {rel_path}  ({src_fmt}, no conversion needed,{peak_str})")
                    return "copied"
                shutil.copy2(file_path, dest_path)
                src_hash = file_hash(file_path)
                append_log(
                    progress_log,
                    {rel_path: {"status": "copied", "source_hash": src_hash, "timestamp": str(datetime.now())}},
                    is_list=False,
                )
                logging.info(f"Copied: {rel_path}  ({src_fmt}, no conversion needed,{peak_str})")
                return "copied"

            if resample_audio(file_path, dest_path, target_rate, bit_depth, config["dry_run"]):
                if not config["dry_run"]:
                    src_hash = file_hash(file_path)
                    append_log(
                        progress_log,
                        {rel_path: {"status": "converted", "source_hash": src_hash, "timestamp": str(datetime.now())}},
                        is_list=False,
                    )
                if config["dry_run"]:
                    logging.info(f"[DRY RUN] Would convert: {rel_path}  ({src_fmt} → {dest_fmt},{peak_str})")
                else:
                    logging.info(f"Converted: {rel_path}  ({src_fmt} → {dest_fmt},{peak_str})")
                return file_path
            return None

        except OSError as e:
            # Only retry on network-related errors affecting the source file itself.
            # ENOENT (2) on a different path (e.g. missing dest dir) is a real bug, not a network drop.
            is_network_enoent = e.errno == 2 and (e.filename is None or str(e.filename) == file_path)
            if is_network_enoent or e.errno == 57:  # ENOENT on source, or socket not connected
                logging.warning(f"Network error on {file_path} (attempt {attempt + 1}/3): {e}")
                if config.get("enable_script_remount"):
                    remount_network(config)
                time.sleep(5)
                continue
            raise
        except KeyboardInterrupt:
            return None

    logging.error(f"Failed after 3 attempts: {file_path}")
    return None


def batch_process(files, config, rejects_log, progress_log, dest_dir):
    converted = copied = rejected = skipped = 0
    executor = None
    try:
        executor = ProcessPoolExecutor(
            max_workers=config["max_workers"], initializer=ignore_sigint
        )
        futures = {
            executor.submit(process_file, f, config, rejects_log, progress_log, dest_dir): f
            for f in files
        }
        for future in as_completed(futures):
            if shutdown_event.is_set():
                logging.info("Shutdown signaled — stopping queue")
                break
            try:
                result = future.result(timeout=300)
                if result == "copied":
                    copied += 1
                elif result == "skipped":
                    skipped += 1
                elif result:
                    converted += 1
                else:
                    rejected += 1
            except TimeoutError:
                logging.error(f"Timeout: {futures[future]}")
                future.cancel()
                rejected += 1
            except Exception as e:
                logging.error(f"Error on {futures[future]}: {e}")
                rejected += 1
    finally:
        if executor:
            executor.shutdown(wait=True, cancel_futures=True)
    return converted, copied, rejected, skipped


# ---------------------------------------------------------------------------
# Post-run verification
# ---------------------------------------------------------------------------

def run_verification(config, dest_dir, progress_log, rejects_log):
    """
    Quick post-run check: confirms all source files are accounted for in
    progress.json or rejects.json. Logs warnings if anything is missing.
    Run verify_audio.py for a full report with hash checking.
    """
    logging.info("--- Post-run verification ---")
    source_files = set(collect_files(config["source_dir"]))
    try:
        progress = json.load(open(progress_log)) if os.path.exists(progress_log) else {}
    except json.JSONDecodeError:
        progress = {}
    try:
        rejects = (
            [e["path"] for e in json.load(open(rejects_log)) if isinstance(e, dict) and "path" in e]
            if os.path.exists(rejects_log)
            else []
        )
    except json.JSONDecodeError:
        rejects = []

    all_processed   = {os.path.join(config["source_dir"], rel) for rel in progress}
    n_converted     = sum(1 for d in progress.values() if d.get("status") == "converted")
    n_copied        = sum(1 for d in progress.values() if d.get("status") == "copied")
    n_skipped       = sum(1 for d in progress.values() if d.get("status") == "skipped_rate")
    rejected_sources = set(rejects)
    unprocessed = source_files - (all_processed | rejected_sources)

    target_rate = config.get("target_sample_rate", 48000)
    bit_depth   = config.get("bit_depth", 24)
    is_float    = str(bit_depth) == "32f"
    suffix      = output_suffix(target_rate, bit_depth)
    missing_dest = []
    for rel, data in progress.items():
        if data.get("status") not in ("converted", "copied"):
            continue
        base, ext = os.path.splitext(rel)
        # Mirror the extension logic used during conversion
        force_wav = config.get("force_wav", False)
        dest_ext  = ".wav" if (force_wav or (is_float and ext.lower() in (".aif", ".aiff"))) else ext
        dest_path = os.path.join(dest_dir, f"{base}-{suffix}{dest_ext}")
        if not os.path.exists(dest_path):
            missing_dest.append(rel)

    logging.info(
        f"Verification: {n_converted} converted, {n_copied} copied, {n_skipped} skipped, "
        f"{len(rejected_sources)} rejected, {len(unprocessed)} unprocessed, {len(missing_dest)} missing dest files"
    )
    for f in sorted(unprocessed):
        logging.warning(f"  Unprocessed: {f}")
    for f in sorted(missing_dest):
        logging.error(f"  Missing dest: {f}")

    return len(unprocessed), len(missing_dest)


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def is_file_stable(file_path, wait_sec):
    last_size = os.path.getsize(file_path)
    start_time = time.time()
    while time.time() - start_time < wait_sec * 2:
        time.sleep(10)
        current_size = os.path.getsize(file_path)
        if current_size == last_size and time.time() - start_time >= wait_sec:
            return True
        elif current_size != last_size:
            last_size = current_size
            start_time = time.time()
    return False


class WatchHandler(FileSystemEventHandler):
    def __init__(self, config, rejects_log, progress_log, dest_dir):
        self.config = config
        self.rejects_log = rejects_log
        self.progress_log = progress_log
        self.dest_dir = dest_dir

    def on_created(self, event):
        if shutdown_event and shutdown_event.is_set():
            return
        if not event.is_directory and is_audio_file(event.src_path):
            logging.info(f"New file detected: {event.src_path}")
            if is_file_stable(event.src_path, self.config["stability_wait_sec"]):
                process_file(
                    event.src_path, self.config, self.rejects_log,
                    self.progress_log, self.dest_dir
                )

    def on_modified(self, event):
        self.on_created(event)


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def terminate_processes():
    current = psutil.Process(os.getpid())
    for child in current.children(recursive=True):
        try:
            child.terminate()
            child.wait(timeout=5)
            if child.is_running():
                child.kill()
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            logging.error(f"Error terminating {child.pid}: {e}")


def signal_handler(sig, frame):
    logging.info("Interrupt received — shutting down gracefully...")
    if shutdown_event:
        shutdown_event.set()
    terminate_processes()
    sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ghost Processing — audio conversion pipeline")
    parser.add_argument("--config", required=True, help="Path to YAML config file")
    args = parser.parse_args()

    config = load_config(args.config)
    _, run_ts = setup_logging(config["log_dir"], config.get("verbose", False), config.get("dry_run", False))
    signal.signal(signal.SIGINT, signal_handler)

    source_name = os.path.basename(os.path.normpath(config["source_dir"]))
    target_rate = config.get("target_sample_rate", 48000)
    bit_depth   = config.get("bit_depth", 24)
    suffix      = output_suffix(target_rate, bit_depth)
    dest_dir    = os.path.join(config["dest_base"], f"{source_name}-{suffix}")
    dry_run     = config.get("dry_run", False)

    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)
        # Write a copy of this run's log into the dest folder so each conversion
        # folder is self-contained — open it later and the full record is right there.
        dest_log = os.path.join(dest_dir, f"run_{run_ts}.log")
        dest_handler = logging.FileHandler(dest_log)
        dest_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
        logging.getLogger().addHandler(dest_handler)

    rejects_log = os.path.join(dest_dir, "rejects.json")
    progress_log = os.path.join(dest_dir, "progress.json")

    start_time = time.time()

    if config["watch_mode"]:
        logging.info(f"Watch mode — monitoring: {config['source_dir']}")
        handler = WatchHandler(config, rejects_log, progress_log, dest_dir)
        observer = Observer()
        observer.schedule(handler, config["source_dir"], recursive=True)
        observer.start()
        try:
            while not shutdown_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            observer.stop()
            observer.join()
    else:
        logging.info(f"Session: {source_name}  →  {suffix}")
        files = collect_files(config["source_dir"])
        logging.info(f"Found {len(files)} audio files")
        try:
            converted, copied, rejected, skipped = batch_process(files, config, rejects_log, progress_log, dest_dir)
            elapsed = time.time() - start_time
            parts = [f"{converted} converted"]
            if copied:   parts.append(f"{copied} copied")
            if rejected: parts.append(f"{rejected} silent")
            if skipped:  parts.append(f"{skipped} skipped")
            summary = f"Done: {', '.join(parts)} in {elapsed:.1f}s"
            logging.info(summary)
        except KeyboardInterrupt:
            shutdown_event.set()

        # Auto-verification after batch (skipped in dry run — nothing was written)
        if config.get("dry_run"):
            logging.info("Dry run complete — no files written.")
        else:
            unprocessed, missing = run_verification(config, dest_dir, progress_log, rejects_log)
            if unprocessed > 0 or missing > 0:
                print(f"WARNING: {unprocessed} unprocessed, {missing} missing dest files.")
                print("Run verify_audio.py for a full report.")
            else:
                print("Verification passed — all files accounted for.")


if __name__ == "__main__":
    import multiprocessing as mp
    if mp.get_start_method(allow_none=True) != "fork":
        try:
            mp.set_start_method("fork")
        except RuntimeError:
            pass
    file_lock = mp.Lock()
    shutdown_event = mp.Event()
    main()
