# Ghost Processing — Project Backlog

## Overview

**What it does:** Batch-converts audio stems from recording sessions. Detects and discards silent/empty files, resamples WAV/AIFF to a target sample rate (default 48kHz/24-bit) using SoX with shaped dithering. Runs as a local web app — double-click to launch.

**Environments:**
- **NAS mode (Mac):** Mounts NFS or SMB shares directly; no third-party mount manager needed.
- **Local mode (Mac):** Files on internal or directly connected drives.
- **Docker mode:** Container runs on the NAS itself with local filesystem access.

**Quick start — Mac:**
```bash
# First time and every time:
double-click start.command   # handles all setup, opens http://localhost:5001
```

**Quick start — Docker (NAS):**
```bash
docker-compose up -d
# Open http://NAS_IP:5001 from any browser on your network
```

**System requirements (Mac — all auto-installed by start.command):**
- Python 3.11+
- Homebrew
- SoX + FFmpeg (via Homebrew)
- Python venv + pip dependencies (requirements.txt)
- Passwordless sudo for `/sbin/mount_nfs` (one-time setup, prompted by start.command)

---

## 🔴 Must Fix (Known Bugs)

- [ ] **Hardcoded `-48` in destination directory name and verifier** — the filename generation in `process_file()` is already correct (uses `rate_suffix` derived from `target_sample_rate`). But two places still hardcode `-48`: (1) `main()` line 500 creates the dest dir as `{source_name}-48` regardless of target rate; (2) `run_verification()` line 404 checks for `{base}-48{ext}` in the dest, so it can't find correctly-named files and reports them all as missing. Fix both to use the same `rate_suffix` logic.
- [x] **executor.shutdown(wait=False)** — already fixed; `batch_process()` uses `wait=True, cancel_futures=True`.
- [ ] **verify_audio.py rejects.json crash** — `[e["path"] for e in json.load(f)]` will raise `KeyError` or `TypeError` if any list entry isn't a dict with a "path" key. Add type guard.
- [ ] **`/browse` endpoint path traversal** — no validation prevents a crafted request from browsing outside intended directories. Add a root-jail check for non-local deployments.
- [ ] **Stop button doesn't kill SoX workers** — the `/stop` endpoint only terminates `_active_process` (the Python wrapper spawned by `app.py`). Because it was launched with `start_new_session=True`, the SoX subprocesses inside the `ProcessPoolExecutor` are in a separate process group and don't receive the signal. Clicking Stop goes quiet in the log but SoX keeps running in the background. Fix: send SIGTERM to the entire process group (`os.killpg`).
- [ ] **Silence threshold too aggressive for sparse stems** — the default 10ms `min_non_silent_len` means a single snap, click, SFX hit, or impact that lands in an otherwise silent stem will keep the file (correct), but the threshold gives no headroom for very-low-level sparse content like room tone or quiet reverb tails near -50dBFS. Before trusting automated rejection, calibrate against a real session that includes sparse/SFX stems. Consider surfacing detected peak level in the log per file so you can see what the detector actually found.
- [ ] **BWF metadata is silently stripped** — SoX does not preserve the BEXT chunk that Pro Tools writes into every WAV (timecode position, originator, session name, creation date). After conversion the output files have no provenance data. For any project delivered to a picture editor, broadcast mixer, or archive, this destroys the session metadata. Fix: add a `bwfmetaedit` post-step that copies the BEXT chunk from source to dest after the SoX conversion.

---

## 🟡 Validation & Testing (Not Yet Done)

### Core conversion
- [ ] **Local mode conversion run** — source and dest on the Mac's local filesystem. Do this first — no NAS dependency, fastest way to confirm the core pipeline works after the recent bug fixes.
- [ ] **All sample rate outputs** — test 44.1kHz, 48kHz, 88.2kHz, 96kHz outputs. Confirm files are correctly resampled and named (e.g. `stem-48k.wav`, `stem-44k.wav`).
- [ ] **All bit depth outputs** — test 16-bit, 24-bit, 32-bit, 32f (float). Confirm SoX command is correct for each and output files are valid.
- [ ] **Silence detection calibration** — run against a known set of stems including intentionally silent tracks and sparse/SFX stems. Confirm correct rejection at default threshold (-50dB / 200ms). Use the analyzer (see 🟢 UI) to dial in settings first.
- [ ] **AIFF source files** — confirm `.aiff` files decode correctly via FFmpeg/pydub and convert cleanly.
- [ ] **Files with spaces and special characters in names** — confirm paths with spaces, parentheses, apostrophes pass through SoX subprocess correctly.
- [ ] **Deep folder nesting** — confirm recursive processing handles 3+ levels of subdirectories and output mirrors the structure exactly.
- [ ] **Resume from interrupted run** — kill a job mid-way, restart it, confirm already-converted files are skipped and the run completes correctly.
- [ ] **Dry run mode** — confirm no files are written and the log correctly reports what would have happened.
- [ ] **verify_audio.py after real run** — run the verifier against a completed job and confirm it accounts for all files.

