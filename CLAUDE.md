# Ghost Processing — AI Context & Handoff

> This file is read automatically by Claude Code at the start of every session.
> It contains everything needed to continue development without prior context.

---

## What This Project Is

A personal audio processing pipeline for a music studio. Converts stems (WAV/AIFF) from Pro Tools sessions at 96kHz → 48kHz (or other target rates) using SoX with shaped dithering. Detects and rejects silent/empty files before conversion. Runs as a local web app — double-click `start.command`, browser opens, fill in paths, click Run.

**Owner:** Scottie — audio engineer / producer. Non-technical user. The app must be double-click simple.

**Environments:**
- **Mac + NAS (primary):** Mac runs the app; files live on a Synology or unRAID NAS over NFS or SMB. The app mounts the share automatically.
- **Mac + local:** Files on Mac's internal or external drive.
- **Docker on NAS:** Container runs on the NAS itself; web UI accessed from any device on the network.

---

## Current State (as of 2026-03-23)

The app is **feature-complete and working** for its primary use case. All major systems are built and committed. The app has not yet been pushed to GitHub (no remote configured).

### What works end-to-end:
- Web UI with NAS / Local / Docker mode selector
- NAS: NFS + SMB connection test, share chips, auto-mount, folder browser
- Local: folder browser starting at `~`
- Docker: path resolution from docker-compose.yml volume mappings
- Sample rate dropdown (44.1 / 48 / 88.2 / 96kHz) and bit depth dropdown (16 / 24 / 32 / 32f)
- SoX-based conversion with shaped dither; pydub silence detection + rejection
- Parallel processing via ProcessPoolExecutor (configurable workers)
- Resume from interrupted run via `progress.json` content hash
- SSE live log streaming with 500-line ring buffer (reconnects after sleep/tab close)
- Job naming from source directory basename
- Completion banner (✓/✗, job name, converted/rejected/skipped counts)
- `last_job.json` — last job result shown in status bar on fresh page load
- macOS Notification Center alert via osascript on completion
- Process detachment (`start_new_session=True`) — conversion survives Terminal close
- `start.command` detects running job on reopen, reopens browser instead of killing server
- Waitress WSGI server (clean terminal, no Flask dev server noise)
- `profile.json` settings persistence across sessions
- Atomic JSON writes (tempfile + fsync + rename)
- Per-run timestamped log files in `logs/`
- Auto-verification after batch completes

### What has NOT been tested end-to-end yet:
- Full NFS conversion run (connection works, mount works, conversion not yet tested through UI)
- Full SMB conversion run
- Local mode conversion run
- All sample rate and bit depth combinations
- Silence detection calibration
- AIFF source files
- Filenames with spaces and special characters
- Deep folder nesting
- Resume from interrupted run
- Docker end-to-end
- Watch mode

---

## Immediate Next Steps (in order)

### 1. Push to GitHub
`gh` CLI is not installed. Options:
- `brew install gh && gh auth login && gh repo create ghost-processing --private --source=. --push`
- Or: create repo at github.com/new (no README init), then `git remote add origin <url> && git push -u origin main`

### 2. Fix known bugs (see BACKLOG.md 🔴 section)
- **Hardcoded `-48` in dest directory name and verifier** — filename generation in `process_file()` is already correct. Bugs remain in: `main()` line 500 (dest dir named `{source_name}-48`) and `run_verification()` line 404 (checks for `{base}-48` in filenames, so misses correctly-named output files). Fix both to use the same `rate_suffix` logic.
- **Stop button doesn't kill SoX workers** — `/stop` only terminates the Python wrapper; SoX processes run in a separate process group (`start_new_session=True`) and keep running. Fix: `os.killpg`.
- **BWF metadata stripped by SoX** — Pro Tools BEXT chunks (timecode, originator) are not preserved. Fix: `bwfmetaedit` post-step.
- **`executor.shutdown(wait=False)`** in `batch_process()` — change to `wait=True` to prevent orphaned workers.
- **`verify_audio.py` rejects.json crash** — add type guard before `e["path"]` access.
- **`/browse` path traversal** — add root-jail validation.

### 3. Full test run
Run a real conversion and verify output files, names, sample rates, bit depths are correct. See BACKLOG.md 🟡 section for full test checklist.

---

## Architecture

