# Ghost Processing — AI Context & Handoff

> This file is read automatically by Claude Code at the start of every session.
> It contains everything needed to continue development without prior context.

---

## What This Project Is

A personal audio processing pipeline for a music studio. Converts stems (WAV/AIFF/AIF) from Pro Tools sessions to a target sample rate (default 48kHz/24-bit) using SoX with shaped dithering. Detects and rejects silent/empty files before conversion. Runs as a local web app — double-click `start.command`, browser opens, fill in paths, click Run.

**Owner:** Scottie — audio engineer / producer. Non-technical user. The app must be double-click simple.

**Environments:**
- **Mac + NAS (primary):** Mac runs the app; files live on a Synology or unRAID NAS over NFS or SMB. The app mounts the share automatically.
- **Mac + local:** Files on Mac's internal or external drive.
- **Docker on NAS:** Container runs on the NAS itself; web UI accessed from any device on the network.

---

## Current State (as of 2026-03-23)

The app is **feature-complete and working**. Core pipeline verified by real test runs. All known code bugs fixed. Remote configured at `origin/main`.

### What works end-to-end (verified):
- Web UI with NAS / Local / Docker mode selector
- NAS: NFS + SMB connection test, share chips, auto-mount, folder browser
- Local: folder browser starting at `~`
- Docker: path resolution from docker-compose.yml volume mappings
- Sample rate dropdown (44.1 / 48 / 88.2 / 96kHz) and bit depth dropdown (16 / 24 / 32 / 32f)
- SoX-based conversion with shaped dither; pydub silence detection + rejection
- `.wav`, `.aif`, `.aiff` source files all supported
- Parallel processing via ProcessPoolExecutor (configurable workers)
- Resume from interrupted run via `progress.json` content hash — partial files (from Stop) are not in progress.json and get re-converted automatically
- SSE live log streaming with 500-line ring buffer (reconnects after sleep/tab close)
- Phase progress bar — "Detecting silence X/N" → "Converting X/N" — accurate, tracks completions
- Job naming from source directory basename
- Completion banner (✓/◼/✗, job name, converted / copied / rejected / skipped counts) + "New Run" reset button — distinguishes Done / Stopped / Error; "copied" shown only when non-zero
- `last_job.json` — last job result shown in status bar on fresh page load; status is `"done"` / `"stopped"` / `"error"` (not just pass/fail)
- macOS Notification Center alert via osascript on completion
- Process detachment (`start_new_session=True`) — conversion survives Terminal close
- Stop button kills entire process group (`os.killpg`) — SoX workers actually stop
- `start.command` detects running job on reopen, reopens browser instead of killing server
- Passwordless sudo for NFS mount — one-time setup only, fixed re-prompt bug
- Waitress WSGI server (clean terminal, no Flask dev server noise)
- `profile.json` settings persistence across sessions
- Atomic JSON writes (tempfile + fsync + rename) for `progress.json`
- Per-run timestamped log files in `logs/` AND copied into dest folder alongside `progress.json` and `rejects.json`
- Auto-verification after batch completes
- `/browse` endpoint blocks sensitive system directories (path traversal protection)
- Output naming: `{source_name}-{rate}k-{depth}b/` e.g. `Boston-48k-24b/`, files named `stem-48k-24b.wav`

