"""
Ghost Processing — web UI

Runs a local web server for launching audio conversion jobs.
Access at http://localhost:5001

Usage:
    python app.py
    (or double-click start.command on Mac)
"""

import collections
import json
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import yaml
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

# Suppress Flask/Werkzeug's "development server" warning and noisy request logs
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

app = Flask(__name__)

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
PROCESS_SCRIPT  = os.path.join(BASE_DIR, "process_audio.py")
PROFILE_PATH    = os.path.join(BASE_DIR, "profile.json")
LAST_JOB_PATH   = os.path.join(BASE_DIR, "last_job.json")
HISTORY_PATH    = os.path.join(BASE_DIR, "run_history.json")
VERSION_FILE    = os.path.join(BASE_DIR, "VERSION")
VERIFY_SCRIPT   = os.path.join(BASE_DIR, "verify_audio.py")

# Docker detection — /.dockerenv exists in all Docker containers
_is_docker = os.path.exists("/.dockerenv")

# Version cache — populated at startup
_version_cache = {"local": None, "remote": None, "up_to_date": True}


def _get_local_sha():
    """Read local SHA from VERSION file (Docker) or git (Mac/local)."""
    if os.path.exists(VERSION_FILE):
        try:
            sha = open(VERSION_FILE).read().strip()
            if sha and sha not in ("unknown", "dev", ""):
                return sha[:7]
        except Exception:
            pass
    try:
        # Use the main branch SHA, not HEAD — avoids false "update available"
        # when running from a feature branch whose HEAD differs from main.
        r = subprocess.run(
            ["git", "rev-parse", "--short", "main"],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=5
        )
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _fetch_remote_sha():
    """Fetch latest main commit SHA from GitHub API. Returns full SHA or None on failure."""
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/scottie8392/ghost-processing/commits/main",
            headers={"User-Agent": "ghost-processing-version-check"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())["sha"]  # full SHA for ancestry check
    except Exception:
        return None


def _init_version():
    local_sha  = _get_local_sha()
    remote_sha = _fetch_remote_sha()
    _version_cache["local"]  = local_sha
    # Store short (7-char) remote SHA for display only
    _version_cache["remote"] = remote_sha[:7] if remote_sha else None

    # Determine up_to_date: remote is None → can't tell, treat as ok.
    # Otherwise check if the remote commit is already in local history.
    # If git knows the remote SHA and it's an ancestor of local main,
    # local is at or ahead of remote → no update needed.
    if remote_sha and local_sha not in ("unknown", None):
        try:
            r = subprocess.run(
                ["git", "merge-base", "--is-ancestor", remote_sha, "main"],
                capture_output=True, cwd=BASE_DIR, timeout=5
            )
            _version_cache["up_to_date"] = (r.returncode == 0)
        except Exception:
            _version_cache["up_to_date"] = True  # can't check → don't nag
    else:
        _version_cache["up_to_date"] = True

threading.Thread(target=_init_version, daemon=True).start()

DEFAULT_PROFILE = {
    "source_dir": "",
    "dest_base": "",
    "log_dir": os.path.join(BASE_DIR, "logs"),
    "max_workers": 6,
    "dry_run": False,
    "force_wav": False,
    "combine_lr": False,
    "allow_unpaired_lr": False,
    "verbose": False,
    "target_sample_rate": 48000,
    "bit_depth": 24,
    "silence_thresh": -50.0,
    "min_non_silent_len": 10,
    "watch_mode": False,
    "stability_wait_sec": 60,
    # NAS connection
    "nas_ip": "",
    "nas_protocol": "nfs",
    "nas_username": "",
    "nas_password": "",
    "nas_remember_credentials": False,
    "nas_connection_history": [],
    # Advanced
    "enable_script_remount": False,
    "run_mode": "nas",
    "saved_sources": [],
}

# Runtime state
_active_process  = None
_log_queue       = queue.Queue()
_log_ring        = collections.deque(maxlen=500)   # recent lines for reconnecting clients
_is_running      = False
_stop_requested  = False   # True when user clicked Stop — distinguishes user-stop from crash
_lock            = threading.Lock()
_current_job     = None   # {"name": str, "source": str} set when a job starts


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def load_profile():
    if os.path.exists(PROFILE_PATH):
        try:
            with open(PROFILE_PATH) as f:
                profile = json.load(f)
            for k, v in DEFAULT_PROFILE.items():
                profile.setdefault(k, v)
            return profile
        except Exception:
            pass
    return dict(DEFAULT_PROFILE)


