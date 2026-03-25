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

## Current State (as of 2026-03-25, Sprint 1.1 + 1.2 + Sprint 2 complete)

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
- Completion banner (✓/◼/✗, converted / copied / rejected / skipped counts) — distinguishes Done / Stopped / Error; "copied" shown only when non-zero
- Dry run mode — amber banner with ◎ icon; no files, dirs, or logs written; report-only; never persisted to profile.json
- Log display — Python timestamp prefix stripped; colour-coded; uniform format per file: `Checking: stem.aif (96k/24b)`, `Copied: stem.aif (48k/24b, no conversion needed, level -0.0dBFS)`, `Converted: stem.aif (96k/24b → 48k/24b, level -0.0dBFS)`, `Rejected: stem.aif (96k/24b, silent, level -∞dBFS)`
- Per-file **peak short-time RMS** (`level`) shown on all result lines — uses same 10ms window as silence detection; directly comparable to silence threshold
- File Review panel — live-updating lists of rejected (silent) and skipped (already in dest) files with counts
- `last_job.json` — last job result shown as small status chip on fresh page load; status is `"done"` / `"stopped"` / `"error"`
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
- Local mode: WAV + AIF + AIFF, spaces in filenames, silence rejection, resume, deep folder nesting
- All sample rates: 44.1kHz, 48kHz, 88.2kHz, 96kHz — correct output naming and resampling
- All bit depths: 16-bit, 24-bit, 32-bit, 32f — correct encoding confirmed with soxi
- 32f with AIF/AIFF source → output as .wav (AIFF can't encode float; fixed)
- Already-at-target files copied (not skipped) to output; counted separately as "copied" in UI and progress.json
- Already-processed files skipped (not counted as silent) — correctly shown as "N skipped" in banner and File Review
- Resume from interrupted run — 3 stop/resume cycles confirmed; progress.json correctly gates skips; partial dest files re-converted by design
- NFS end-to-end — NAS mode, real session folder, 48kHz/24-bit, verification clean
- SMB end-to-end — confirmed working; share name vs mount point mismatch fixed; local dest path routing fixed; SIGKILL escalation on Stop
- `.aif` (single-f) files correctly detected and converted
- Ableton `.asd` sidecar files correctly ignored
- Silence detection: truly silent file rejected; sparse/noisy content correctly kept — measurement unified (Sprint 1.1): both display and detection now use peak short-time RMS
- Dry run mode: no files written, all counts correct, amber banner, format matches real run
- Force WAV output toggle — forces all output to .wav regardless of source format
- Silence detection now runs on ALL files before copy or convert — silent files never reach destination regardless of format
- Progress bar tracks live file completions (liveDoneCount); label "Processing X/N"
- Log rotation — keeps 10 most recent run_*.log files in logs/; oldest pruned on each run
- Stop button SIGKILL escalation — SIGTERM + SIGKILL after 5s for workers blocked on SMB/NFS I/O
- NAS mode dest path: local absolute paths (e.g. /Users/scottie/Desktop) used as-is; not mangled through NAS share detection
- SMB share name vs mount point: uses nasShareRoot (actual share name) not the local folder name which macOS may suffix with -1/-2

### Not yet tested:
- NFS/SMB network drop mid-run
- Watch mode

### Verified by Sprint 2:
- Docker end-to-end — DS1821+ (AMD Ryzen V1500B, 4GB RAM), 3 workers, real session folder, 48kHz/24-bit. Verification passed — all files accounted for.
- Docker dry run — correct counts, amber banner, no files written
- docker-compose.yml volume mappings — source + show archive shares with spaces in path
- Docker path mapping display — docker-compose.yml baked into image via Dockerfile; `/docker-mappings` endpoint resolves container → host paths correctly
- save_profile() on Docker bind-mounted files — os.replace() EBUSY fallback to direct write
- Recommended workers for DS1821+ (4GB RAM): **3** — 6 caused OOM kills

---

## Immediate Next Steps

### Sprint 3 — see BACKLOG.md for candidates
All 🔴 bugs fixed. Focus shifts to workflow features and quality-of-life improvements.

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
1. Zero-byte check — reject immediately (return None)
2. Resume check — if in `progress.json` with matching source MD5 hash and dest file exists → return `"skipped"`
3. `soxi -r` + `soxi -b` — log `"Checking: {rel_path}  ({src_fmt})"`
4. `check_silence()` — computes peak short-time RMS (max chunk `.dBFS` using `min_non_silent_len` ms windows); reject if entirely silent (return None); log includes `level XdBFS`
5. Already at target rate+depth → `shutil.copy2` (return `"copied"`)
6. SoX subprocess: `sox input [-b N] [-e floating-point] output rate -v SR [dither -s]`
7. Atomic write to `progress.json` on success; return file path (truthy → converted)
8. Auto-verification at end of batch; run log also written to dest folder

`batch_process()` counts returns: `"copied"` → copied, `"skipped"` → skipped, truthy path → converted, None → rejected. Emits `"Done: N converted[, N copied][, N silent][, N skipped] in Xs"` — this line is parsed by app.py as the authoritative count for the SSE summary event and completion banner.

**No tqdm** — was removed because tqdm's `\r` progress bar writes to stderr (merged into stdout pipe) were corrupting parallel worker log lines via Python's universal-newlines `\r`-as-newline handling. UI has its own phase bar.

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
| Peak short-time RMS for silence display | `check_silence()` chunks audio into `min_non_silent_len` ms windows, takes `max(chunk.dBFS)` — same metric as `detect_nonsilent` uses internally. `level` value in log is directly comparable to silence threshold. Prevents false-positive on single transient peaks (old `max_dBFS`) while correctly keeping sparse content like a single rimshot. |
| `min_silence_len` hardcoded, removed from UI | pydub gap-bridging parameter is irrelevant for binary keep/reject decisions. Hardcoded to 200ms; only `silence_thresh` and `min_non_silent_len` exposed in Advanced panel. |
| BWF/BEXT preserved via pure Python RIFF manipulation | SoX strips BEXT chunks. `bwfmetaedit` CLI `--out-core`/`--in-core` round-trip was unreliable. Pure Python `_read_bext_chunk`/`_write_bext_chunk` reads raw BEXT bytes from source WAV RIFF structure and injects into dest — no external tools, guaranteed correct. TimeReference sample count preserved exactly; wall-clock shift on sample rate change is expected behavior. |
| `_stop_requested` flag for Stop status | `/stop` sets flag before killpg; `run_process` reads it after wait() to write `"stopped"` vs `"error"` — can't use returncode alone since both are non-zero |
| `output_suffix(rate, depth)` helper | Dest dirs and filenames include both rate and depth e.g. `48k-24b`; prevents collisions across bit depths and makes skip logic unambiguous |
| 32f AIF/AIFF → .wav output | AIFF can't encode float; SoX silently falls back to 32i. Force .wav for 32f+AIF sources. Same ext swap mirrored in both verifiers. |
| Checkboxes as pill toggles | All `input[type="checkbox"]` styled as dark-surface pill toggles with amber knob — default white checkbox breaks dark theme |
| No tqdm in batch_process | tqdm's `\r` updates (written to stderr, merged into stdout pipe) were corrupting parallel worker log lines via Python universal-newlines `\r`-as-newline. UI has its own phase bar. |
| "Done:" line as authoritative count | app.py incremental line-parse counts can be wrong when parallel workers write simultaneously. `process_audio.py` batch_process counts from return values and emits a final "Done:" summary — app.py parses this as the definitive numbers for the banner. |
| `process_file` returns "skipped" string | Distinct from None (rejected) so batch_process can count skipped files separately. Previously both returned None → skipped files inflated the silent count. |
| Dry run writes nothing | No dest dir, no log files, no progress/rejects JSON. Output is UI-only. dry_run flag never persisted to profile.json — it's a one-off mode. |

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
- Remote: `origin/main` — `https://github.com/scottie8392/ghost-processing`
- Working tree: clean as of 2026-03-25 Sprint 2 completion
