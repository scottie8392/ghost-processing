# Ghost Processing

Batch audio conversion tool for music stems. Scrubs silent/empty files, converts 96kHz WAV/AIFF to 48kHz WAV using high-quality SoX resampling with shaped dithering, and names output files with a `-48` suffix.

Runs as a local web app — open a browser, fill in two paths, click Run.

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
9. [Troubleshooting](#troubleshooting)
10. [Advanced: Command Line](#advanced-command-line)

---

## How It Works

You point the tool at a folder of stems. For each audio file it finds:

1. **Skip** if already at 48kHz (no reprocessing)
2. **Reject** if the file is entirely silent or zero bytes
3. **Convert** everything else — 96kHz → 48kHz using SoX with shaped dithering

Output lands in a new folder next to your source, named after the session with a `-48` suffix. Example:

```
PT Exports/
└── Boston/               ← your source (untouched)
    ├── Drums.wav
    ├── Bass.wav
    └── Vocals.wav

_Ghost Tracks/
└── Boston-48/            ← created automatically
    ├── Drums-48.wav
    ├── Bass-48.wav
    └── Vocals-48.wav
```

Progress is tracked in a `progress.json` file in the output folder, so interrupted runs pick up where they left off.

---

## Requirements

### Mac

| Requirement | Version | Notes |
|-------------|---------|-------|
| macOS | 12+ | Monterey or later |
| Python | 3.11+ | Check with `python3 --version` in Terminal |
| Homebrew | any | Package manager — install at [brew.sh](https://brew.sh) |
| SoX | any | Audio conversion — `brew install sox` |
| FFmpeg | any | Audio decoding — `brew install ffmpeg` |
| AutoMounter | any | For NFS access to your NAS — [pixeleyes.nz](https://www.pixeleyes.co.nz/automounter/) |

> **Note:** `setup.sh` will install SoX and FFmpeg for you automatically if Homebrew is already installed.

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
cd ghost-processing
```

### Step 2 — Install Python (if needed)

Open Terminal and run:
```bash
python3 --version
```

If you see `Python 3.11` or higher, you're good. If not, download it from [python.org/downloads](https://www.python.org/downloads/).

### Step 3 — Install Homebrew (if needed)

Homebrew is a package manager for Mac that installs tools like SoX and FFmpeg. If you don't have it:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the on-screen instructions. This takes a few minutes.

### Step 4 — Run setup

In Terminal, `cd` to the project folder and run:

```bash
bash setup.sh
```

This will:
- Create an isolated Python environment (so nothing affects your system Python)
- Install all Python dependencies
- Install SoX and FFmpeg via Homebrew if they're missing

You should see green checkmarks for everything. If something fails, see [Troubleshooting](#troubleshooting).

### Step 5 — Make the launcher executable (first time only)

```bash
chmod +x start.command
```

### Step 6 — Launch

Double-click **`start.command`** in Finder.

A Terminal window will open briefly, then your browser will open automatically at `http://localhost:5001`. You're ready to use the app.

> **Tip:** You can also drag `start.command` to your Dock for quick access.

---

## Installation — Synology NAS (Docker)

This runs the app permanently on your NAS. You access the UI from any browser on your network at `http://YOUR_NAS_IP:5001`.

### Step 1 — Copy the project to your NAS

Copy the entire `ghost-processing` folder to your NAS. A good location is somewhere in your home volume, e.g. `/volume1/homes/admin/ghost-processing`.

You can do this via the Synology File Station, or with `scp` from Terminal:
```bash
scp -r ~/Documents/projects/ghost-processing admin@10.11.24.24:/volume1/homes/admin/
```

### Step 2 — Edit docker-compose.yml

Open `docker-compose.yml` in a text editor and update the volume path to match your NAS layout:

```yaml
volumes:
  - /volume1/Stems:/data/stems    # ← change /volume1/Stems to your actual Stems folder
  - ./logs:/app/logs
  - ./profile.json:/app/profile.json
```

The left side of each `:` is the path on your NAS. The right side is what the app sees inside the container — leave those as-is.

### Step 3 — SSH into your NAS and start the container

```bash
ssh admin@10.11.24.24
cd /volume1/homes/admin/ghost-processing
docker-compose up -d
```

The `-d` flag runs it in the background. It will restart automatically if the NAS reboots.

### Step 4 — Open the UI

From any device on your network, open a browser and go to:
```
http://10.11.24.24:5001
```

Replace `10.11.24.24` with your NAS IP address.

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

### Fill in the fields

**Source Directory** — the full path to the session folder you want to process.

This is the folder that contains your stems (WAV/AIFF files). It can have subfolders — the tool processes everything recursively.

```
Mac example:
/Users/scottie/Library/Containers/nz.co.pixeleyes.AutoMounter/Data/Mounts/Local-JUPITER/NFS/Stems/_Ghost Tracks/PT Exports/Boston

Synology Docker example:
/data/stems/_Ghost Tracks/PT Exports/Boston
```

**Destination Base** — the folder where output will be created.

The tool creates a new subfolder here, named after your session with `-48` appended. You don't need to create anything manually.

```
Mac example:
/Users/scottie/Library/Containers/nz.co.pixeleyes.AutoMounter/Data/Mounts/Local-JUPITER/NFS/Stems/_Ghost Tracks

Synology Docker example:
/data/stems/_Ghost Tracks
```

**Workers** — how many files to process at the same time.

- Mac (via NFS): `6` is a good balance
- Synology Docker: `4` is safer (Synology CPUs are slower)
- If the NAS feels sluggish during a run, lower this

**Dry Run** — tick this to do a test run without actually converting anything. Good for checking that paths are correct and seeing how many files would be processed.

**Verbose Logs** — tick this to see more detail in the output panel, including every file that gets skipped.

### Run

Click **Run Processing**. The output panel will show live progress as files are processed.

Lines are color-coded:
- **Green** — file converted successfully
- **Amber** — warning (e.g. file skipped, already converted)
- **Red** — error
- **Gray** — skipped/already done

When complete, a verification runs automatically and reports how many files were accounted for.

### Your paths are remembered

After the first run, your source and destination paths are saved. Next time you open the app, they're pre-filled. The **Source Directory** field also keeps a dropdown of your last 10 source paths — useful when switching between sessions.

---

## Finding Your Paths

### Mac (AutoMounter)

The NAS is mounted through AutoMounter, which puts files at a long path under your user Library. The easiest way to find it:

1. Open **Finder**
2. Navigate to the folder on your NAS that you want to process
3. Right-click the folder → **Get Info**
4. Under **Where**, you'll see the full path — copy it

Or in Terminal:
```bash
ls ~/Library/Containers/nz.co.pixeleyes.AutoMounter/Data/Mounts/
```

This lists all your mounted shares. Your stems will be somewhere inside one of these.

### Synology Docker

When running in Docker, paths are the container-internal paths. The NAS volume is mounted at `/data/stems` (as configured in `docker-compose.yml`). So if your stems are at `/volume1/Stems/_Ghost Tracks/PT Exports/Boston` on the NAS, inside the container that becomes:

```
/data/stems/_Ghost Tracks/PT Exports/Boston
```

---

## Understanding the Output

### Output folder

Given a source at `.../PT Exports/Boston`, the output will be at `.../PT Exports/../Boston-48/` — specifically, a `Boston-48` folder created inside your Destination Base.

### File naming

Each converted file gets `-48` appended before the extension:

```
Drums.wav       →   Drums-48.wav
Vocals.aiff     →   Vocals-48.aiff
Bass Stem.wav   →   Bass Stem-48.wav
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
silence_thresh: -50.0       # Files quieter than this (dB) are rejected
min_silence_len: 200        # Min ms for a chunk to count as silence
min_non_silent_len: 10      # Min ms of audio needed to keep a file

max_workers: 6              # Parallel processing jobs

# NFS remount (leave enable_script_remount: false to use AutoMounter)
mount_type: "nfs"
nfs_server_path: "10.11.24.24:/volume1/Stems"
mount_point: "/path/to/mount"
enable_script_remount: false
```
