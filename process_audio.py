import os
import sys
import time
import logging
import multiprocessing
import hashlib
import json
import signal
import argparse
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError
from tqdm import tqdm
import psutil
import yaml
from pydub import AudioSegment, silence
from pydub.exceptions import CouldntDecodeError
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import subprocess  # For remount

# NEW: Global shutdown event for graceful worker signaling
shutdown_event = None  # Initialize as None; set later

# Global vars for interrupt handling (updated to be dynamic)
ffmpeg_pids = []  # Still here but we'll use psutil primarily

# NEW: Lock for synchronizing access to shared JSON files (progress/rejects) - Moved creation to __main__
file_lock = None

# NEW: Initializer to ignore SIGINT in worker processes
def ignore_sigint():
    signal.signal(signal.SIGINT, signal.SIG_IGN)

# Configure logging (unchanged)
def setup_logging(log_dir, verbose=False):
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "process_audio.log")
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )
    return log_file

# Load config (YAML) - UPDATED: Lower default max_workers
def load_config(config_path):
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    # Override with env vars if set (for Docker)
    config["source_dir"] = os.getenv("SOURCE_DIR", config.get("source_dir"))
    config["dest_base"] = os.getenv("DEST_BASE", config.get("dest_base"))
    config["log_dir"] = os.getenv("LOG_DIR", config.get("log_dir", "/tmp/logs"))
    # Defaults - CHANGED: Lower max_workers for safety
    config["max_workers"] = config.get("max_workers", max(1, multiprocessing.cpu_count() // 4))  # e.g., 2 on 8-core
    config["smb_url"] = config.get("smb_url", None)  # Optional for remount
    config["mount_point"] = config.get("mount_point", None)  # Optional for remount
    return config

# Remount SMB (unchanged)
def remount_smb(smb_url, mount_point):
    if smb_url and mount_point:
        try:
            subprocess.run(["umount", mount_point], check=False)  # Force unmount
            os.makedirs(mount_point, exist_ok=True)  # Ensure dir exists
            subprocess.run(["mount", "-t", "smbfs", smb_url, mount_point], check=True)
            logging.info(f"Remounted SMB at {mount_point}")
            return True
        except subprocess.CalledProcessError as e:
            logging.error(f"Remount failed: {e}")
            return False
    return False

# File hash for integrity (unchanged)
def file_hash(file_path):
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

# Check if file is audio (.wav or .aiff) (unchanged)
def is_audio_file(file_path):
    return file_path.lower().endswith((".wav", ".aiff"))

# Silence detection (unchanged)
def is_entirely_silent(audio_file, silence_thresh, min_silence_len, min_non_silent_len):
    try:
        audio = AudioSegment.from_file(audio_file)
        non_silent_segments = silence.detect_nonsilent(
            audio, min_silence_len=min_non_silent_len, silence_thresh=silence_thresh
        )
        return not bool(non_silent_segments)  # Silent if no non-silent segments
    except CouldntDecodeError as e:
        logging.error(f"Decode error for {audio_file}: {e}")
        return False  # Treat as non-silent to avoid false rejects
    except Exception as e:
        logging.error(f"Error in silence detection for {audio_file}: {e}")
        return False

# Resample to 48kHz (UPDATED: Check shutdown_event)
def resample_audio(audio_file, dest_file, dry_run):
    if shutdown_event and shutdown_event.is_set():
        logging.info(f"Shutdown signaled; skipping resample for {audio_file}")
        return False
    if dry_run:
        logging.info(f"[DRY RUN] Would resample {audio_file} to {dest_file}")
        return True
    try:
        audio = AudioSegment.from_file(audio_file)
        if audio.frame_rate != 96000:
            logging.warning(f"Skipping {audio_file}: Sample rate is {audio.frame_rate}Hz, not 96kHz")
            return False
        resampled = audio.set_frame_rate(48000)  # pydub uses high-quality sinc
        resampled.export(dest_file, format="wav", codec="pcm_s24le")  # Force 24-bit signed PCM
        return True
    except Exception as e:
        logging.error(f"Resample error for {audio_file}: {e}")
        return False

# Process single file (UPDATED: Check shutdown_event, wrap in try for interrupt, add lock for progress read)
def process_file(file_path, config, rejects_log, progress_log, dest_dir):
    if shutdown_event and shutdown_event.is_set():
        logging.info(f"Shutdown signaled; skipping {file_path}")
        return None
    try:
        for attempt in range(3):  # Retry 3 times on disconnect
            try:
                if os.path.getsize(file_path) == 0:
                    logging.warning(f"Skipping zero-byte file: {file_path}")
                    append_log(rejects_log, {"path": file_path, "reason": "Zero-byte file", "timestamp": str(datetime.now())}, is_list=True)
                    return None

                rel_path = os.path.relpath(file_path, config["source_dir"])
                base, ext = os.path.splitext(rel_path)
                dest_path = os.path.join(dest_dir, f"{base}-48{ext}")
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)

                # Check progress (skip if done and hash matches) - UPDATED: With lock
                with file_lock:
                    if os.path.exists(progress_log):
                        try:
                            with open(progress_log, "r") as f:
                                progress = json.load(f)
                        except json.JSONDecodeError as e:
                            logging.error(f"Corrupt progress.json: {e} - resetting")
                            progress = {}
                    else:
                        progress = {}
                if rel_path in progress and progress[rel_path].get("status") == "converted":
                    if os.path.exists(dest_path) and file_hash(file_path) == progress[rel_path].get("source_hash"):
                        logging.info(f"Skipping {file_path}: Already converted")
                        return None

                # Silence detection
                if is_entirely_silent(
                    file_path, config["silence_thresh"], config["min_silence_len"], config["min_non_silent_len"]
                ):
                    append_log(rejects_log, {"path": file_path, "reason": "Entirely silent", "timestamp": str(datetime.now())}, is_list=True)
                    return None

                # Convert
                if resample_audio(file_path, dest_path, config["dry_run"]):
                    append_log(progress_log, {rel_path: {"status": "converted", "source_hash": file_hash(file_path), "timestamp": str(datetime.now())}}, is_list=False)
                    return file_path
                return None
            except OSError as e:
                if e.errno in [2, 57]:  # No such file / Socket not connected
                    logging.warning(f"Disconnect detected on {file_path} (attempt {attempt+1}/3): {e}")
                    if remount_smb(config["smb_url"], config["mount_point"]):
                        time.sleep(5)  # Wait for remount to stabilize
                        continue
                raise  # Re-raise if not disconnect or retries exhausted
        logging.error(f"Failed after 3 retries: {file_path}")
        return None
    except KeyboardInterrupt:
        logging.info(f"Interrupt in worker for {file_path}; exiting")
        return None

# Append to JSON log (UPDATED: With lock for read-modify-write)
def append_log(log_path, data, is_list=False):
    with file_lock:
        if os.path.exists(log_path):
            try:
                with open(log_path, "r") as f:
                    logs = json.load(f)
            except json.JSONDecodeError as e:
                logging.error(f"Corrupt log {log_path}: {e} - resetting")
                logs = [] if is_list else {}
        else:
            logs = [] if is_list else {}
        if is_list:
            if not isinstance(logs, list):
                logs = []
            logs.append(data)
        else:
            logs.update(data)
        with open(log_path, "w") as f:
            json.dump(logs, f, indent=4)

# Collect files (unchanged)
def collect_files(directory):
    files = []
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            file_path = os.path.join(root, filename)
            if is_audio_file(file_path):
                files.append(file_path)
    return files

# Main processing (UPDATED: Wrap in try-finally for shutdown, add initializer)
def batch_process(files, config, rejects_log, progress_log, dest_dir):
    converted = []
    executor = None  # Declare outside for finally block
    try:
        executor = ProcessPoolExecutor(max_workers=config["max_workers"], initializer=ignore_sigint)
        futures = {executor.submit(process_file, f, config, rejects_log, progress_log, dest_dir): f for f in files}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Processing files"):
            if shutdown_event.is_set():
                logging.info("Shutdown signaled; canceling remaining futures")
                break
            try:
                result = future.result(timeout=300)  # 5min timeout per file
                if result:
                    converted.append(result)
            except TimeoutError:
                logging.error(f"Timeout on file {futures[future]} - canceling")
                future.cancel()
            except Exception as e:
                logging.error(f"Error on file {futures[future]}: {e}")
        return len(converted), len(files) - len(converted)
    finally:
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)  # Force shutdown
            logging.info("Executor shut down")