```
ghost-processing/
├── app.py                  # Flask web server + SSE + all API endpoints
├── process_audio.py        # Conversion engine (SoX subprocess + pydub silence detection)
├── verify_audio.py         # Post-run verifier (checks all files accounted for)
├── templates/index.html    # Single-page web UI (vanilla JS, SSE client)
├── start.command           # Mac launcher (double-click) — handles all setup
├── Dockerfile              # For Docker/NAS deployment
├── docker-compose.yml      # Volume mounts for Synology/unRAID
├── requirements.txt        # Python deps (waitress, flask, pydub, psutil, etc.)
├── config.local.yaml       # Mac config — gitignored (personal paths)
├── config.docker.yaml      # Docker config — gitignored
├── profile.json            # UI settings persistence — gitignored
├── last_job.json           # Last job result — gitignored
├── logs/                   # Run logs — gitignored
└── BACKLOG.md              # Project management, bugs, test checklist, decisions log
```

### app.py key globals
```python
_active_process = None          # subprocess.Popen for the running job
_log_queue      = queue.Queue() # inter-thread log line passing
_log_ring       = collections.deque(maxlen=500)  # SSE replay buffer
_is_running     = False
_lock           = threading.Lock()
_current_job    = None          # {"name": str, "source": str}
```

### app.py key endpoints
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Serves index.html |
| `/run` | POST | Starts conversion job |
| `/stop` | POST | Kills running job |
| `/stream` | GET | SSE log stream (replays ring buffer first) |
| `/status` | GET | `{"running": bool, "current_job": dict, "last_job": dict}` |
| `/browse` | GET | Server-side directory browser |
| `/profile` | GET/POST | Load/save UI settings |
| `/test-connection` | POST | NFS showmount / SMB smbutil view |
| `/mount` | POST | sudo mount_nfs or osascript SMB mount |
| `/docker-mappings` | GET | Reads docker-compose.yml volume mappings |

### SSE event types (emitted by run_process thread)
| Type | Payload | Purpose |
|------|---------|---------|
| `log` | `{"text": str, "level": str}` | Log line for output panel |
| `summary` | `{"job_name": str, "converted": N, "rejected": N, "skipped": N}` | Server-verified counts before done |
| `done` | `{"returncode": int, "job_name": str}` | Job finished |

### NFS mounting flow
1. `start.command` configures passwordless sudo for `/sbin/mount_nfs` (one-time)
2. `/mount` endpoint: probe sudo with `sudo -n mkdir` — if exit 0, use `sudo mount_nfs`; otherwise fall back to osascript
3. `detect_nas_share()` auto-derives NFS export root from source path

### Conversion pipeline (process_audio.py)
1. `soxi -r` — check source sample rate; skip if already at target
2. pydub silence detection — reject if silent beyond threshold
3. SoX subprocess: `sox input [-b N] [-e floating-point] output rate -v SR [dither -s]`
4. Atomic write to `progress.json` on each success
5. Auto-verification at end of batch

---

## Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| SoX + shaped dither | Better quality than pydub resampling; `rate -v` + `dither -s` reduces quantization noise |
| No normalization | Stems must preserve original levels for mixing |
| Waitress over Flask dev server | No terminal noise, no ANSI garbage, production-grade |
| `start_new_session=True` | Conversion subprocess detaches from Terminal's process group |
| `deque(maxlen=500)` ring buffer | SSE replay for reconnecting clients without reading log files |
| `last_job.json` separate from `profile.json` | Profile = settings (stable); last_job = ephemeral state (overwrites each run) |
| Job name from `os.path.basename(source_dir)` | Natural, automatic, meaningful in notifications |
| Destination defaults to `dirname(source_dir)` | Most natural workflow — output lands next to input |
| `sudo mount_nfs` primary, osascript fallback | sudo is silent and stable; osascript avoids sudo dependency but can show macOS dialogs |
| Fork start method for multiprocessing | Avoids lock init issues on macOS with spawn |
| Docker platform `linux/amd64` | Synology NAS is Intel x86_64 |

---

## Running Locally

```bash
# Launch (handles all setup)
./start.command

# Or manually:
source ghost-processing-venv/bin/activate
python app.py
# Open http://localhost:5001

# Command-line conversion (no UI)
python process_audio.py --config config.local.yaml

# Verify a completed run
python verify_audio.py --config config.local.yaml
```

---

## Git State

- Branch: `main`
- Last commit: `be45819` — "Add NAS/Local/Docker modes, overnight reliability, job completion system"
- Remote: **none configured** — not yet on GitHub
- All meaningful changes are committed; `.DS_Store` is the only unstaged file (gitignored)
