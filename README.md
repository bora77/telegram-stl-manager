# Telegram STL

**A local web app for downloading, organizing and previewing your STL release collection.**

Choose creators from your configured Telegram catalog, assign their destination
folders, and download their releases with one button. Telegram STL keeps the
archives, extracted images and download history together, whether you store your
collection on a local disk or a mounted NAS share.

The app runs locally using a Telegram CLI connection. Everyday operation needs
no AI service, OCR, Telegram Desktop or VNC session. Downloads and folder changes
start when you request them; saving a subscription does not start a background
schedule.

## What it can do

- **Manage creator subscriptions.** Search the catalog, filter subscribed or
  unsubscribed creators, and choose a folder and starting month for each one.
  Select **All (archiving)** when you want the complete available collection.
- **Download all selected subscriptions.** The queue shows actual received
  bytes, progress, current and average speed, verification, image extraction and
  transfers to your destination. Split archives are collected before extraction.
- **Check for new files quickly.** After the first complete creator scan, later
  download runs request only newer messages. Previously discovered files remain
  available for unfinished downloads or a change of scope.
- **Resume a stopped queue.** Each completed artist check is saved. Resume keeps
  that run's artists, scopes and folders, skips completed checks and files, and
  reuses verified local downloads. An interrupted check resumes at that artist.
- **Remember completed work.** SQLite records attachment identities, exact
  sizes, destinations and transfer results. Moving a completed release out of
  Incoming later does not cause it to be downloaded again.
- **Choose download servers.** Auto compares available servers and reuses recent
  results. You can select an endpoint manually or enable a comparison for each
  artist/month. A configurable speed threshold can request a new comparison
  after a sustained slowdown.
- **Extract release images.** Images are extracted locally before delivery and
  collected in one flat `release_images` directory. Extraction status is saved
  so completed releases do not need to be opened again on every run.
  Unusual member names, including Windows backslashes, receive safe local names;
  image bytes and original release archives stay intact.
- **Organize an existing collection.** Preview a creator's flat folder, match
  archives against Telegram filenames and exact sizes, and review proposed
  monthly destinations. The separate **Apply** action moves matched files,
  optionally extracts images, and records them as already downloaded.
- **Recover incomplete releases.** Organizer warnings identify releases needing
  attention. A manual repair action can download known missing or incomplete
  archive parts and retry the release, retaining a backup of a replaced original.
- **Build release collages.** Drag 5–9 extracted images into a grid, rearrange
  them, and choose a layout, background, title and landscape, portrait or square
  output. Images keep their proportions and complete contents. Save the JPEG
  beside the release archives; replacing it keeps the previous version locally.
- **Download and organize at the same time.** Organizer previews can scan while
  a file is downloading. Each job has its own Stop control; operations affecting
  the same artist/month coordinate their file changes.

A release folder can look like this:

```text
Incoming/
└── Example Artist/
    └── 2026-08/
        ├── Example Artist 2026-08.7z.001
        ├── Example Artist 2026-08.7z.002
        ├── Example Artist-2026-08.jpg
        └── release_images/
            ├── preview_front__….jpg
            └── preview_side__….png
```

Release months are read from filenames. Files with unclear dates or ambiguous
matches remain available for review. Successful moves and extraction results
are recorded independently so retries can reuse completed work.

## A typical session

1. Set your local or mounted NAS destination in **Configuration**.
2. Find creators in **Artists**, assign their folders and scopes, and save.
3. Click **Download all subscriptions** on the queue page.
   If the run stops, use **Resume download queue** to continue the saved work.
4. Use **Organize folders → Preview** to review existing archives, then **Apply**
   when the proposed changes are ready.
5. Open **Collages** to select extracted images and save a release overview.

Auto normally keeps its server comparisons for up to six hours on the current
network. The optional speed threshold uses a 30-second average after each file's
first ten seconds. Speed must stay below the threshold for at least **five minutes
of observed download time**, carried across consecutive files on the same server.
Startup, extraction, NAS moves and idle time do not count. Recovery above the
threshold resets the counter; observations older than 30 minutes are discarded.

After a slowdown, the current file finishes and the next suitable file in that
data center triggers a comparison. Slowdown tests have a ten-minute cooldown;
Auto changes servers only when the comparison shows at least a 10% improvement.
Set the threshold to `0` to disable it. Connection failures can still trigger
retesting. Comparisons consume bandwidth and can take more than ten seconds;
they help choose an endpoint but cannot guarantee a particular speed.

Resume does not check already checked artists for newer posts; use a new
**Download all subscriptions** run for that. Completed staged files are reused;
an incomplete file transfer restarts. Resume requires the original destination
and source, and uses the current server settings. Errors that caused a stop may
need attention before retrying.

## Install and configure

Supported setup: **Linux**, or **Windows through WSL2**. Windows installation
instructions use Ubuntu inside WSL2; this is not a native Windows application.

- [Installation guide](INSTALL.md): dependencies, CLI build and login, local/NAS
  storage, configuration, startup and migration.
- [AI installation prompt](AI-INSTALL-PROMPT.md): instructions for an agent that
  can help set up a machine. An agent is optional, not a runtime dependency.
- [Application workflow](CATALOG.md): subscriptions, scopes, queue and organizer.
- [Images and collages](IMAGE-WORKFLOW.md): extraction, layouts, saved versions
  and recovery behavior.

Follow the installation guide before starting the app. Once configured, the web
interface is available at **http://127.0.0.1:6093/**.

The runtime uses Python, SQLite, Pillow, 7-Zip and a customized Go Telegram CLI.
The browser interface uses JavaScript. Go is needed to build the CLI; no cloud
image-generation service is involved.

## Private configuration and sharing

Each installation supplies its own authorized Telegram source and creator
catalog. The app limits its content operations to that configured source and
catalog. The repository includes no real Telegram group identity, creator
catalog, login session or downloaded release content.

Local configuration, databases, generated pages, screenshots and credentials stay
outside the public source package. Back up private state separately if you need
to preserve history, subscriptions, unfinished jobs or previous collage versions.

Before sharing a source archive, run:

```bash
python3 tools/package-release.py --check
python3 tools/package-release.py
```

For development, enable the staged-file privacy check:

```bash
git config --local core.hooksPath .githooks
```

The check uses `distribution.json` and validates staged content against the public
file list and locally configured source identity. Keep that allowlist intact.
You can also run it manually with `python3 tools/check-git.py`.