def save_profile(data):
    profile = load_profile()
    for key in data:
        if key in DEFAULT_PROFILE:
            profile[key] = data[key]
    # Don't persist credentials unless remember is explicitly set
    if not profile.get("nas_remember_credentials"):
        profile["nas_username"] = ""
        profile["nas_password"] = ""
    # Dry run is a one-off flag — never sticky
    profile.pop("dry_run", None)
    src = data.get("source_dir", "").strip()
    if src and src not in profile.get("saved_sources", []):
        profile.setdefault("saved_sources", []).insert(0, src)
        profile["saved_sources"] = profile["saved_sources"][:10]
    tmp_path = PROFILE_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(profile, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp_path, PROFILE_PATH)
    except OSError:
        # os.replace fails on Docker bind-mounted files (EBUSY) — fall back to direct write
        with open(PROFILE_PATH, "w") as f:
            json.dump(profile, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# NAS mounting
# ---------------------------------------------------------------------------

def detect_nas_share(source_path, protocol):
    """
    Auto-detect the NFS/SMB share root from a source path.

    NFS (Synology convention):  /volume1/Stems/...  →  share=/volume1/Stems
    NFS (generic):              /data/audio/...      →  share=/data
    SMB:                        Stems/subdir/...     →  share=Stems

    Returns (share, subpath) where share is what to mount.
    """
    parts = source_path.strip("/").split("/")
    parts = [p for p in parts if p]  # drop empty components
    if not parts:
        return source_path, ""

    if protocol == "nfs":
        # Synology: first component is "volumeN", second is the share folder
        if parts[0].startswith("volume") and len(parts) >= 2:
            share = "/" + "/".join(parts[:2])
            subpath = "/".join(parts[2:])
        else:
            share = "/" + parts[0]
            subpath = "/".join(parts[1:])
    else:  # smb
        share = parts[0]
        subpath = "/".join(parts[1:])

    return share, subpath


def get_current_mount(nas_ip, nas_share, protocol):
    """
    Check if the NAS share is currently mounted. Returns mount point path or None.
    Parses `mount` output:
      NFS: "10.0.1.10:/volume1/Stems on /Volumes/Stems (nfs, ...)"
      SMB: "//user@10.0.1.10/Stems on /Volumes/Stems (smbfs, ...)"
    """
    result = subprocess.run(["mount"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if protocol == "nfs":
            search = f"{nas_ip}:{nas_share}"
        else:
            share_name = nas_share.strip("/").split("/")[-1]
            search = f"{nas_ip}/{share_name}"
        if search in line:
            m = re.search(r" on (/[^\s(]+)", line)
            if m:
                return m.group(1)
    return None


def mount_nas(nas_ip, nas_share, protocol, username="", password=""):  # noqa: C901
    """
    Mount a NAS share and return (mount_point, error_message).

    NFS strategy: uses mount_nfs with stability options (soft, timeo, retrans,
    resvport) to avoid the hanging/dropping behaviour of default Finder mounts.
    Requires passwordless sudo for mount_nfs — configured by setup.sh.

    SMB strategy: uses osascript (no sudo needed).
    """
    if not nas_ip or not nas_share:
        return None, "NAS IP and share path are required"

    # Already mounted?
    existing = get_current_mount(nas_ip, nas_share, protocol)
    if existing:
        return existing, None

    if protocol == "nfs":
        share = nas_share if nas_share.startswith("/") else f"/{nas_share}"
        share_name = share.strip("/").split("/")[-1]
        mount_point_path = f"/Volumes/{share_name}"
        nfs_opts = "soft,resvport,timeo=60,retrans=5,vers=3,intr"

        # Probe sudo availability by attempting mkdir (fails instantly if not configured).
        mkdir_r = subprocess.run(
            ["sudo", "mkdir", "-p", mount_point_path],
            capture_output=True, text=True, timeout=5,
        )
        sudo_works = mkdir_r.returncode == 0

        if sudo_works:
            # Strategy 1: sudo mount_nfs — silent, stable, no macOS GUI dialogs.
            cmd = ["sudo", "mount_nfs", "-o", nfs_opts, f"{nas_ip}:{share}", mount_point_path]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    return None, r.stderr.strip() or r.stdout.strip() or "NFS mount failed"
            except subprocess.TimeoutExpired:
                return None, "NFS mount timed out — check the NAS allows this machine's IP"
        else:
            # Strategy 2: osascript — no sudo needed. May show a macOS dialog if the
            # NFS server denies access, but works silently on success.
            url = f"nfs://{nas_ip}{share}"
            try:
                r2 = subprocess.run(
                    ["osascript", "-e", f'mount volume "{url}"'],
                    capture_output=True, text=True, timeout=15,
                )
                if r2.returncode != 0:
                    return None, r2.stderr.strip() or r2.stdout.strip() or "NFS mount failed"
            except subprocess.TimeoutExpired:
                return None, "NFS mount timed out"

    else:  # smb
        share = nas_share.strip("/")
        if username and password:
            url = f"smb://{username}:{password}@{nas_ip}/{share}"
        elif username:
            url = f"smb://{username}@{nas_ip}/{share}"
        else:
            url = f"smb://{nas_ip}/{share}"

        result = subprocess.run(
            ["osascript", "-e", f'mount volume "{url}"'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "Mount failed"
            return None, err

    time.sleep(0.8)
    mount_point = get_current_mount(nas_ip, nas_share, protocol)
    if not mount_point:
        # Best-effort fallback from share name
        share_name = nas_share.strip("/").split("/")[-1]
        candidate = f"/Volumes/{share_name}"
        if os.path.ismount(candidate):
            mount_point = candidate
    if not mount_point:
        return None, "Mounted but could not determine mount point — check /Volumes/"
    return mount_point, None


def unmount_nas(mount_point):
    """Unmount using diskutil (macOS, no sudo needed for user-mounted shares)."""
    result = subprocess.run(
        ["diskutil", "unmount", mount_point],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True, None
    # Try sudo umount as fallback (for sudo-mounted shares)
    result2 = subprocess.run(
        ["sudo", "umount", mount_point],
        capture_output=True, text=True,
    )
    if result2.returncode == 0:
        return True, None
    return False, result.stderr.strip() or result.stdout.strip()


def resolve_paths(data):
    """
    Resolve source and destination to full local paths.

    If nas_ip is set: auto-detect the share root from the source path, mount it,
    then return paths rooted at the mount point.

    If dest_base is empty: default to the parent directory of source_dir so
    output lands next to the source session folder.

    Returns (source_dir, dest_base, error).
    """
    nas_ip = data.get("nas_ip", "").strip()
    source = data.get("source_dir", "").strip()
    dest = data.get("dest_base", "").strip()

    if not nas_ip:
        # Direct paths (local or Docker)
        if not dest:
            dest = os.path.dirname(source)
        return source, dest, None

    protocol = data.get("nas_protocol", "nfs")
    username = data.get("nas_username", "").strip()
    password = data.get("nas_password", "").strip()

    # Auto-detect the mountable share from the source path
    nas_share, source_subpath = detect_nas_share(source, protocol)

    mount_point, error = mount_nas(nas_ip, nas_share, protocol, username, password)
    if error:
        return None, None, f"Failed to mount NAS: {error}"

    source_full = os.path.join(mount_point, source_subpath)

    if dest:
        # If dest is a local absolute path (its parent exists on this machine),
        # use it directly — e.g. /Users/scottie/Desktop. Otherwise treat it as
        # a NAS-relative path and resolve it through the mount point.
        dest_parent = os.path.dirname(dest.rstrip("/")) or "/"
        if os.path.isabs(dest) and os.path.exists(dest_parent):
            dest_full = dest
        else:
            _, dest_subpath = detect_nas_share(dest, protocol)
            dest_full = os.path.join(mount_point, dest_subpath)
    else:
        # Default: same directory as source (output folder lands next to it)
        dest_full = os.path.dirname(source_full)

    return source_full, dest_full, None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", profile=load_profile())


@app.route("/profile", methods=["GET"])
def get_profile():
    return jsonify(load_profile())


@app.route("/profile", methods=["POST"])
def post_profile():
    """Save current form state as new defaults without starting a run."""
    save_profile(request.json or {})
    return jsonify({"ok": True})


@app.route("/connect", methods=["POST"])
def connect():
    """
    Test NAS connectivity.

    Strategy:
    - Verifies the NAS is reachable using showmount (NFS) or smbutil (SMB) — no mount needed.
    - If source_dir is also provided, auto-detects the share and mounts it so the
      browse button can navigate the live volume. If mounting fails, connectivity
      is still reported as successful (mount_point will be null).
    """
    data = request.json
    save_profile(data)
    nas_ip   = data.get("nas_ip", "").strip()
    protocol = data.get("nas_protocol", "nfs")
    username = data.get("nas_username", "").strip()
    password = data.get("nas_password", "").strip()
    source   = data.get("source_dir", "").strip()

    if not nas_ip:
        return jsonify({"success": False, "message": "NAS IP is required"})

    # --- Verify reachability (no mount) ---
    try:
        if protocol == "nfs":
            result = subprocess.run(
                ["showmount", "-e", nas_ip],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip() or "Could not reach NAS"
                return jsonify({"success": False, "message": err})
        else:  # smb — fast TCP handshake on port 445 (no credentials needed)
            import socket as _socket
            try:
                sock = _socket.create_connection((nas_ip, 445), timeout=5)
                sock.close()
            except (_socket.timeout, ConnectionRefusedError, OSError) as e:
                return jsonify({"success": False, "message": f"Cannot reach {nas_ip} on SMB port 445"})
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Connection timed out"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

    # --- Optional: mount share so folder browser works ---
    mount_point = None
    mount_err   = None
    nas_share   = None
    if source:
        # exact_share bypasses path detection — used when a chip is clicked and
        # the full export path is already known (e.g. /mnt/user/unraid_media).
        exact_share = data.get("exact_share", "").strip()
        if exact_share:
            nas_share = exact_share
        else:
            nas_share, _ = detect_nas_share(source, protocol)
        mount_point, mount_err = mount_nas(nas_ip, nas_share, protocol, username, password)

    # Save to connection history
    profile = load_profile()
    history = profile.get("nas_connection_history", [])
    entry = {"ip": nas_ip, "protocol": protocol}
    history = [h for h in history if h.get("ip") != nas_ip]
    history.insert(0, entry)
    profile["nas_connection_history"] = history[:5]
    with open(PROFILE_PATH, "w") as f:
        json.dump(profile, f, indent=2)

    return jsonify({
        "success": True,
        "mount_point": mount_point,
        "mount_error": mount_err,
        "nas_share":   nas_share,
        "message":     f"Reached {nas_ip}",
    })


@app.route("/disconnect", methods=["POST"])
def disconnect():
    mount_point = (request.json or {}).get("mount_point", "").strip()
    if not mount_point:
        return jsonify({"success": False, "message": "No mount point specified"})
    ok, error = unmount_nas(mount_point)
    if ok:
        return jsonify({"success": True})
    return jsonify({"success": False, "message": error})


@app.route("/preflight", methods=["POST"])
def preflight():
    """
    Pre-flight environment check — called by the UI before starting a job.
    Validates: SoX installed, source path accessible, dest parent writable.
    Returns: {"checks": [{"name", "ok", "message"}], "all_ok": bool}
    """
    import shutil as _shutil

    data = request.json or {}
    checks = []

    # 1. SoX installed
    sox_path = _shutil.which("sox")
    if sox_path:
        try:
            r = subprocess.run(["sox", "--version"], capture_output=True, text=True, timeout=5)
            version_line = (r.stderr or r.stdout or "").strip().splitlines()[0] if (r.stderr or r.stdout) else ""
            checks.append({"name": "SoX", "ok": True, "message": version_line or sox_path})
        except Exception:
            checks.append({"name": "SoX", "ok": True, "message": sox_path})
    else:
        checks.append({"name": "SoX", "ok": False,
                       "message": "sox not found — install via Homebrew: brew install sox"})

    # 2. Source path readable
    source_dir, dest_base, mount_error = resolve_paths(data)
    if mount_error:
        checks.append({"name": "Source", "ok": False, "message": f"Mount error: {mount_error}"})
        checks.append({"name": "Destination", "ok": False, "message": "Cannot check — source mount failed"})
    else:
        if source_dir and os.path.isdir(source_dir) and os.access(source_dir, os.R_OK):
            checks.append({"name": "Source", "ok": True, "message": source_dir})
        elif not source_dir:
            checks.append({"name": "Source", "ok": False, "message": "No source path provided"})
        elif not os.path.isdir(source_dir):
            checks.append({"name": "Source", "ok": False, "message": f"Directory not found: {source_dir}"})
        else:
            checks.append({"name": "Source", "ok": False, "message": f"Not readable: {source_dir}"})

        # 3. Dest parent writable
        # dest_base may be empty — default is parent of source_dir
        dest_check = dest_base or (os.path.dirname(source_dir.rstrip("/\\")) if source_dir else "")
        if not dest_check:
            checks.append({"name": "Destination", "ok": False, "message": "Cannot determine destination path"})
        else:
            # Check the first existing ancestor — dest itself may not exist yet
            check_path = dest_check
            while check_path and not os.path.exists(check_path):
                parent = os.path.dirname(check_path)
                if parent == check_path:
                    break
                check_path = parent
            if check_path and os.access(check_path, os.W_OK):
                checks.append({"name": "Destination", "ok": True, "message": dest_check})
            elif not check_path:
                checks.append({"name": "Destination", "ok": False, "message": f"No writable ancestor found for: {dest_check}"})
            else:
                checks.append({"name": "Destination", "ok": False,
                               "message": f"Not writable: {check_path}"})

    all_ok = all(c["ok"] for c in checks)
    return jsonify({"checks": checks, "all_ok": all_ok})


@app.route("/run", methods=["POST"])
def run():
    global _active_process, _is_running, _current_job, _stop_requested

    with _lock:
        if _is_running:
            return jsonify({"error": "Already processing"}), 409

    _stop_requested = False   # clear any previous stop flag before starting a new job

    data = request.json

    # --- Validate config before doing anything else ---
    _VALID_RATES  = {44100, 48000, 88200, 96000}
    _VALID_DEPTHS = {16, 24, 32, "32f"}
    src = (data.get("source_dir") or "").strip()
    if not src:
        return jsonify({"error": "Source path is required."}), 400
    try:
        workers = int(data.get("max_workers", 6))
        if not 1 <= workers <= 32:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Workers must be a number between 1 and 32."}), 400
    try:
        thresh = float(data.get("silence_thresh", -50.0))
        if thresh > 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Silence threshold must be 0 or negative (e.g. -50.0)."}), 400
    try:
        rate = int(data.get("target_sample_rate", 48000))
        if rate not in _VALID_RATES:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": f"Invalid sample rate. Must be one of: {sorted(_VALID_RATES)}."}), 400
    depth = data.get("bit_depth", 24)
    try:
        depth_key = int(depth)
    except (TypeError, ValueError):
        depth_key = str(depth)
    if depth_key not in _VALID_DEPTHS:
        return jsonify({"error": f"Invalid bit depth. Must be one of: 16, 24, 32, 32f."}), 400
    # --- End validation ---

    save_profile(data)

    source_dir, dest_base, mount_error = resolve_paths(data)
    if mount_error:
        return jsonify({"error": mount_error}), 500

    # Derive a human-readable job name from the source folder
    job_name = os.path.basename(source_dir.rstrip("/\\")) if source_dir else "Job"
    _current_job = {"name": job_name, "source": source_dir}

    config = {
        "source_dir": source_dir,
        "dest_base": dest_base,
        "log_dir": data.get("log_dir") or os.path.join(BASE_DIR, "logs"),
        "max_workers": int(data.get("max_workers", 6)),
        "dry_run": bool(data.get("dry_run", False)),
        "force_wav":  bool(data.get("force_wav", False)),
        "combine_lr":         bool(data.get("combine_lr", False)),
        "allow_unpaired_lr":  bool(data.get("allow_unpaired_lr", False)),
        "verbose": bool(data.get("verbose", False)),
        "target_sample_rate": int(data.get("target_sample_rate", 48000)),
        "bit_depth": data.get("bit_depth", 24),
        "silence_thresh": float(data.get("silence_thresh", -50.0)),
        "min_silence_len": 200,
        "min_non_silent_len": int(data.get("min_non_silent_len", 10)),
        "stability_wait_sec": int(data.get("stability_wait_sec", 60)),
        "watch_mode": bool(data.get("watch_mode", False)),
        "enable_script_remount": bool(data.get("enable_script_remount", False)),
        # Pass NAS info so process_audio.py can remount if needed
        "mount_type": data.get("nas_protocol") if data.get("nas_ip") else None,
        "nfs_server_path": (
            f"{data['nas_ip']}:{detect_nas_share(data.get('source_dir',''), data.get('nas_protocol','nfs'))[0]}"
            if data.get("nas_ip") and data.get("nas_protocol") == "nfs"
            else None
        ),
        "mount_point": None,
    }

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(config, tmp)
    tmp.close()

    while not _log_queue.empty():
        try:
            _log_queue.get_nowait()
        except queue.Empty:
            break

    def run_process():
        global _active_process, _is_running, _current_job
        converted = rejected = skipped = copied = merged = unpaired = 0
        job_name  = (_current_job or {}).get("name", "Job")
        try:
            python = os.path.join(BASE_DIR, "ghost-processing-venv", "bin", "python")
            if not os.path.exists(python):
                python = "python3"
            _active_process = subprocess.Popen(
                [python, "-u", PROCESS_SCRIPT, "--config", tmp.name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                # Detach from the parent process group so the job survives
                # Terminal close on the Mac.
                start_new_session=True,
            )
            _log_prefix_re = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \w+:\s*")
            for line in _active_process.stdout:
                msg  = line.rstrip()
                clean = _log_prefix_re.sub("", msg)
                ml   = clean.lower()
                if ml.startswith("done:") and " converted" in ml:
                    # Authoritative counts from process_audio.py batch_process return values
                    m = re.search(r'(\d+) converted', clean)
                    if m: converted = int(m.group(1))
                    m = re.search(r'(\d+) copied', clean)
                    copied = int(m.group(1)) if m else 0
                    m = re.search(r'(\d+) merged', clean)
                    merged = int(m.group(1)) if m else 0
                    m = re.search(r'(\d+) unpaired', clean)
                    unpaired = int(m.group(1)) if m else 0
                    m = re.search(r'(\d+) silent', clean)
                    rejected = int(m.group(1)) if m else 0
                    m = re.search(r'(\d+) skipped', clean)
                    skipped = int(m.group(1)) if m else 0
                elif ml.startswith("converted:") or ml.startswith("[dry run] would convert:"):
                    converted += 1
                elif ml.startswith("copied:") or ml.startswith("[dry run] would copy:"):
                    copied += 1
                elif ml.startswith("rejected:"):
                    rejected += 1
                elif ml.startswith("skipping:") or ml.startswith("already processed"):
                    skipped += 1
                entry = {"type": "log", "message": msg}
                _log_ring.append(entry)
                _log_queue.put(entry)
            _active_process.wait()
            rc = _active_process.returncode

            # Emit verified summary counts before done
            summary_entry = {
                "type":      "summary",
                "job_name":  job_name,
                "converted": converted,
                "copied":    copied,
                "merged":    merged,
                "unpaired":  unpaired,
                "rejected":  rejected,
                "skipped":   skipped,
            }
            _log_ring.append(summary_entry)
            _log_queue.put(summary_entry)

            # Persist last job for reconnecting clients
            if rc == 0:
                job_status = "done"
            elif _stop_requested:
                job_status = "stopped"
            else:
                job_status = "error"

            done_entry = {"type": "done", "returncode": rc, "job_name": job_name, "status": job_status, "dry_run": config.get("dry_run", False)}
            _log_ring.append(done_entry)
            _log_queue.put(done_entry)
            last_job_record = {
                "name":                job_name,
                "source":              (_current_job or {}).get("source", ""),
                "finished_at":         time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status":              job_status,
                "converted":           converted,
                "copied":              copied,
                "merged":              merged,
                "unpaired":            unpaired,
                "rejected":            rejected,
                "skipped":             skipped,
                "returncode":          rc,
                # Fields needed for post-run verification
                "source_dir":          config.get("source_dir", ""),
                "dest_base":           config.get("dest_base", ""),
                "target_sample_rate":  config.get("target_sample_rate", 48000),
                "bit_depth":           config.get("bit_depth", 24),
                "dry_run":             config.get("dry_run", False),
            }
            try:
                tmp_path = LAST_JOB_PATH + ".tmp"
                with open(tmp_path, "w") as f:
                    json.dump(last_job_record, f)
                os.replace(tmp_path, LAST_JOB_PATH)
            except Exception:
                pass
            # Append to run history (keep last 50 non-dry-run jobs)
            if not config.get("dry_run", False):
                try:
                    history = []
                    if os.path.exists(HISTORY_PATH):
                        with open(HISTORY_PATH) as f:
                            history = json.load(f)
                    history.append(last_job_record)
                    history = history[-50:]
                    tmp_h = HISTORY_PATH + ".tmp"
                    with open(tmp_h, "w") as f:
                        json.dump(history, f)
                    os.replace(tmp_h, HISTORY_PATH)
                except Exception:
                    pass
            # macOS Notification Center alert when job finishes
            if sys.platform == "darwin":
                if rc == 0:
                    status_word = "complete"
                elif _stop_requested:
                    status_word = "stopped"
                else:
                    status_word = "finished with errors"
                try:
                    subprocess.run(
                        ["osascript", "-e",
                         f'display notification "Conversion {status_word}" '
                         f'with title "Ghost Processing" sound name "Glass"'],
                        timeout=5, capture_output=True,
                    )
                except Exception:
                    pass
        except Exception as e:
            err_entry = {"type": "error", "message": str(e)}
            _log_ring.append(err_entry)
            _log_queue.put(err_entry)
            done_entry = {"type": "done", "returncode": 1}
            _log_ring.append(done_entry)
            _log_queue.put(done_entry)
        finally:
            _is_running = False
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    _is_running = True
    _log_ring.clear()
    threading.Thread(target=run_process, daemon=False).start()
    return jsonify({"status": "started"})


@app.route("/shares")
def list_shares():
    """
    Discover available NFS exports or SMB shares on a NAS.
    NFS: uses showmount -e (no credentials needed)
    SMB: uses smbutil view (credentials optional)
    """
    nas_ip   = request.args.get("ip", "").strip()
    protocol = request.args.get("protocol", "nfs")
    username = request.args.get("username", "").strip()
    password = request.args.get("password", "").strip()

    if not nas_ip:
        return jsonify({"shares": [], "error": "NAS IP required"})

    shares = []
    try:
        if protocol == "nfs":
            result = subprocess.run(
                ["showmount", "-e", nas_ip],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line.startswith("/"):
                    continue
                # Split on 2+ spaces or a tab — the separator between the export
                # path and the client list. This preserves spaces inside the path
                # itself (e.g. "/volume1/Backline Storage").
                parts = re.split(r'\s{2,}|\t', line)
                share = parts[0].strip()
                if share:
                    shares.append(share)
        else:  # smb
            # Always pass explicit credentials to prevent macOS from showing
            # its GUI auth dialog. If no credentials are provided, skip the
            # smbutil call entirely and tell the client to prompt for them.
            if not username:
                return jsonify({"shares": [], "hint": "Enter a username and password above, then test again to see available shares."})
            if password:
                url = f"smb://{username}:{password}@{nas_ip}"
            else:
                url = f"smb://{username}@{nas_ip}"
            result = subprocess.run(
                ["smbutil", "view", url],
                capture_output=True, text=True, timeout=15,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("Share") or line.startswith("-"):
                    continue
                # Match share name (may contain spaces) followed by 2+ spaces
                # and the type column ("Disk", "Pipe", etc.).
                m = re.match(r'^(.+?)\s{2,}(Disk|Pipe|Print)\b', line)
                if m and m.group(2) == "Disk":
                    share = m.group(1).strip()
                    if not share.endswith("$"):
                        shares.append(share)
    except subprocess.TimeoutExpired:
        return jsonify({"shares": [], "error": "Timed out"})
    except Exception as e:
        return jsonify({"shares": [], "error": str(e)})

    return jsonify({"shares": shares})


@app.route("/docker-mappings")
def docker_mappings():
    """Parse docker-compose.yml and return volume mappings (host → container)."""
    compose_path = os.path.join(BASE_DIR, "docker-compose.yml")
    if not os.path.exists(compose_path):
        return jsonify({"mappings": []})
    try:
        with open(compose_path) as f:
            compose = yaml.safe_load(f)
        mappings = []
        for svc in (compose.get("services") or {}).values():
            for vol in (svc.get("volumes") or []):
                if isinstance(vol, str) and ":" in vol:
                    parts = vol.split(":")
                    host, container = parts[0], parts[1]
                    # Skip relative paths like ./logs
                    if not host.startswith(".") and not host.startswith("~"):
                        mappings.append({"host": host, "container": container})
        return jsonify({"mappings": mappings})
    except Exception as e:
        return jsonify({"mappings": [], "error": str(e)})


_BROWSE_BLOCKED = {
    "/etc", "/private/etc", "/System", "/usr", "/bin", "/sbin",
    "/Library/Keychains", "/private/var", "/root",
}


@app.route("/browse")
def browse():
    path = request.args.get("path", "~")
    path = os.path.expanduser(path)
    path = os.path.normpath(path)
    # Block sensitive system directories — prevents network-accessible Docker
    # instances from being used to traverse the host filesystem.
    for blocked in _BROWSE_BLOCKED:
        if path == blocked or path.startswith(blocked + "/"):
            return jsonify({"error": "Access denied"}), 403
    try:
        entries = []
        with os.scandir(path) as it:
            for entry in sorted(it, key=lambda e: e.name.lower()):
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=True):
                        entries.append(entry.name)
                except OSError:
                    pass
        parent = os.path.dirname(path) if path != "/" else None
        return jsonify({"path": path, "entries": entries, "parent": parent})
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404


@app.route("/stop", methods=["POST"])
def stop():
    global _active_process, _stop_requested
    if _active_process and _active_process.poll() is None:
        _stop_requested = True
        try:
            pgid = os.getpgid(_active_process.pid)
            os.killpg(pgid, signal.SIGTERM)
            # Workers may be blocked on SMB/NFS kernel I/O and won't respond to
            # SIGTERM until the syscall returns. Escalate to SIGKILL after 5s.
            def _force_kill(pgid):
                time.sleep(5)
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            threading.Thread(target=_force_kill, args=(pgid,), daemon=True).start()
        except ProcessLookupError:
            pass
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not running"})


@app.route("/status")
def status():
    last_job = None
    if os.path.exists(LAST_JOB_PATH):
        try:
            with open(LAST_JOB_PATH) as f:
                last_job = json.load(f)
        except Exception:
            pass
    return jsonify({
        "running":      _is_running,
        "current_job":  _current_job,
        "last_job":     last_job,
    })


@app.route("/history", methods=["GET"])
def history():
    """Return run history (last 50 non-dry-run jobs)."""
    if not os.path.exists(HISTORY_PATH):
        return jsonify([])
    try:
        with open(HISTORY_PATH) as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify([])


@app.route("/history", methods=["DELETE"])
def clear_history():
    """Delete run history."""
    try:
        if os.path.exists(HISTORY_PATH):
            os.unlink(HISTORY_PATH)
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/verify", methods=["POST"])
def verify():
    """
    Run verify_audio.py --json against the last completed (non-dry-run) job.
    Returns the structured JSON output from the verifier.
    """
    if not os.path.exists(LAST_JOB_PATH):
        return jsonify({"error": "No completed job found"}), 404
    try:
        with open(LAST_JOB_PATH) as f:
            last_job = json.load(f)
    except Exception:
        return jsonify({"error": "Could not read last job record"}), 500

    if last_job.get("dry_run"):
        return jsonify({"error": "Last job was a dry run — nothing to verify"}), 400

    source_dir = last_job.get("source_dir", "")
    dest_base  = last_job.get("dest_base", "")
    rate       = last_job.get("target_sample_rate", 48000)
    depth      = last_job.get("bit_depth", 24)

    if not source_dir or not dest_base:
        return jsonify({"error": "Last job record is missing source/dest paths — re-run the job to enable verification"}), 400

    # Build a temp config YAML for verify_audio.py
    verify_config = {
        "source_dir":          source_dir,
        "dest_base":           dest_base,
        "target_sample_rate":  rate,
        "bit_depth":           depth,
    }
    try:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(verify_config, tmp)
        tmp.close()

        python = os.path.join(BASE_DIR, "ghost-processing-venv", "bin", "python")
        if not os.path.exists(python):
            python = "python3"

        result = subprocess.run(
            [python, VERIFY_SCRIPT, "--config", tmp.name, "--json"],
            capture_output=True, text=True, timeout=300,
        )
        os.unlink(tmp.name)

        if result.returncode != 0 and not result.stdout.strip():
            return jsonify({"error": "Verifier failed: " + (result.stderr or "unknown error")}), 500

        data = json.loads(result.stdout)
        data["job_name"]    = last_job.get("name", "")
        data["finished_at"] = last_job.get("finished_at", "")
        return jsonify(data)
    except json.JSONDecodeError:
        return jsonify({"error": "Verifier output could not be parsed"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Verification timed out"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stream")
def stream():
    def generate():
        # Replay buffered log lines to reconnecting clients so they catch up
        # on everything that happened while the browser was closed/sleeping.
        for entry in list(_log_ring):
            yield f"data: {json.dumps(entry)}\n\n"
            if entry.get("type") == "done":
                return   # job already finished — nothing more to stream

        # Stream live lines
        while True:
            try:
                msg = _log_queue.get(timeout=0.5)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") == "done":
                    break
            except queue.Empty:
                if not _is_running:
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Version & Update
# ---------------------------------------------------------------------------

@app.route("/version")
def version():
    local  = _version_cache["local"] or "unknown"
    remote = _version_cache.get("remote")
    up_to_date = _version_cache.get("up_to_date", True)
    return jsonify({
        "local_sha":  local,
        "remote_sha": remote,
        "up_to_date": up_to_date,
        "is_docker":  _is_docker,
    })


@app.route("/update", methods=["POST"])
def update():
    if _is_running:
        return jsonify({"error": "Cannot update while a job is running"}), 400
    if _is_docker:
        return jsonify({
            "docker":  True,
            "command": "git fetch origin && git reset --hard origin/main && git rev-parse --short HEAD > VERSION && sudo docker compose up --build -d",
        })
    try:
        subprocess.run(["git", "fetch", "origin"],              cwd=BASE_DIR, capture_output=True, timeout=30, check=True)
        subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=BASE_DIR, capture_output=True, timeout=30, check=True)
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Git update failed: {e}"}), 500
    # Restart server in-place — browser reconnects via SSE auto-reconnect
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    host = os.getenv("HOST", "0.0.0.0")
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    from waitress import serve
    serve(app, host=host, port=port, threads=8)
