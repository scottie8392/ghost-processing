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

- [x] **Hardcoded `-48` in destination directory name and verifier** — fixed in `main()` (dest dir name) and `run_verification()` (filename check); both now derive `rate_suffix` from `target_sample_rate` (e.g. 48000 → `48k`). Also fixed in `verify_audio.py`. Tested: 44.1kHz/16-bit run confirmed correct naming.
- [x] **executor.shutdown(wait=False)** — already fixed; `batch_process()` uses `wait=True, cancel_futures=True`.
- [x] **verify_audio.py rejects.json crash** — added `isinstance(e, dict) and "path" in e` type guard in both `process_audio.py` and `verify_audio.py`.
- [x] **`/browse` endpoint path traversal** — added `_BROWSE_BLOCKED` set in `app.py`; requests targeting `/etc`, `/System`, `/usr`, `/bin`, `/sbin`, `/Library/Keychains`, `/private/var`, `/root` return 403.
- [x] **Stop button doesn't kill SoX workers** — fixed with `os.killpg(os.getpgid(_active_process.pid), signal.SIGTERM)` in `/stop` endpoint; entire process group (Python wrapper + all SoX subprocesses) receives SIGTERM.
- [ ] **Silence detection measurement mismatch — must fix before relying on rejection** — two different measurements are currently in use and they don't agree: (1) the log displays `max_dBFS` (true momentary peak), (2) pydub's `detect_nonsilent` uses **RMS of short chunks** internally. This means a file with a transient peak at -40dBFS can still be rejected if its chunk RMS falls below the threshold — the numbers are not comparable. Observed in testing: `almost silent.wav` reported `peak -40.6dBFS` but was rejected at a `-50dB` threshold. The displayed peak and the threshold label both say "dB" but mean different things. **Must fix:** unify both measurements — either switch detection to peak-based or switch the log display to RMS. Research needed: peak detection is simple and directly comparable but noisy room tone or reverb tails may have peaks that rescue files that should be rejected. RMS is more representative of perceived loudness but harder for a user to reason about. Whatever is chosen, the log display and the threshold value must use the same metric, and the Advanced panel label must accurately describe what is being measured.
- [ ] **BWF metadata is silently stripped** — SoX does not preserve the BEXT chunk that Pro Tools writes into every WAV (timecode position, originator, session name, creation date). After conversion the output files have no provenance data. For any project delivered to a picture editor, broadcast mixer, or archive, this destroys the session metadata. Fix: add a `bwfmetaedit` post-step that copies the BEXT chunk from source to dest after the SoX conversion.

---

## 🟡 Validation & Testing (Not Yet Done)