# Stability check for watch mode (unchanged)
def is_file_stable(file_path, wait_sec):
    last_size = os.path.getsize(file_path)
    start_time = time.time()
    while time.time() - start_time < wait_sec * 2:  # Timeout after 2x wait
        time.sleep(10)  # Poll every 10s
        current_size = os.path.getsize(file_path)
        if current_size == last_size:
            if time.time() - start_time >= wait_sec:
                return True
        else:
            last_size = current_size
            start_time = time.time()
    return False

# Watch handler (UPDATED: Check shutdown_event)
class WatchHandler(FileSystemEventHandler):
    def __init__(self, config, rejects_log, progress_log, dest_dir):
        self.config = config
        self.rejects_log = rejects_log
        self.progress_log = progress_log
        self.dest_dir = dest_dir

    def on_created(self, event):
        if shutdown_event.is_set():
            return
        if not event.is_directory and is_audio_file(event.src_path):
            logging.info(f"New file: {event.src_path}")
            if is_file_stable(event.src_path, self.config["stability_wait_sec"]):
                process_file(event.src_path, self.config, self.rejects_log, self.progress_log, self.dest_dir)
                print(f"Processed new file: {event.src_path} - Done!")  # Terminal notification

    def on_modified(self, event):
        self.on_created(event)  # Same logic for mods