### Verified by test run:
- Local mode: WAV + AIF + AIFF, spaces in filenames, already-at-target-rate skip, silence rejection
- All sample rates: 44.1kHz, 48kHz, 88.2kHz, 96kHz — correct output naming and resampling
- All bit depths: 16-bit, 24-bit, 32-bit, 32f — correct encoding confirmed with soxi
- 32f with AIF/AIFF source → output as .wav (AIFF can't encode float; fixed)
- Already-at-target files copied (not skipped) to output; counted separately as "copied" in UI and progress.json
- Resume from interrupted run — 3 stop/resume cycles confirmed; progress.json correctly gates skips; partial dest files re-converted by design
- NFS end-to-end — NAS mode, real session folder, 48kHz/24-bit, verification clean
- `.aif` (single-f) files correctly detected and converted
- Ableton `.asd` sidecar files correctly ignored
- Silence detection: truly silent file rejected; sparse/noisy content correctly kept

### Not yet tested:
- Deep folder nesting
- Dry run mode
- SMB end-to-end conversion run
- NFS/SMB network drop mid-run
- Docker end-to-end
- Watch mode

---

## Immediate Next Steps (in order)

### 1. Remaining test checklist
See BACKLOG.md 🟡 section. Priority: 88.2/96kHz outputs, 32bit/32f outputs, then NFS real run.

### 2. Remaining 🔴 bugs
- **BWF metadata stripped by SoX** — BEXT chunks (timecode, originator) not preserved. Fix: `bwfmetaedit` post-step.
- **Silence calibration** — needs real sparse/SFX stems to calibrate threshold. Analyzer feature (🟢) will help.

### 3. Docker compose gaps (before Docker test)
- Add dest volume to docker-compose.yml
- Mount config.docker.yaml into container

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
├── running_job.pid         # PID file for orphan detection (planned) — gitignored
├── logs/                   # Central run logs — gitignored
└── BACKLOG.md              # Project management, bugs, test checklist, decisions log
```

### app.py key globals
```python
_active_process  = None          # subprocess.Popen for the running job
_log_queue       = queue.Queue() # inter-thread log line passing
_log_ring        = collections.deque(maxlen=500)  # SSE replay buffer
_is_running      = False
_stop_requested  = False         # True when user clicked Stop; distinguishes user-stop from crash
_lock            = threading.Lock()
_current_job     = None          # {"name": str, "source": str} set when a job starts
```

### app.py key endpoints
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Serves index.html |
| `/run` | POST | Starts conversion job |
| `/stop` | POST | Kills running job (entire process group via os.killpg) |
| `/stream` | GET | SSE log stream (replays ring buffer first) |
| `/status` | GET | `{"running": bool, "current_job": dict, "last_job": dict}` |
| `/browse` | GET | Server-side directory browser (blocks sensitive system paths) |
| `/profile` | GET | Load UI settings |
| `/connect` | POST | NFS showmount / SMB TCP test + optional mount |
| `/disconnect` | POST | Unmount NAS share |
| `/shares` | GET | List NFS exports or SMB shares |
| `/mount` | POST | sudo mount_nfs or osascript SMB mount |
| `/docker-mappings` | GET | Reads docker-compose.yml volume mappings |

### SSE event types (emitted by run_process thread)
| Type | Payload | Purpose |
|------|---------|---------|
| `log` | `{"message": str}` | Log line for output panel + phase bar parsing |
| `summary` | `{"job_name": str, "converted": N, "copied": N, "rejected": N, "skipped": N}` | Server-verified counts before done |
| `done` | `{"returncode": int, "job_name": str, "status": "done"\|"stopped"\|"error"}` | Job finished |
| `heartbeat` | — | Keep SSE connection alive |

### NFS mounting flow
1. `start.command` configures passwordless sudo for `/sbin/mount_nfs` (one-time, checks via stderr grep)
2. `/connect` endpoint: TCP reachability check, then optional mount via `mount_nas()`
3. `mount_nas()`: probe `sudo -n mkdir` — if passwordless, use `sudo mount_nfs`; otherwise fall back to osascript
4. `detect_nas_share()` auto-derives NFS export root from source path (Synology `volumeN` convention aware)

### Conversion pipeline (process_audio.py)
Per file in `process_file()`:
1. Zero-byte check — reject immediately
2. Resume check — skip if in `progress.json` with matching source MD5 hash and dest file exists
3. `soxi -r` — skip if already at target sample rate
4. pydub silence detection — reject if entirely silent
5. Log `"Analyzing: {rel_path}"` — signals silence check passed; drives phase bar in UI
6. SoX subprocess: `sox input [-b N] [-e floating-point] output rate -v SR [dither -s]`
7. Atomic write to `progress.json` on success
8. Auto-verification at end of batch; run log also written to dest folder

---

## Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| SoX + shaped dither | Better quality than pydub resampling; `rate -v` + `dither -s` reduces quantization noise |
| No normalization | Stems must preserve original levels for mixing |
| Waitress over Flask dev server | No terminal noise, no ANSI garbage, production-grade |
| `start_new_session=True` | Conversion subprocess detaches from Terminal's process group |
| `os.killpg` for Stop | Kills entire process group including SoX workers, not just Python wrapper |
| `deque(maxlen=500)` ring buffer | SSE replay for reconnecting clients without reading log files |
| `last_job.json` separate from `profile.json` | Profile = settings (stable); last_job = ephemeral state (overwrites each run) |
| Job name from `os.path.basename(source_dir)` | Natural, automatic, meaningful in notifications |
| Destination defaults to `dirname(source_dir)` | Most natural workflow — output lands next to input |
| Output naming `{name}-{rate}k-{depth}b` | Suffix includes both rate and bit depth (e.g. 48000/24 → `48k-24b`, 44100/32f → `44k-32f`) so different conversions never collide and skip logic is unambiguous |
| Run log copied to dest folder | Each conversion folder is self-contained — open it later, full record is there |
| `sudo mount_nfs` primary, osascript fallback | sudo is silent and stable; osascript avoids sudo dependency but can show macOS dialogs |
| Sudoers check via stderr grep | `sudo -n mount_nfs` exit code is unreliable; checking for "password is required" in stderr is accurate |
| Fork start method for multiprocessing | Avoids lock init issues on macOS with spawn |
| Docker platform `linux/amd64` | Synology NAS is Intel x86_64 |
| "Analyzing:" log after silence check | Phase bar tracks completions, not worker pickups — bar fills accurately |
| `_stop_requested` flag for Stop status | `/stop` sets flag before killpg; `run_process` reads it after wait() to write `"stopped"` vs `"error"` — can't use returncode alone since both are non-zero |
| `output_suffix(rate, depth)` helper | Dest dirs and filenames include both rate and depth e.g. `48k-24b`; prevents collisions across bit depths and makes skip logic unambiguous |
| 32f AIF/AIFF → .wav output | AIFF can't encode float; SoX silently falls back to 32i. Force .wav for 32f+AIF sources. Same ext swap mirrored in both verifiers. |
| Checkboxes as pill toggles | All `input[type="checkbox"]` styled as dark-surface pill toggles with amber knob — default white checkbox breaks dark theme |

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
- Remote: `origin/main` configured
- Working tree: clean
- All known bugs fixed and committed as of 2026-03-23
