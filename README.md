# Telegram STL manager

**A local web app for downloading, organizing and previewing your STL release collection.**

Choose creators from your configured Telegram catalog, assign their destination
folders, and download their releases with one button. Telegram STL manager keeps the
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
  Direct photo/image attachments are skipped; images inside release archives are
  still extracted into `release_images`.
- **Fill spare download capacity.** Adaptive mode gradually runs up to three
  files, favoring other data centers when an archive is slow. An oldest-first
  download keeps the queue progressing, while faster completed releases can
  be extracted and moved ahead. The speed gauge shows combined throughput.
- **Check for new files quickly.** After the first complete creator scan, later
  download runs request only newer messages. Previously discovered files remain
  available for unfinished downloads or a change of scope.
- **Resume a stopped or unfinished queue.** Each completed artist check is saved. Resume keeps
  that run's artists, scopes and folders, skips completed checks and files, and
  reuses verified local downloads. An interrupted check resumes at that artist.
  Runs with unfinished files offer Resume and show received bytes separately
  from completed files. The review panel shows the latest attempt's unresolved
  issues instead of replaying warnings from earlier attempts.
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
  image bytes and original release archives stay intact. Successful filename
  repairs finish normally; skipped, damaged or unreadable images still raise warnings.
  macOS archive metadata is ignored, and legacy ZIP filename encodings are handled.
- **Organize an existing collection.** Preview a creator's flat folder, match
  archives against Telegram filenames and exact sizes, and review proposed
  monthly destinations. The separate **Apply** action moves matched files,
  optionally extracts images, and records them as already downloaded.
  Existing standalone images and collages are left in place and excluded from
  organizer previews and Apply actions.
  When an archive was uploaded in different versions, the organizer prefers the
  larger complete set, then the later upload if sizes are equal. It reuses a
  complete local copy or downloads the selected set when needed; it never joins
  parts from different versions. Previous files are retained under the month's
  `.previous_versions` folder before their replacements are installed.
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
- **Get completion alerts.** The relevant navigation tab pulses when a download
  run, organizer preview or Apply job finishes, even while you use another page.
  An optional chime sounds once across open browser tabs. Enable or test sound in
  Configuration, and click in the app once so the browser allows audio.

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

Set **Target total download speed** in Configuration to your connection’s
practical capacity in MB/s (default `28`). During a manual run, adaptive mode
starts with one file and adds at most one every 30 seconds while combined
measured speed is below 95% of the target, up to three transfers. It favors
unused data centers, available archive companions and faster observed sources.
File age alone does not determine speed. Slow files are never cancelled just
to change order, and the oldest pending work retains a download lane.

Completed files are extracted and moved while other downloads continue. Parts
of one archive wait for their companions. Failed items remain available for
review while unrelated releases continue. The scheduler reserves remaining
space for active transfers plus a 2 GiB margin and limits completed files
waiting for processing to about 8 GiB (the final file may exceed that limit).

The target is not a rate cap. Target and adaptive-mode changes apply within
five seconds during an existing run. Choose **One file at a time** to stop
adding parallel work; existing transfers finish normally. Saving settings does
not start a run, and no new attachments are added to its saved plan.

Auto normally keeps its server comparisons for up to six hours on the current
network. The optional speed threshold uses a 30-second average after each file's
first ten seconds. After **two minutes of observed download time** below the
threshold, Auto compares servers before the next suitable file. The counter
carries across files and releases on the same server; startup, extraction, NAS
moves and idle time pause it, even during long processing gaps. Recovery to the
threshold resets it. Finishing a release does not reset the server choice.

If measured speed falls **below half the threshold**, Auto pauses the current
file and compares immediately, without waiting two minutes. For a `20` MB/s
threshold, this means below `10` MB/s. The file resumes with its saved chunks;
progress does not restart from zero. Failed comparisons keep the previous server.
When files overlap, slowdown checks use their combined throughput.
Both slowdown rules have a ten-minute cooldown between comparisons. Auto changes
working servers only when another measures at least 10% faster. Set the threshold
to `0` to disable monitoring. Connection failures can still trigger retesting.
Comparisons consume bandwidth and can take more than ten seconds; they help
choose an endpoint but cannot guarantee a particular speed.

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