### Core conversion
- [x] **Local mode conversion run** — confirmed working; 44.1kHz/16-bit test run completed on `/Users/scottie/Desktop/TR Test Show`.
- [x] **All sample rate outputs** — 44.1kHz ✓, 48kHz ✓, 88.2kHz ✓, 96kHz ✓. All correctly resampled and named with rate+depth suffix (e.g. `stem-48k-24b.wav`).
- [x] **All bit depth outputs** — 16-bit ✓, 24-bit ✓, 32-bit ✓, 32f ✓. Note: 32f with .aif/.aiff sources outputs as .wav (AIFF can't encode float — SoX warns and silently falls back to 32i; fixed by forcing .wav output for float).
- [ ] **Silence detection calibration** — run against a known set of stems including intentionally silent tracks and sparse/SFX stems. Confirm correct rejection at default threshold (-50dB / 200ms). Use the analyzer (see 🟢 UI) to dial in settings first.
- [x] **AIFF source files** — `.aif` extension detection fixed in `process_audio.py` and `verify_audio.py`; confirmed `.aif` files detected during test run.
- [x] **Files with spaces and special characters in names** — confirmed working; test run used `/Users/scottie/Desktop/TR Test Show` (space in path) without issues.
- [x] **Deep folder nesting** — confirmed working; recursive processing handles 3+ levels, output mirrors source structure exactly.
- [x] **Resume from interrupted run** — tested with 3 stop/resume cycles: stop during silence detection (no progress written → re-runs), stop after conversion (progress written → skips), stop before file starts (re-runs). Final run picked up exactly where it left off, verification clean. Note: partial dest file (stop mid-SoX write) handled correctly by design — progress.json only written on success, so partial file gets overwritten on resume.
- [x] **Dry run mode** — confirmed working; no files, dirs, or logs written. Log and banner match real run format. Amber banner with ◎ icon. Never persisted to profile.json.
- [ ] **Dry run: save log option** — dry run currently outputs only to the UI (no files created). Add a "Save dry run log" checkbox that, when ticked, writes the dry run output to the central `logs/` directory only (no dest dir created).
- [ ] **verify_audio.py after real run** — run the verifier against a completed job and confirm it accounts for all files.

### NAS (Mac)
- [x] **Full NFS conversion run** — confirmed working end-to-end: NAS mode, NFS mount, real session folder, 48kHz/24-bit. Verification passed — all files accounted for.
- [x] **Full SMB conversion run** — confirmed working end-to-end. Fixed share name vs mount point mismatch (macOS appends -1/-2 on naming conflicts — now uses nasShareRoot instead of mount point folder name). Fixed hung Stop (SIGTERM + SIGKILL escalation after 5s for workers blocked on SMB I/O).
- [x] **NAS share chip: error state not cleared on second selection** — after a failed mount (permission denied), clicking a second working share chip left the error message visible even on success. Fixed: the early-return path (already-mounted share) now calls `showNasStatus('connected', ...)` before opening the browser.
- [ ] **Multiple chips — same NAS, different shares** — confirm clicking chip A, browsing, then clicking chip B mounts and browses the new share independently.
- [ ] **unRAID appdata NFS mount** — confirm this specific share mounts after re-applying the export in unRAID settings.
- [ ] **NFS network drop during conversion** — simulate a network interruption mid-run (disable NAS interface or pull switch) and confirm: (a) the app detects the drop and logs it clearly rather than hanging silently, (b) the NFS retry logic (3 attempts + remount) fires correctly, (c) `progress.json` is intact when the network comes back, (d) restarting the job resumes from where it left off without re-converting completed files. Also test: laptop sleep/wake mid-run with NAS mounted.
- [ ] **SMB network drop during conversion** — same scenario via SMB. SMB has different timeout behaviour than NFS on macOS — confirm it fails fast rather than blocking workers indefinitely.
- [ ] **SMB stale mount detection and recovery** — macOS SMB mounts can go stale silently after a network drop: the mount point exists but all I/O fails immediately. The app currently has no detection for this. Need to: (1) detect stale mount mid-run (I/O error from SoX that isn't a normal file error), (2) surface a clear "NAS disconnected — remount and re-run to resume" message in the UI rather than a cryptic SoX failure, (3) optionally attempt auto-remount before surfacing the error. NFS is less affected as the kernel stalls and retries; SMB needs explicit handling.
- [ ] **NAS reboot mid-run** — start a conversion, reboot the NAS, confirm the app surfaces a clear error and the job can be resumed cleanly once the NAS is back.

### Docker (Synology)
- [ ] **Fix docker-compose.yml before testing** — two gaps must be resolved first: (1) add a dest volume so converted files aren't lost on rebuild; (2) mount `config.docker.yaml` so paths can be configured without rebuilding the image. See 🟢 Infrastructure items.
- [ ] **Docker end-to-end** — `docker compose up`, access UI from another device on the network, run a conversion, confirm output appears on the NAS volume and persists after container restart.
- [ ] **Watch mode end-to-end** — drop a new file into a watched folder and confirm it gets picked up and converted. Confirm `stability_wait_sec` prevents processing files still being written.
- [ ] **Watch mode: Pro Tools bounce race condition** — Pro Tools writes bounce files incrementally; the file appears on disk immediately but grows as the export progresses. The watcher must not pick up a file until Pro Tools has finished writing it. Current `stability_wait_sec` setting is the guard, but it needs to be validated against real Pro Tools bounces — the stability window must be longer than the longest expected write time for a large session. Also consider: watch for file size stability (no growth for N seconds) rather than just a fixed delay after creation.

---

## 🟢 Backlog — Features & Improvements

### Stability
- [ ] **Pre-flight check before run** — before starting, validate: source path exists and is readable, dest path is writable, SoX is installed and correct version, sufficient disk space for estimated output. Show a summary in the UI before committing.
- [ ] **Realistic progress bar** — true percentage (files done / total), elapsed time, and ETA. Currently the log scrolls but there's no summary-level progress indicator.
- [ ] **Health check page** — `/health` endpoint showing SoX version, FFmpeg version, Python version, active NFS mounts, disk space on source and dest. Useful for diagnosing issues without opening Terminal.
- [ ] **Log rotation / pruning** — cap individual log files at a reasonable size (e.g. 10MB) and rotate. Also prune old log files from the central `logs/` directory after N days or when count exceeds a limit. Currently logs accumulate unbounded.
- [ ] **Progress.json stale entry cleanup** — if a converted file is later deleted from dest, it remains marked "done" in `progress.json`. Add a `--clean` flag to `verify_audio.py` that removes stale entries so the file gets re-converted on next run.
- [ ] **Dest file integrity check on resume** — the resume logic currently checks that the dest file exists and the source hash matches, but does not verify the dest file itself. A truncated write, disk error, or corruption after conversion would be silently skipped on re-run. Fix: store a `dest_hash` in `progress.json` alongside `source_hash` at write time, and verify it on resume — if it doesn't match, re-convert. Also makes `verify_audio.py` more useful since it can flag corrupt outputs without needing the source.
- [ ] **Config validation before run** — validate all values (sample rate, bit depth, silence threshold) in `app.py` before spawning the subprocess, with clear error messages in the UI rather than a crash in the log.

### Reconnection & Job State Awareness
These cover the gap between "job ran while browser/laptop was closed" and "user knows what happened when they reopen the app."

- [ ] **Prominent job result banner on reconnect** — `last_job.json` is currently shown in the status bar, which is easy to miss. On page load, if `last_job.json` exists and no job is currently running, show a prominent banner: job name, outcome (✓ complete / ✗ failed), file counts, and the timestamp it finished. Should be impossible to miss and should require a dismiss action to clear.
- [ ] **Orphaned process detection on server restart** — when `app.py` starts, if a `process_audio.py` subprocess is still running from a previous server instance (i.e., the server died but the detached job kept going), the new server has no idea. Implement a PID file: write the PID of the running `process_audio.py` to `running_job.pid` on start; delete it on completion. On server startup, if the PID file exists and the process is alive, show a warning in the UI: "A conversion job from a previous session is still running — do not start a new job." If the PID file exists but the process is dead, it means the job died uncleanly — surface that too.
- [ ] **Frozen job watchdog** — the blinking "Running" dot is driven by the SSE heartbeat, not by the script itself. If a worker hangs inside SoX or pydub, `_is_running` stays True, heartbeats keep coming, and the dot blinks forever. A real signal: if no new log lines have appeared for N seconds (e.g. 60s) while the job is running, surface a "No activity detected — job may be frozen" warning in the UI. Reset the timer on every incoming log line.
- [ ] **Docker / remote: webhook notification on completion** — `osascript` notifications only fire on macOS. In Docker mode (container running on the NAS, browser on another device), there is no push notification at all. Add an optional webhook URL field in settings — on job completion, POST a JSON payload to it. Works with ntfy, Slack incoming webhooks, or any HTTP endpoint. One config field, no dependencies.

### Workflow
- [ ] **NAS IP nicknames** — let the user assign a friendly name to each NAS IP (e.g. "192.168.1.20" → "Synology" or "unRAID"). Show the nickname in the IP history dropdown and connection status bar so frequently used servers are immediately recognisable without memorising IPs.
- [ ] **Source/destination favorites** — let the user star frequently used folder paths (source and destination) and recall them from a dropdown in the respective field. Persisted in `profile.json`. Useful for returning to the same session folder or delivery destination across many jobs.
- [ ] **Named presets** — save and recall complete configurations as named profiles (e.g. "unRAID 48k/24", "Synology 44.1k/16", "Local Quick"). One click to load a known-good setup for a specific studio context.
- [ ] **Session queue** — queue multiple source folders to process sequentially. Add a queue panel to the UI with reorder/remove controls. Essential for unattended overnight runs across many sessions.
- [ ] **Re-process rejected files** — button in the File Review panel to retry rejected files with a temporarily lowered silence threshold. Useful when a stem is very quiet but not actually silent (e.g. room tone, reverb tail).
- [ ] **Subfolder flattening** — tick box to output all converted files into a single flat directory, stripping the source subdirectory structure. Useful when the session has nested folders (e.g. `Drums/OH/`, `Guitars/DI/`) but the delivery needs a flat folder of stems. Collision handling needed: if two files in different subdirs share a filename, append the parent folder name to disambiguate.
- [ ] **Folder exclude patterns** — option to skip subdirectories by name pattern (e.g. "Bounce", "Reference", "Archive"). Avoids accidentally converting non-stem files mixed into the session folder.
- [ ] **Multiple source dirs** — accept a list of source paths in one run rather than one at a time.

### Audio Quality
- [ ] **AIFF output option** — some studios deliver stems as AIFF. Add an output format dropdown (WAV / AIFF) alongside sample rate and bit depth.
- [x] **Force WAV output toggle** — checkbox in UI; `force_wav` config flag passed to process_audio.py; dest_ext logic updated in both process_file and run_verification.
- [ ] **Mono L/R pair combining** — Pro Tools exports stereo tracks as two mono files suffixed with ` L` and ` R` (space + letter, e.g. `Strings L.wav` / `Strings R.wav`). Add an option to detect matched pairs and merge them into a single stereo interleaved file (e.g. `Strings.wav`), dropping the ` L` / ` R` suffix. Files without either suffix are true mono and pass through unchanged. Pairs must match exactly on basename, extension, sample rate, and bit depth before merging. Use SoX `sox -M left.wav right.wav stereo.wav` (or equivalent merge command). Run after conversion so both channels are already at the target format before combining. Edge cases: unpaired ` L` or ` R` file (no matching partner) — pass through as-is and log a warning.
- [ ] **Source sample rate reporting** — in the File Review panel or log, show the detected sample rate of each source file before conversion. Useful for spotting sessions with mixed rates.
- [ ] **Per-file peak level display (QC)** — log peak dBFS for each file during the Checking phase. Surfaces which files are near the silence threshold (quiet room tone, sparse SFX) vs full-level stems. Essential for silence calibration — helps explain why a file was rejected or kept and gives confidence in the threshold setting. Could show inline in the log (`Checking: stem.wav (48k/24b, peak -32dBFS)`) and optionally in the File Review panel.

### UI / UX
- [ ] **Pre-run summary modal** — before running, show a confirmation dialog: "X files to convert, Y already done, Z will be rejected. Estimated time: ~N minutes." Require a confirm click.
- [ ] **Silence threshold calibration tool** — a calibration workflow in the Advanced section that lets the user feed one or more reference files (e.g. an open mic recording of the room noise floor, a known-silent track, a sparse SFX stem) and auto-suggests appropriate threshold and min-length settings. Should display: measured level of each reference file using whatever metric silence detection uses (once measurement mismatch is resolved), a recommended threshold with some headroom, and a preview of which files in the source folder would be rejected at that setting. Goal: user never has to guess — they hand it a noise floor sample and the app proposes numbers they can accept or tweak.
- [ ] **Silence detection analyzer / preview** — before committing to a full batch run, let the user select a single file and analyze it against the currently configured detection settings. The result panel should show: overall peak dBFS, whether the file would be kept or rejected under current settings, and a list of detected non-silent regions with their timestamps and peak levels. This lets you dial in `silence_thresh` and `min_non_silent_len` against a representative sparse stem (a snap, SFX hit, room tone track) before running thousands of files. Implementation: new `/analyze` endpoint in `app.py` that accepts a file path + detection params, runs `pydub silence.detect_nonsilent()` and `audio.max_dBFS`, returns structured JSON. UI: file path input + "Analyze" button above the Run button, collapsible result panel.
- [ ] **Run history** — a collapsible log of past runs: date, source, file counts, duration. Stored in a lightweight JSON file. Useful for auditing and for comparing across sessions.
- [ ] **Dark/light mode toggle** — the UI is dark-only currently. A light mode option for brighter studio environments.

### Infrastructure
- [ ] **Auto-discover NAS on network** — use mDNS/Bonjour to detect NAS devices on the local subnet and suggest them in the IP field. Removes the need to know or remember the NAS IP.
- [x] **Remove setup.sh** — deleted; fully redundant since `start.command` handles all setup.
- [ ] **Docker: template hot-reload in container** — `TEMPLATES_AUTO_RELOAD` is set in app.py but Waitress doesn't use Flask's reloader. Consider a volume mount for the template in docker-compose.yml for easier UI iteration without rebuilds.
- [ ] **docker-compose.yml missing dest volume** — the current compose file only mounts a source volume (`/volume1/Stems`). There is no volume for the converted output, which means converted files would be written inside the container and lost on rebuild. Add a second volume for the destination (e.g. `/volume1/Converted:/data/converted`) and update the config accordingly.
- [ ] **docker-compose.yml missing config file mount** — `config.docker.yaml` is not mounted into the container, so the defaults baked into the image are used. Mount it as a volume so settings can be updated without a rebuild: `./config.docker.yaml:/app/config.docker.yaml`.
- [ ] **Synology deployment workflow** — no documented process for getting the image onto the NAS and keeping it updated. Options to evaluate and document: (1) build on Mac + `docker save | ssh NAS docker load` — no registry needed, works offline; (2) push to Docker Hub or a private registry + pull from Container Manager on the NAS; (3) copy the project folder to the NAS over SSH/SMB and build directly on the NAS (`docker compose up --build`). Option 3 is simplest for a single-NAS setup. Also: Synology Container Manager can import a `docker-compose.yml` directly via its UI — worth documenting as the non-CLI path for future reference.
- [x] **`save_profile()` is not atomic** — `app.py`'s `save_profile()` uses a plain `open()` + `json.dump()`, unlike the atomic tempfile+fsync+rename pattern used correctly in `process_audio.py`. Low risk for a local app but inconsistent — a crash mid-write could corrupt `profile.json`.
- [x] **Completion counts parsed from log text** — `run_process()` in `app.py` still does incremental line-by-line counting as a live signal for the phase bar, but the final banner counts now come from parsing the `"Done: N converted[, N copied][, N silent][, N skipped] in Xs"` line emitted by `process_audio.py`'s `batch_process()` return values. This is authoritative regardless of parallel worker log ordering.

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
- [x] `.aif` extension detection — added to `is_audio_file()` in `process_audio.py` and `verify_audio.py`
- [x] Phase progress bar — "Detecting silence X/N" → "Converting X/N" phases visible in UI during run
- [x] "New Run" button — appears in completion banner; resets UI without page reload
- [x] Run log written to dest dir — each dest folder gets `run_{timestamp}.log` alongside `progress.json` and `rejects.json`
- [x] Stop button kills entire process group — `os.killpg` ensures SoX workers are terminated, not just the Python wrapper
- [x] `/browse` path traversal protection — `_BROWSE_BLOCKED` denies access to sensitive system directories
- [x] sudoers re-prompt fix — launch script now correctly detects existing passwordless sudo rule using stderr string match
- [x] Stop vs Error distinction — `_stop_requested` flag in `app.py`; user-initiated stop saves `status: "stopped"` in `last_job.json` and SSE `done` event; UI shows "Stopped" with neutral grey banner and ◼ icon instead of red "Error"
- [x] Output suffix includes bit depth — `output_suffix(rate, depth)` helper; dest dirs and filenames now `{name}-48k-24b/`, `stem-48k-24b.wav` etc. Prevents collisions when converting same source to different bit depths
- [x] Skip logic checks both rate AND bit depth — converting same source to different bit depth at same sample rate now triggers conversion instead of skipping
- [x] 32f AIF/AIFF sources output as .wav — AIFF can't encode floating-point PCM; SoX silently falls back to 32i. Fixed by forcing .wav output for 32f+AIF sources. Verifier updated to match.
- [x] Verification summary fixed — `run_verification()` was counting `skipped_rate` entries as converted; split into `n_converted` / `n_skipped`
- [x] Log note styled as amber callout box — subtle amber tint + left border accent; moved into btn-row to fill space beside Run/Stop
- [x] Checkboxes styled as dark-theme pill toggles — amber knob on check, matches dark UI
- [x] Dry Run moved to its own field with field-label + hint (matches Workers layout); Verbose Logs moved to Advanced → Logging section
- [x] Already-at-target files copied to output instead of skipped — `shutil.copy2` + `status: "copied"` in `progress.json`; UI shows "N copied" in completion banner and last-run chip when non-zero
- [x] NAS share chip error cleared on re-select — clicking a working share after a failed one now correctly shows "Connected" status
- [x] SMB share name vs mount point mismatch — macOS appends -1/-2 when share name conflicts with existing mount; selectDir() now uses nasShareRoot (actual share name) instead of deriving it from the mount point folder name
- [x] Stop button SIGKILL escalation — after SIGTERM, spawns a thread that SIGKILLs the process group after 5s; fixes "Already processing" stuck state when workers are blocked on NFS/SMB kernel I/O
- [x] NAS mode dest path resolution — local absolute paths (e.g. /Users/scottie/Desktop) were being treated as NAS-relative paths, routing output to the NAS mount. Now detects local paths by checking if the parent directory exists and uses them as-is
- [x] Log and banner colors — Rejected: lines changed from amber to red (matches File Review panel); Copied: lines green in both terminal log and completion banner (matches Converted); "no conversion needed" replaces "already at target" in Copied log messages
- [x] Dry run overhaul — no files, dirs, or logs written; amber banner with ◎ icon and "Dry Run" title; log format unified with real run (timestamps stripped, colour-coded, format info per file e.g. `Checking: foo.wav (48k/24b)`); dry_run never persisted to profile.json
- [x] Log display cleanup — Python timestamp prefix stripped from all UI log lines; colour-coded by type (success/warn/error/dim/meta/dry-run); format info (rate/depth) shown per file
- [x] "New Run" button removed from completion banner — no longer needed
- [x] tqdm removed from batch_process loop — tqdm wrote `\r` carriage returns to stderr (merged with stdout pipe); Python's universal-newlines mode treats `\r` as a line terminator, so tqdm updates interleaving with parallel worker log writes were splitting/corrupting those lines. Replaced with plain `as_completed()` loop; UI phase bar handles progress display
- [x] "Done:" line as authoritative count source — app.py now parses `"Done: N converted..."` line from process_audio.py as the final authoritative counts for the SSE summary event and banner, fixing race conditions where parallel worker "Rejected:" or "Checking:" lines arrived after other output
- [x] process_file returns "skipped" — previously returned None for both rejected (silent) and already-processed (skipped) files; batch_process counted both as rejected. Now returns "skipped" string; batch_process counts it separately; "Done:" line and banner show correct split
- [x] File Review panel "Skipped" label — changed to "Skipped — already exist in destination" (accurate: skip requires dest file exists + source hash match)

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
| 2026-03-23 | `os.killpg` for Stop button | `_active_process.terminate()` only reached Python wrapper; SoX workers are in a separate process group due to `start_new_session=True` — must kill the entire group |
| 2026-03-23 | "Analyzing:" log line moved to after silence detection | Previously logged before silence check — workers grabbed files instantly so phase bar hit 100% immediately. Now logs after silence check passes, before SoX — tracks real completions |
| 2026-03-23 | Run log written to dest dir alongside progress.json | Each dest folder is self-contained (log + progress + rejects); easier to archive and audit |
| 2026-03-23 | sudoers check: `sudo -n /sbin/mount_nfs 2>&1 \| grep "password is required"` | Previous check used `sudo -n grep` and `--help` flag; both returned non-zero even when rule was installed. Stderr string is reliable. |
| 2026-03-23 | `_stop_requested` flag to distinguish Stop from error | returncode is non-zero for both user stop (SIGTERM → -15) and genuine crash — can't use it alone. Flag set in `/stop` before killpg, read in `run_process` after wait(). rc==0 check is first so a clean finish can't be mislabeled. |
| 2026-03-24 | Remove tqdm from batch_process | tqdm's `\r` progress bar updates written to stderr (merged into stdout pipe) were corrupting parallel worker log lines via universal-newlines line splitting. UI has its own phase bar — tqdm added nothing and broke output. |
| 2026-03-24 | "Done:" line as authoritative count source | `batch_process()` counts from `process_file` return values (accurate). app.py's line-by-line count is unreliable when parallel workers write simultaneously. Parsing the final "Done:" summary gives exact numbers regardless of ordering. |
| 2026-03-24 | process_file returns "skipped" string | Was returning None for both rejected and skipped — batch_process couldn't distinguish them. Returning "skipped" lets batch_process count accurately; "Done:" line and banner reflect the real split. |
| 2026-03-24 | Dry run writes nothing — not even the top-level dest dir | Dry run is a report tool. Creating directories or log files would be confusing and potentially polluting. All output goes to the UI only. |
| 2026-03-24 | SIGTERM + SIGKILL escalation for Stop | Workers blocked on NFS/SMB kernel I/O ignore SIGTERM until the syscall returns. A background thread sends SIGKILL to the process group after 5s, guaranteeing _is_running clears. |
| 2026-03-24 | Local dest path detection via parent directory existence | In NAS mode, detect_nas_share() was mangling local absolute paths (e.g. /Users/scottie/Desktop → /Volumes/Stems/scottie/Desktop). Check os.path.exists(parent) to distinguish local paths from NAS-relative ones. |