### NAS (Mac)
- [ ] **Full NFS conversion run** — connect to NAS → browse → select source → run → verify output files, names, sample rates, and bit depths are correct.
- [ ] **Full SMB conversion run** — same as above via SMB protocol.
- [ ] **Multiple chips — same NAS, different shares** — confirm clicking chip A, browsing, then clicking chip B mounts and browses the new share independently.
- [ ] **unRAID appdata NFS mount** — confirm this specific share mounts after re-applying the export in unRAID settings.
- [ ] **NFS network drop during conversion** — simulate a network interruption mid-run (disable NAS interface or pull switch) and confirm: (a) the app detects the drop and logs it clearly rather than hanging silently, (b) the NFS retry logic (3 attempts + remount) fires correctly, (c) `progress.json` is intact when the network comes back, (d) restarting the job resumes from where it left off without re-converting completed files. Also test: laptop sleep/wake mid-run with NAS mounted.
- [ ] **SMB network drop during conversion** — same scenario via SMB. SMB has different timeout behaviour than NFS on macOS — confirm it fails fast rather than blocking workers indefinitely.
- [ ] **NAS reboot mid-run** — start a conversion, reboot the NAS, confirm the app surfaces a clear error and the job can be resumed cleanly once the NAS is back.

### Docker (Synology)
- [ ] **Fix docker-compose.yml before testing** — two gaps must be resolved first: (1) add a dest volume so converted files aren't lost on rebuild; (2) mount `config.docker.yaml` so paths can be configured without rebuilding the image. See 🟢 Infrastructure items.
- [ ] **Docker end-to-end** — `docker compose up`, access UI from another device on the network, run a conversion, confirm output appears on the NAS volume and persists after container restart.
- [ ] **Watch mode end-to-end** — drop a new file into a watched folder and confirm it gets picked up and converted. Confirm `stability_wait_sec` prevents processing files still being written.

---

## 🟢 Backlog — Features & Improvements

### Stability
- [ ] **Pre-flight check before run** — before starting, validate: source path exists and is readable, dest path is writable, SoX is installed and correct version, sufficient disk space for estimated output. Show a summary in the UI before committing.
- [ ] **Realistic progress bar** — true percentage (files done / total), elapsed time, and ETA. Currently the log scrolls but there's no summary-level progress indicator.
- [ ] **Health check page** — `/health` endpoint showing SoX version, FFmpeg version, Python version, active NFS mounts, disk space on source and dest. Useful for diagnosing issues without opening Terminal.
- [ ] **Log rotation** — cap individual log files at a reasonable size (e.g. 10MB) and rotate. Currently logs grow unbounded.
- [ ] **Progress.json stale entry cleanup** — if a converted file is later deleted from dest, it remains marked "done" in `progress.json`. Add a `--clean` flag to `verify_audio.py` that removes stale entries so the file gets re-converted on next run.
- [ ] **Config validation before run** — validate all values (sample rate, bit depth, silence threshold) in `app.py` before spawning the subprocess, with clear error messages in the UI rather than a crash in the log.

### Reconnection & Job State Awareness
These cover the gap between "job ran while browser/laptop was closed" and "user knows what happened when they reopen the app."

- [ ] **Prominent job result banner on reconnect** — `last_job.json` is currently shown in the status bar, which is easy to miss. On page load, if `last_job.json` exists and no job is currently running, show a prominent banner: job name, outcome (✓ complete / ✗ failed), file counts, and the timestamp it finished. Should be impossible to miss and should require a dismiss action to clear.
- [ ] **Orphaned process detection on server restart** — when `app.py` starts, if a `process_audio.py` subprocess is still running from a previous server instance (i.e., the server died but the detached job kept going), the new server has no idea. Implement a PID file: write the PID of the running `process_audio.py` to `running_job.pid` on start; delete it on completion. On server startup, if the PID file exists and the process is alive, show a warning in the UI: "A conversion job from a previous session is still running — do not start a new job." If the PID file exists but the process is dead, it means the job died uncleanly — surface that too.
- [ ] **Docker / remote: webhook notification on completion** — `osascript` notifications only fire on macOS. In Docker mode (container running on the NAS, browser on another device), there is no push notification at all. Add an optional webhook URL field in settings — on job completion, POST a JSON payload to it. Works with ntfy, Slack incoming webhooks, or any HTTP endpoint. One config field, no dependencies.

