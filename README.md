> **Claude — read `CLAUDE.md` first.** It has full project context, current state, known bugs, and immediate next steps. Pick up from there.

---

# Ghost Processing

Batch audio conversion tool for music stems. Detects and discards silent/empty files, then resamples WAV/AIFF to a target rate (48kHz default) and bit depth (24-bit default) using SoX with shaped dithering. Runs as a local web app — double-click to launch, connect to your NAS, click Run.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Requirements](#requirements)
3. [Installation — Mac](#installation--mac)
4. [Installation — Synology NAS (Docker)](#installation--synology-nas-docker)
5. [Sharing With Someone Else](#sharing-with-someone-else)
6. [Using the Web UI](#using-the-web-ui)
7. [Finding Your Paths](#finding-your-paths)
8. [Understanding the Output](#understanding-the-output)
9. [Overnight & Unattended Runs](#overnight--unattended-runs)
10. [Troubleshooting](#troubleshooting)
11. [Advanced: Command Line](#advanced-command-line)

---

## How It Works

You point the tool at a folder of stems. For each audio file it finds:

1. **Skip** if already at 48kHz (no reprocessing)
2. **Reject** if the file is entirely silent or zero bytes
3. **Convert** everything else — 96kHz → 48kHz using SoX with shaped dithering

Output lands in a new folder next to your source, named after the session with the target rate and bit depth appended. Example:

```
PT Exports/
└── Boston/               ← your source (untouched)
    ├── Drums.wav
    ├── Bass.wav
    └── Vocals.wav

_Ghost Tracks/
└── Boston-48k-24b/       ← created automatically
    ├── Drums-48k-24b.wav
    ├── Bass-48k-24b.wav
    └── Vocals-48k-24b.wav
```

Progress is tracked in a `progress.json` file in the output folder, so interrupted runs pick up where they left off.

---

## Requirements

### Mac

| Requirement | Version | Notes |
|-------------|---------|-------|
| macOS | 12+ | Monterey or later |
| Python | 3.11+ | Only manual step — download from [python.org](https://www.python.org/downloads/) if needed |
| Homebrew | any | Installed automatically by `start.command` if missing |
| SoX | any | Installed automatically via Homebrew |
| FFmpeg | any | Installed automatically via Homebrew |

### Synology NAS

| Requirement | Notes |
|-------------|-------|
| Container Manager | Install from Synology Package Center |
| Docker Compose | Included with Container Manager |

> SoX and FFmpeg are bundled inside the Docker image — no manual installation needed.

---

## Installation — Mac

### Step 1 — Get the project

If you received a zip file, unzip it to a folder like `~/Documents/projects/ghost-processing`.

If you're cloning from Git:
```bash
cd ~/Documents/projects
git clone <repo-url> ghost-processing
```

### Step 2 — Launch

**Right-click `start.command` → Open** (right-click is required the first time due to macOS security).

A Terminal window opens. On the very first run it will automatically:
- Install Homebrew if missing *(you may be asked for your password)*
- Install SoX and FFmpeg via Homebrew
- Create an isolated Python environment
- Install all Python dependencies
- Open your browser at `http://localhost:5001`

Subsequent launches are instant — just double-click `start.command`.

> **Python required:** The script will check for Python 3.11+ and open the download page if it's missing. Install it, then right-click → Open again.

> **Tip:** Drag `start.command` to your Dock for one-click access.

---

## Installation — Synology NAS (Docker)

This runs the app permanently on your NAS. You access the UI from any browser on your network at `http://YOUR_NAS_IP:5001`.

### Step 1 — SSH into your NAS and clone the repo

```bash
ssh admin@YOUR_NAS_IP
cd /volume1/docker        # or wherever you keep appdata
git clone https://github.com/scottie8392/ghost-processing.git
cd ghost-processing
echo '{}' > profile.json
echo '{}' > last_job.json
mkdir -p logs
```

### Step 2 — Edit docker-compose.yml

Update the volume paths to match your NAS layout:

```yaml
volumes:
  - /volume1/Stems:/data/stems    # ← change to your actual audio folder
  - ./logs:/app/logs
  - ./profile.json:/app/profile.json
  - ./last_job.json:/app/last_job.json
```

The left side of each `:` is the path on your NAS. The right side is what the app sees inside the container — leave those as-is.

> **Paths with spaces** must be quoted: `- "/volume1/Show Archive:/data/show_archive"`

### Step 3 — Build and start the container

```bash
sudo docker compose up --build -d
```

The `-d` flag runs it in the background. It restarts automatically if the NAS reboots.

### Step 4 — Open the UI

From any device on your network:
```
http://YOUR_NAS_IP:5001
```

### Updating to the latest version

```bash
git fetch origin && git reset --hard origin/main && sudo docker compose up --build -d
```

---

## Sharing With Someone Else

1. Give them the project folder (zip it up or share via Git)
2. They follow [Installation — Mac](#installation--mac) steps 1–6
3. The only thing they need to know is what path their NAS is mounted at — see [Finding Your Paths](#finding-your-paths)
4. Everything else saves automatically after their first run

---

## Using the Web UI

### Launch

Double-click `start.command`. Your browser opens to the app automatically.

### Choose your mode

Use the mode selector at the top to tell the app where your files live:

- **NAS** — files are on a network-attached storage device (Synology, unRAID, etc.). The app connects via NFS or SMB and mounts the share automatically.
- **Local** — files are on your Mac's internal drive or a directly connected external drive.
- **Docker** — you're accessing the app from inside a Docker container running on the NAS. Paths are container-internal (e.g. `/data/...`).

### NAS mode — connecting to your NAS

1. Enter your NAS IP address and select a protocol (NFS or SMB).
2. Click **Test Connection**. Available shares appear as clickable chips.
3. Click a share chip to mount it and pre-fill the source path.
4. Use the **folder icon** next to the source field to browse and select the exact session folder.

Your NAS IP and previously used paths are remembered for next time.

**SMB only:** A username/password field appears. Tick **Remember me** to persist credentials across sessions (stored in `profile.json` on your Mac — never leaves your machine).

### Local mode

Use the **folder icon** to browse your Mac's filesystem and select the session folder directly. No mounting needed.

### Docker mode

Enter container-internal paths (e.g. `/data/stems/_Ghost Tracks/PT Exports/Boston`). The app reads your `docker-compose.yml` and shows the corresponding host path below each field so you can confirm the mapping is correct.

### Configure the conversion

**Sample Rate** — target output rate. Default is 48kHz. Options: 44.1kHz, 48kHz, 88.2kHz, 96kHz.

**Bit Depth** — output bit depth. Default is 24-bit. Options: 16-bit, 24-bit, 32-bit, 32f (float).

**Destination Base** — where output is created. Leave blank to default to the folder next to your source. The app creates a new subfolder named after your session with the target rate and bit depth appended (e.g. `Boston-48k-24b`) — you don't need to create anything manually.

**Workers** — parallel conversions. Start at `6`. Lower if the NAS feels sluggish during a run; raise if your Mac is idle and you want it to go faster.

**Dry Run** — simulates the run without converting anything. Use this to check paths and see how many files would be processed.

**Verbose Logs** — shows every file in the output panel, including already-converted skips. Useful for troubleshooting or verifying the run.

### Run

Click **Run Processing**. The output panel shows live progress as files are processed.

Lines are color-coded:
- **Green** — file converted successfully
- **Amber** — warning (e.g. file skipped, already at target rate)
- **Red** — error
- **Gray** — skipped/already done

When the job finishes, a **completion banner** appears showing the job name, total files converted, rejected (silent), and skipped. A verification runs automatically and reports how many files were accounted for.

### Job names and last-run memory

Each run is named automatically from the source folder (e.g. processing `Boston` shows **"Boston"** as the job name). When you reopen the app, the status bar shows the result of the last job so you can see at a glance whether it completed successfully.

### Your settings are remembered

After the first run, your source directory, destination, workers setting, and NAS details are saved in `profile.json`. Next time you open the app, everything is pre-filled. The source directory field keeps a dropdown of your last 10 paths — useful when switching between sessions.

---

## Finding Your Paths

### NAS mode (Mac)

In NAS mode you don't need to know the full path ahead of time:

1. Enter your NAS IP and click **Test Connection**
2. Click the share chip to mount it
3. Use the **folder browser** (📁 icon) to navigate to your session folder and click **Select**

The path is filled in automatically.

If you prefer to type the path manually: once the share is mounted, the mount point is something like `/Volumes/Stems`. Your session folder would be `/Volumes/Stems/_Ghost Tracks/PT Exports/Boston`.

### Local mode (Mac)

Use the **folder browser** to navigate your Mac's filesystem. No paths to type.

### Docker mode (Synology / unRAID)

Paths inside the container depend on how you've configured volumes in `docker-compose.yml`. For example, if your docker-compose has:

```yaml
volumes:
  - /volume1/Stems:/data/stems
```

Then a session at `/volume1/Stems/_Ghost Tracks/PT Exports/Boston` on the NAS becomes `/data/stems/_Ghost Tracks/PT Exports/Boston` inside the container. The app shows the resolved host path below each field to help you confirm.

---

## Understanding the Output

### Output folder

Given a source at `.../PT Exports/Boston`, the output will be at `.../PT Exports/Boston-48k-24b/` — a folder created inside your Destination Base named after the session with the target rate and bit depth appended.

### File naming

Each converted file gets the target rate and bit depth appended before the extension:

```
Drums.wav       →   Drums-48k-24b.wav
Vocals.aiff     →   Vocals-48k-24b.wav
Bass Stem.wav   →   Bass Stem-48k-24b.wav
```

Folder structure is preserved exactly.

### Log files

Each run creates a timestamped log in the `logs/` folder:
```
logs/
├── process_audio.log        ← cumulative log across all runs
└── run_2026-03-22_14-30-00.log  ← this run only
```

### progress.json and rejects.json

These live inside the output folder (e.g. `Boston-48/`) and track what happened to every file:

- **`progress.json`** — files that were converted or skipped (with source hash for change detection)
- **`rejects.json`** — files that were rejected (silent, zero-byte) with the reason

If a run is interrupted, re-running will skip already-converted files and pick up where it left off.

---

## Overnight & Unattended Runs

### Resuming an interrupted job

Every file conversion is tracked in a `progress.json` file inside the output folder. If a run is interrupted for any reason — network drop, power loss, manual stop — just run it again with the same source and destination. Already-converted files are detected by their content hash and skipped automatically. Only the remaining files are processed.

### Closing the browser tab

Safe in all modes. The conversion job runs server-side and is not affected by the browser. When you reopen the app, the last job result is shown in the status bar so you can see whether it completed.

### Sleeping your Mac (lid close)

macOS suspends running processes but does not kill them. When your Mac wakes, the server resumes and the browser **automatically reconnects**. The log panel replays the last 500 lines so you can see everything that happened while you were away.

If you open `start.command` while a job is running, it detects the active job and reopens the browser instead of restarting the server.

### Closing the Terminal window (Mac modes)

The conversion subprocess is **detached from Terminal** — it will continue writing files to disk even after the Terminal window closes. However, the web server is a child of Terminal and will stop when the window is closed. Output files are written correctly either way; you just won't be able to monitor progress in the UI until you restart. Re-running after the job finishes will skip all already-converted files.

### Full Mac shutdown

The job stops. Re-running after restart resumes from the last completed file via `progress.json`.

### macOS notifications

When a job completes, you receive a **macOS Notification Center alert** with the job name and file count — even if your Mac was asleep and just woke up, or if the browser was closed.

### Recommended: Docker on NAS for overnight use

For truly unattended overnight runs the right setup is Docker on your Synology or unRAID. The job runs entirely on the NAS hardware — independent of your Mac's sleep state, Terminal, or browser.

| Scenario | Docker (NAS) | Mac — sleep | Mac — Terminal close | Mac — shutdown |
|----------|:---:|:---:|:---:|:---:|
| Job keeps running | ✅ | ✅ | ⚠️ files write, no UI | ❌ |
| UI reconnects | ✅ | ✅ | restart server | restart server |
| Resume from progress.json | ✅ | ✅ | ✅ | ✅ |
| Completion notification (macOS) | ✅ | ✅ | — | — |
| Last job shown on reopen | ✅ | ✅ | ✅ | ✅ |

---

## Troubleshooting

### "sox: command not found"

SoX isn't installed. Run:
```bash
brew install sox
```

### "ffmpeg: command not found"

FFmpeg isn't installed. Run:
```bash
brew install ffmpeg
```

### The browser doesn't open automatically

Flask may have taken longer than expected to start. Wait a few seconds and manually open `http://localhost:5001`.

### "Address already in use" / port 5001 conflict

Another instance is already running. Either use that one, or close it first:
```bash
lsof -ti:5001 | xargs kill -9
```
Then double-click `start.command` again.

### Files aren't showing up / source path not found

- Make sure AutoMounter has mounted the NAS share. Open Finder and navigate to the share to trigger the mount.
- Double-check the path — copy it from Finder's **Get Info** rather than typing it manually.
- Paths are case-sensitive. `PT Exports` ≠ `pt exports`.

### Processing stops with "Network error" / NFS disconnect

The NAS dropped the connection mid-run. The script retries automatically 3 times. If it keeps failing:
- Check your network connection
- Make sure the NAS isn't in sleep mode
- On Mac, AutoMounter should remount automatically — navigate to the share in Finder to force it
- Re-run the tool — already-converted files will be skipped

### "Already processing" error in the UI

A run is already active. Either wait for it to finish, or click **Stop** first.

### Docker container won't start on Synology

- Make sure Container Manager is installed from Package Center
- Check that port 5001 isn't in use by another container
- Verify your volume paths in `docker-compose.yml` exist on the NAS

### Verify a completed run manually

```bash
source ghost-processing-venv/bin/activate
python verify_audio.py --config config.local.yaml
```

This checks that every source file is accounted for and all destination files exist.

---

## Advanced: Command Line

If you prefer not to use the web UI, you can run the scripts directly:

```bash
# Activate the virtual environment first
source ghost-processing-venv/bin/activate

# Process (edit config.local.yaml first to set your paths)
python process_audio.py --config config.local.yaml

# Verify
python verify_audio.py --config config.local.yaml

# Verify with JSON output (for scripting)
python verify_audio.py --config config.local.yaml --json
```

### config.local.yaml reference

```yaml
source_dir: "/path/to/session"      # Folder to process
dest_base: "/path/to/output/base"   # Where output folder is created
log_dir: "./logs"                    # Where log files go

dry_run: false          # true = simulate without converting
watch_mode: false       # true = monitor folder for new files continuously
verbose: false          # true = show every file in logs

target_sample_rate: 48000   # Output sample rate in Hz
silence_thresh: -50.0       # Files quieter than this (short-time RMS dB) are rejected
min_non_silent_len: 10      # Min ms window — any chunk above threshold keeps the file

max_workers: 6              # Parallel processing jobs

# NFS remount (leave enable_script_remount: false to use AutoMounter)
mount_type: "nfs"
nfs_server_path: "10.11.24.24:/volume1/Stems"
mount_point: "/path/to/mount"
enable_script_remount: false
```
