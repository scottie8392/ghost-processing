"""
Ghost Processing — web UI

Runs a local web server for launching audio conversion jobs.
Access at http://localhost:5001

Usage:
    python app.py
    (or double-click start.command on Mac)
"""

import json
import os
import queue
import subprocess
import tempfile
import threading

import yaml
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESS_SCRIPT = os.path.join(BASE_DIR, "process_audio.py")
PROFILE_PATH = os.path.join(BASE_DIR, "profile.json")

DEFAULT_PROFILE = {
    "source_dir": "",
    "dest_base": "",
    "log_dir": os.path.join(BASE_DIR, "logs"),
    "max_workers": 6,
    "dry_run": False,
    "verbose": False,
    "target_sample_rate": 48000,
    "silence_thresh": -50.0,
    "min_silence_len": 200,
    "min_non_silent_len": 10,
    "watch_mode": False,
    "stability_wait_sec": 60,
    "mount_type": None,
    "enable_script_remount": False,
    "nfs_server_path": None,
    "mount_point": None,
    "saved_sources": [],  # Source dir history
}

# Runtime state
_active_process = None
_log_queue = queue.Queue()
_is_running = False
_lock = threading.Lock()


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
    # Update only the fields that were submitted
    for key in data:
        if key in DEFAULT_PROFILE:
            profile[key] = data[key]
    # Add source dir to history
    src = data.get("source_dir", "").strip()
    if src and src not in profile.get("saved_sources", []):
        profile.setdefault("saved_sources", []).insert(0, src)
        profile["saved_sources"] = profile["saved_sources"][:10]
    with open(PROFILE_PATH, "w") as f:
        json.dump(profile, f, indent=2)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", profile=load_profile())


@app.route("/profile", methods=["GET"])
def get_profile():
    return jsonify(load_profile())


@app.route("/run", methods=["POST"])
def run():
    global _active_process, _is_running

    with _lock:
        if _is_running:
            return jsonify({"error": "Already processing"}), 409

    data = request.json
    save_profile(data)

    config = {
        "source_dir": data["source_dir"],
        "dest_base": data["dest_base"],
        "log_dir": data.get("log_dir") or os.path.join(BASE_DIR, "logs"),
        "max_workers": int(data.get("max_workers", 6)),
        "dry_run": bool(data.get("dry_run", False)),
        "verbose": bool(data.get("verbose", False)),
        "target_sample_rate": int(data.get("target_sample_rate", 48000)),
        "silence_thresh": float(data.get("silence_thresh", -50.0)),
        "min_silence_len": int(data.get("min_silence_len", 200)),
        "min_non_silent_len": int(data.get("min_non_silent_len", 10)),
        "stability_wait_sec": int(data.get("stability_wait_sec", 60)),
        "watch_mode": bool(data.get("watch_mode", False)),
        "mount_type": data.get("mount_type") or None,
        "enable_script_remount": bool(data.get("enable_script_remount", False)),
        "nfs_server_path": data.get("nfs_server_path") or None,
        "mount_point": data.get("mount_point") or None,
    }

    # Write a temp config file for process_audio.py
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(config, tmp)
    tmp.close()

    # Clear the log queue
    while not _log_queue.empty():
        try:
            _log_queue.get_nowait()
        except queue.Empty:
            break

    def run_process():
        global _active_process, _is_running
        try:
            python = os.path.join(BASE_DIR, "ghost-processing-venv", "bin", "python")
            if not os.path.exists(python):
                python = "python3"

            _active_process = subprocess.Popen(
                [python, PROCESS_SCRIPT, "--config", tmp.name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in _active_process.stdout:
                _log_queue.put({"type": "log", "message": line.rstrip()})
            _active_process.wait()
            _log_queue.put({"type": "done", "returncode": _active_process.returncode})
        except Exception as e:
            _log_queue.put({"type": "error", "message": str(e)})
            _log_queue.put({"type": "done", "returncode": 1})
        finally:
            _is_running = False
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    _is_running = True
    threading.Thread(target=run_process, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/stop", methods=["POST"])
def stop():
    global _active_process
    if _active_process and _active_process.poll() is None:
        _active_process.terminate()
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not running"})


@app.route("/status")
def status():
    return jsonify({"running": _is_running})


@app.route("/stream")
def stream():
    def generate():
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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"Ghost Processing running at http://localhost:{port}")
    print("Press Ctrl+C to stop.\n")
    app.run(host=host, port=port, debug=False, threaded=True)