### Workflow
- [ ] **Named presets** — save and recall complete configurations as named profiles (e.g. "unRAID 48k/24", "Synology 44.1k/16", "Local Quick"). One click to load a known-good setup for a specific studio context.
- [ ] **Session queue** — queue multiple source folders to process sequentially. Add a queue panel to the UI with reorder/remove controls. Essential for unattended overnight runs across many sessions.
- [ ] **Re-process rejected files** — button in the File Review panel to retry rejected files with a temporarily lowered silence threshold. Useful when a stem is very quiet but not actually silent (e.g. room tone, reverb tail).
- [ ] **Folder exclude patterns** — option to skip subdirectories by name pattern (e.g. "Bounce", "Reference", "Archive"). Avoids accidentally converting non-stem files mixed into the session folder.
- [ ] **Multiple source dirs** — accept a list of source paths in one run rather than one at a time.

### Audio Quality
- [ ] **AIFF output option** — some studios deliver stems as AIFF. Add an output format dropdown (WAV / AIFF) alongside sample rate and bit depth.
- [ ] **Source sample rate reporting** — in the File Review panel or log, show the detected sample rate of each source file before conversion. Useful for spotting sessions with mixed rates.

### UI / UX
- [ ] **Pre-run summary modal** — before running, show a confirmation dialog: "X files to convert, Y already done, Z will be rejected. Estimated time: ~N minutes." Require a confirm click.
- [ ] **Silence detection analyzer / preview** — before committing to a full batch run, let the user select a single file and analyze it against the currently configured detection settings. The result panel should show: overall peak dBFS, whether the file would be kept or rejected under current settings, and a list of detected non-silent regions with their timestamps and peak levels. This lets you dial in `silence_thresh` and `min_non_silent_len` against a representative sparse stem (a snap, SFX hit, room tone track) before running thousands of files. Implementation: new `/analyze` endpoint in `app.py` that accepts a file path + detection params, runs `pydub silence.detect_nonsilent()` and `audio.max_dBFS`, returns structured JSON. UI: file path input + "Analyze" button above the Run button, collapsible result panel.
- [ ] **Run history** — a collapsible log of past runs: date, source, file counts, duration. Stored in a lightweight JSON file. Useful for auditing and for comparing across sessions.
- [ ] **Dark/light mode toggle** — the UI is dark-only currently. A light mode option for brighter studio environments.

### Infrastructure
- [ ] **Auto-discover NAS on network** — use mDNS/Bonjour to detect NAS devices on the local subnet and suggest them in the IP field. Removes the need to know or remember the NAS IP.
- [ ] **Remove setup.sh** — now fully redundant since `start.command` handles everything. Keep for reference or delete to reduce confusion.
- [ ] **Docker: template hot-reload in container** — `TEMPLATES_AUTO_RELOAD` is set in app.py but Waitress doesn't use Flask's reloader. Consider a volume mount for the template in docker-compose.yml for easier UI iteration without rebuilds.
- [ ] **docker-compose.yml missing dest volume** — the current compose file only mounts a source volume (`/volume1/Stems`). There is no volume for the converted output, which means converted files would be written inside the container and lost on rebuild. Add a second volume for the destination (e.g. `/volume1/Converted:/data/converted`) and update the config accordingly.
- [ ] **docker-compose.yml missing config file mount** — `config.docker.yaml` is not mounted into the container, so the defaults baked into the image are used. Mount it as a volume so settings can be updated without a rebuild: `./config.docker.yaml:/app/config.docker.yaml`.
- [ ] **Synology deployment workflow** — no documented process for getting the image onto the NAS and keeping it updated. Options to evaluate and document: (1) build on Mac + `docker save | ssh NAS docker load` — no registry needed, works offline; (2) push to Docker Hub or a private registry + pull from Container Manager on the NAS; (3) copy the project folder to the NAS over SSH/SMB and build directly on the NAS (`docker compose up --build`). Option 3 is simplest for a single-NAS setup. Also: Synology Container Manager can import a `docker-compose.yml` directly via its UI — worth documenting as the non-CLI path for future reference.
- [ ] **`save_profile()` is not atomic** — `app.py`'s `save_profile()` uses a plain `open()` + `json.dump()`, unlike the atomic tempfile+fsync+rename pattern used correctly in `process_audio.py`. Low risk for a local app but inconsistent — a crash mid-write could corrupt `profile.json`.
- [ ] **Completion counts parsed from log text** — `run_process()` in `app.py` counts converted/rejected/skipped by pattern-matching stdout strings. If a log message changes wording, counts silently go wrong. Better: have `process_audio.py` emit a structured JSON summary line at the end and parse that.

