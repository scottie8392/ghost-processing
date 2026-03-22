# Ghost Processing — Project Backlog

## Overview

**What it does:** Batch-converts audio stems from recording sessions. Scrubs silent/empty files, resamples 96kHz WAV/AIFF → 48kHz WAV using SoX with shaped dithering, and names output files with a `-48` suffix.

**Environments:**
- **Local (Mac):** Files accessed via AutoMounter NFS from JUPITER NAS. Fast processing.
- **Docker (Synology):** Container runs directly on the NAS with local filesystem access. Slower CPU, no network overhead.

**Quick start — Mac:**
```bash
cd ~/Documents/projects/ghost-processing
source ghost-processing-venv/bin/activate
# Edit config.local.yaml to set source_dir for the session
python process_audio.py --config config.local.yaml
python verify_audio.py --config config.local.yaml
```

**Quick start — Docker (on Synology):**
```bash
# Edit docker-compose.yml volume paths for the session
docker-compose up
```

**System requirements:**
- Python 3.13 + venv with `pip install -r requirements.txt`
- `sox` binary: `brew install sox` (Mac) or included in Docker image
- `ffmpeg` binary: `brew install ffmpeg` (Mac) or included in Docker image

---

## Backlog

- [ ] **Watch mode testing** — watch_mode exists but hasn't been tested end-to-end. Validate with a real session folder.
- [ ] **Per-session source_dir management** — currently you edit config.local.yaml manually each session. Consider a CLI flag `--source` to override without editing the file.
- [ ] **Dry-run report** — when dry_run is true, output a preview of what would be processed/rejected/skipped before committing.
- [ ] **Docker: NFS mount inside container** — current Docker setup assumes Synology-local paths. If source/dest are on a different NAS volume or remote, add NFS mount support to the container.
- [ ] **Rejects review workflow** — no easy way to review/re-process rejected files. Consider a `--reprocess-rejects` flag that re-evaluates files in rejects.json.
- [ ] **Progress.json cleanup** — if a converted file is deleted from dest, it stays "converted" in progress.json. Add a `--clean` flag to verify_audio.py that fixes stale entries.
- [ ] **Multiple source dirs in one run** — currently one source_dir per run. Could accept a list or glob pattern.
- [ ] **Notification on completion** — macOS `osascript` notification or Synology push notification when a batch finishes.

---

## In Progress

*(nothing active)*

---

## Done

- [x] Consolidate `process_audio.py` (v1 pydub) and `process_audio_wdither.py` (v2 SoX) into single script
- [x] Add sample rate pre-check — skip files already at target rate (48kHz)
- [x] Per-run timestamped log files (`logs/run_YYYY-MM-DD_HH-MM-SS.log`)
- [x] Fix NFS remount logic — now actually called when `enable_script_remount: true`; defaults to AutoMounter
- [x] Auto-verification after batch — runs quick check and prints summary
- [x] Rename `verify_audio_processing.py` → `verify_audio.py`; add `--json` flag
- [x] Add Docker support (Dockerfile + docker-compose.yml)
- [x] Split config into `config.local.yaml` and `config.docker.yaml`
- [x] Clean up requirements.txt — remove unused packages
- [x] Remove normalization (was removed in earlier commit)
- [x] Add shaped dithering via SoX for higher-quality resampling

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-22 | Use SoX + shaped dither instead of pydub for resampling | Better audio quality; SoX's `rate -v` + `dither -s` reduces quantization noise on downsample |
| 2026-03-22 | Remove normalization | Stems should preserve original levels for mixing; normalization changes the signal |
| 2026-03-22 | Default to AutoMounter for NFS (`enable_script_remount: false`) | AutoMounter handles reconnection more reliably than manual umount/mount; script remount available as opt-in |
| 2026-03-22 | Keep output naming as `{name}-48.wav` | Simple, unambiguous, consistent with existing output |
| 2026-03-22 | Locks created in `__main__` after `set_start_method('fork')` | Avoids multiprocessing lock issues on macOS with fork/spawn |
| 2026-03-22 | Docker platform: `linux/amd64` | JUPITER Synology is Intel x86_64 |

---

## Known Issues

- **SoX must be installed separately on Mac** — not a pip package. `brew install sox` required. Documented above.
- **FFmpeg must be installed separately on Mac** — pydub uses it to decode AIFF files. `brew install ffmpeg` required.
- **Watch mode untested** — stability check logic exists but has not been validated against real NFS writes.
- **Docker volume paths** — `docker-compose.yml` uses literal Synology paths (`/volume1/...`). Update for your NAS layout before running.