# Interrupt handlers - UPDATED: Dynamic psutil termination, including ffmpeg
def terminate_processes():
    logging.info("Terminating all child processes...")
    current = psutil.Process(os.getpid())
    for child in current.children(recursive=True):
        try:
            cmdline = ' '.join(child.cmdline())
            if 'ffmpeg' in cmdline or 'python' in cmdline.lower() or 'process_audio.py' in cmdline:
                child.terminate()  # Try TERM first
                child.wait(timeout=5)  # Wait briefly
                if child.is_running():
                    child.kill()  # Force if needed
                logging.info(f"Terminated process: {child.pid} ({cmdline[:50]}...)")
        except psutil.NoSuchProcess:
            pass  # Already gone
        except Exception as e:
            logging.error(f"Error terminating {child.pid}: {e}")

def signal_handler(sig, frame):
    logging.info("Interrupt received (SIGINT). Signaling shutdown...")
    if shutdown_event:
        shutdown_event.set()
    terminate_processes()
    sys.exit(0)

# Main - UPDATED: Set daemon, try-except for batch, signal handler
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to YAML config")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config["log_dir"], config["verbose"])
    signal.signal(signal.SIGINT, signal_handler)

    # Derive dest dir (unchanged)
    source_name = os.path.basename(os.path.normpath(config["source_dir"]))
    dest_dir = os.path.join(config["dest_base"], f"{source_name}-48")
    os.makedirs(dest_dir, exist_ok=True)

    rejects_log = os.path.join(dest_dir, "rejects.json")
    progress_log = os.path.join(dest_dir, "progress.json")

    start_time = time.time()

    if config["watch_mode"]:
        logging.info("Starting watch mode...")
        event_handler = WatchHandler(config, rejects_log, progress_log, dest_dir)
        observer = Observer()
        observer.schedule(event_handler, config["source_dir"], recursive=True)
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
        logging.info("Starting batch mode...")
        files = collect_files(config["source_dir"])
        try:
            converted_count, rejected_count = batch_process(files, config, rejects_log, progress_log, dest_dir)
            time_taken = time.time() - start_time
            stats = f"Processing complete: {converted_count} files converted, {rejected_count} rejected. Time taken: {time_taken:.2f}s. Check logs for details."
            logging.info(stats)
            print(stats)  # Terminal notification
        except KeyboardInterrupt:
            logging.info("Batch interrupted; cleaning up...")
            shutdown_event.set()
            terminate_processes()

if __name__ == "__main__":
    import multiprocessing as mp  # Alias to avoid full name
    current_method = mp.get_start_method(allow_none=True)
    if current_method is None or current_method != 'fork':
        try:
            mp.set_start_method('fork')  # Fix macOS hangs with spawn
        except RuntimeError as e:
            logging.warning(f"Could not set start method: {e}")
    file_lock = mp.Lock()  # Create after set_start_method
    shutdown_event = mp.Event()
    main()