---

## ✅ Done

- [x] Consolidate `process_audio.py` versions into single script with SoX + shaped dither
- [x] Sample rate pre-check — skip files already at target rate
- [x] Per-run timestamped log files
- [x] Auto-verification after batch run
- [x] Browser-based web UI (Flask + SSE live log streaming)
- [x] `start.command` one-click Mac launcher — handles Python check, Homebrew, SoX, FFmpeg, venv, deps, sudoers, browser open
- [x] Mode selector — NAS / Local / Docker
- [x] NAS connection: Test Connection with showmount (NFS) / smbutil view (SMB)
- [x] NFS auto-mount via passwordless `sudo mount_nfs` (osascript fallback)
- [x] SMB mounting via osascript
- [x] Available shares displayed as clickable chips after test
- [x] Folder browser — server-side directory picker with parent navigation
- [x] NAS IP history with dropdown recall (protocol-aware, doesn't overwrite protocol)
- [x] SMB credentials (username/password) with "Remember me" checkbox
- [x] Source field clears on mode change, IP change, protocol change
- [x] Sample rate dropdown (48kHz default)
- [x] Bit depth dropdown (24-bit default, includes 32f float)
- [x] File Review panel — live-updating lists of rejected (silent) and skipped files
- [x] Docker path converter — reads docker-compose.yml mappings, resolves container → host path live as user types
- [x] Destination defaults to next to source when left blank
- [x] Folder browser starts at `~` in Local mode
- [x] Passwordless sudo configured by start.command (one-time, prompts for password)
- [x] Waitress WSGI server replaces Flask dev server — clean terminal output, no warnings
- [x] Werkzeug request logging suppressed
- [x] Terminal URL with Ctrl+click hint
- [x] setup.sh merged into start.command (single entry point)
- [x] Settings persist across sessions via `profile.json`
- [x] Atomic JSON writes (tempfile + fsync + rename) for `progress.json` — note: `save_profile()` in `app.py` still uses a plain write; see Infrastructure backlog item
- [x] Job naming from source directory basename (e.g. processing `Boston` shows "Boston")
- [x] Completion banner — visual ✓/✗ indicator with job name, converted/rejected/skipped counts
- [x] last_job.json persistence — last job result shown in status bar on fresh page load
- [x] macOS Notification Center alert on job completion (even if browser was closed)
- [x] SSE auto-reconnect with 500-line ring buffer replay — browser reconnects seamlessly after sleep/tab close
- [x] Process detachment (`start_new_session=True`) — conversion subprocess survives Terminal window close
- [x] start.command detects running job on reopen — reopens browser instead of killing server

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-22 | SoX + shaped dither over pydub resampling | Better quality; `rate -v` + `dither -s` reduces quantization noise on downsample |
| 2026-03-22 | Remove normalization | Stems must preserve original levels for mixing |
| 2026-03-22 | Output naming: `{name}-{rate}k.wav` | Simple, unambiguous — filename generation correct; directory naming and verifier still use hardcoded `-48` (see 🔴 bugs) |
| 2026-03-22 | Fork start method for multiprocessing | Avoids lock init issues on macOS with spawn |
| 2026-03-22 | Docker platform: `linux/amd64` | Synology NAS is Intel x86_64 |
| 2026-03-23 | Remove Share Path field from NAS UI | Auto-detected from source path using `detect_nas_share()` — one less field to fill |
| 2026-03-23 | Destination defaults to dirname(source) | Most natural workflow — output lands next to input |
| 2026-03-23 | `showmount -e` for NFS test, `smbutil view` for SMB | Verifies reachability without mounting; fast |
| 2026-03-23 | `sudo mount_nfs` primary, osascript fallback | sudo is silent and stable; osascript avoids sudo dependency but can show macOS dialogs on failure |
| 2026-03-23 | Waitress over Flask dev server | Eliminates terminal noise (WARNING banner, ANSI garbage); production-grade; pure Python |
| 2026-03-23 | No eject button for NAS shares | macOS Finder sidebar already has eject; out of scope for this tool |
| 2026-03-23 | Job naming from `os.path.basename(source_dir)` | Natural, automatic — no user input required; meaningful in notifications and completion banner |
| 2026-03-23 | `last_job.json` separate from `profile.json` | Profile is settings (survives many runs); last job is ephemeral state — separate concerns |
| 2026-03-23 | `start_new_session=True` on conversion subprocess | Detaches from Terminal's process group; conversion continues if Terminal closes |
| 2026-03-23 | `deque(maxlen=500)` ring buffer for SSE replay | Allows reconnecting clients to catch up on recent output without replaying the full log file |
