# Telegram STL manager

**A local web app for downloading, organizing and previewing your STL release collection.**

**[Download the latest Windows installer](https://github.com/bora77/telegram-stl-manager/releases/latest/download/Telegram-STL-Manager-Windows.zip)**
 · [Release notes and other downloads](https://github.com/bora77/telegram-stl-manager/releases/latest)

New users: extract the entire ZIP, double-click **Install.cmd**, and accept the
administrator prompt. The installer sets up WSL automatically; configure the app
in your browser afterward.

Choose creators from your configured Telegram catalog, assign their destination
folders, and download their releases with one button. Telegram STL manager keeps the
archives, extracted images and download history together, whether you store your
collection on a local disk or a mounted NAS share.

The app runs locally using a Telegram CLI connection. Everyday operation needs
no AI service, OCR, Telegram Desktop or VNC session. Downloads and folder changes
start when you request them; saving a subscription does not start a background
schedule.

## Software components and how they work together

The manager runs on your own machine. Your normal browser displays the interface
and talks to a local Python web server; that server starts workers for downloads,
image extraction and organization. Files pass through local staging storage before
being delivered to your chosen local folder or mounted network share.

| Component | Purpose |
| --- | --- |
| HTML, CSS and JavaScript | The browser interface: creator selection, progress, configuration, organizer previews and the drag-and-drop collage editor. No browser extension is required. |
| Python | Application logic, job management and the local web server, using Python's built-in `ThreadingHTTPServer`. No separate Apache or nginx installation is needed. |
| Telegram CLI (`tdl`, written in Go) | Connects directly to Telegram through the `gotd` MTProto library. The bundled build includes this project's integration patches for transfer control and server selection. It reads the configured source and downloads attachments without controlling Telegram Desktop. |
| SQLite and JSON files | SQLite stores download history, MMF source versions and collage records. JSON stores settings, subscriptions and saved job state. SQLite is embedded; there is no separate database server to configure. |
| 7-Zip | Reads supported archive formats, including RAR, extracts release contents and builds prepared MMF `.7z` sets with configurable compression and part size (defaults: level 7, 4000 MiB). |
| Pillow | Processes images, creates previews and renders the final JPEG collages. |
| pypdf | Processes recognized PDF footer stamps during MMF repackaging, without rasterizing pages. |
| Direct MMF HTTP client (beta) | Python's HTTP and cookie libraries authenticate to MyMiniFactory, read the supported library collections and fetch files. Checks and downloads do not require a separate MMF browser window or Tampermonkey. |
| WSL2, Ubuntu and PowerShell on Windows | The installer prepares a dedicated Linux environment and the launcher starts the app inside it. You use the interface in your normal Windows browser. Linux installations run directly. |

The runtime package bundles Python, the application web server, SQLite, Pillow, pypdf,
the compiled Telegram CLI and 7-Zip. Go is needed to build the CLI from source,
not to run the packaged application. No AI service or OCR is used during normal
operation. Installation details and platform requirements are in [INSTALL.md](INSTALL.md).

### From selection to saved release

1. **Configure and select.** Complete initial setup, connect your account, choose
   a destination and save the creators and release scopes you want.
2. **Check availability.** While a manager page is open, the browser requests
   checks at the configured interval. These checks announce available downloads;
   they do not start transfers. Closing the browser stops those scheduled checks.
3. **Start a run manually.** The server saves a queue and starts a worker. Telegram
   checks reuse saved scan positions and history. MMF tracks source versions so
   additions or changes to older collections can be considered too.
4. **Download and process locally.** Workers stage files on the application
   machine, collect companion archive parts and extract images into a flat
   `release_images` folder. MMF and Telegram downloads retain the original archives;
   MMF packaging is a separate **Make release** action.
5. **Deliver and remember.** Files are verified during delivery to the selected
   destination, and successful work is recorded. New monthly folders use
   `Artist/Artist YYYY-MM/`; named MMF collections keep their names with the
   artist prefix. Existing legacy monthly folders remain supported.
6. **Review or continue.** Failed items remain available for review or retry.
   Saved queues and processing records let subsequent attempts reuse completed
   work. Organizer Apply uses the same delivery and image-processing machinery
   for existing files, while Collages saves the finished JPEG beside the archives.

An already started worker runs independently of the browser as long as the local
application remains running. Download and organizer jobs can overlap, with locks
coordinating changes to the same release. Account sessions and history stay in
local application data and are excluded from the public distribution.

## What it can do

- **Artist logos and creator links.** A collected library covers the catalog,
  with initials where no confident image match is available. Click an artist's
  logo or **Creator links / logo** to open its Patreon, MyMiniFactory, Cults or
  other verified storefront links. Upload a replacement, choose initials, or
  restore the collected logo. Your replacements are saved separately and survive
  library updates. Normal use reads local files and needs no AI or web search.
  Collected links come from matched creator pages; blank entries mean the lookup
  could not establish a reliable match, not that the artist has no account.

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
- **Handle corrupt downloads.** Integrity errors offer **Redownload** to fetch
  fresh bytes while retaining the old local copy, or **Ignore corrupt source**
  to skip that attachment in future runs. Ignored files remain separate from
  successfully downloaded files and no longer hold the run open.
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
    └── Example Artist 2026-08/
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

For new Windows testers, use the
[latest GitHub release](https://github.com/bora77/telegram-stl-manager/releases/latest)
and download **Telegram-STL-Manager-Windows.zip**. Extract the entire ZIP, then
double-click **Install.cmd**. It requests administrator approval automatically
and prepares its own WSL environment; no manual WSL configuration is needed.
Existing users should use the optional in-app updater or the separate
**Telegram-STL-Manager-Update.zip**, preserving their configuration and history.

For macOS and Linux Docker testing, use the separate
**Telegram-STL-Manager-Docker.zip** package. It includes a launcher, persistent
application storage and a host download-folder mapping. See the
[Docker setup guide](docker/README.md). Docker Desktop is required on macOS;
Linux can use Docker Engine with Compose. The Docker build targets x86-64 and
ARM64; real Mac validation is still pending.

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

During organizer Preview, loose files whose names and sizes match an existing monthly copy are marked for duplicate cleanup. Manual Apply verifies their contents with SHA-256 before removing only the loose copy. Different contents stay untouched and are flagged for review. Preview never deletes files.

Split archives with mixed names such as `release.7z.001`, `release.002`, and `release.003` are grouped together. Extraction uses temporary consistent names while preserving the original filenames; retrying an older organizer job also repairs this grouping.

The browser checks subscribed creators for available downloads at the interval saved in Configuration (four hours by default) while the tool is open; it never starts downloads automatically. The Linux x64 runtime package includes Python, the web server, SQLite, Pillow, Telegram CLI and 7-Zip. Run `start.sh`; Windows uses WSL2. See INSTALL.md for first-time account/source setup and supported platforms.

### MyMiniFactory

MMF is a beta feature, hidden by default.

**MMF Beta is for testers only: its features are incomplete and buggy, and should not be relied on for regular use.**
 Enable it in **Configuration → Beta
features**, then connect under **MyMiniFactory account**. The manager connects directly to MMF using an MMF username/email and
password. A separate MMF browser, extension, Playwright or AI agent is not needed
for checks and downloads. Google sign-in users may need to set an MMF password
through MMF's password-reset page first. Passwords are used for login only;
the private session stays in local application data. Reconnect if it expires.

Select artists from **Shared with me, Tribes and Frontiers**, assign each creator folder, choose a
starting month or **All (archiving)**, then save. Availability checks use the
configured interval while any manager page is open. They include new files
added to existing collections. Downloads start only when you click **Download
available releases**. **Resume unfinished** reuses the saved queue and local partial
files. One MMF transfer runs at a time in this initial implementation.

The release list follows the artist’s MMF folders, including monthly releases,
Welcome Packs and loyalty rewards. Named Frontier collections use the same full
workflow: extract images, create collage, make release, upload, and confirm publication.
Archive names follow the collection folder; later additions use numbered addenda.
Expand a release to see its files. **Make release**
produces one 7-Zip set named after that release. Configuration controls compression
and maximum part size, defaulting to level **7** and **4000M** (4000 MiB). Original member filenames are preserved under model/archive
subfolders to avoid collisions. Small releases use `.7z`; larger sets use
`.7z.001`, `.002`, and so on.

Original archives stage locally and are verified during delivery to `<creator>/<Artist YYYY-MM>/`, or an artist-prefixed named collection
folder such as `<creator>/<Artist Welcome Pack>/`. Unsafe filename characters are
sanitized for the filesystem. Images go into one flat `release_images` folder
inside the release. For ordinary releases without a month in their name, the MMF release folder’s
creation date supplies the destination month (`YYYY-MM`). Each named release
still produces its own archive set. The UI labels this inferred month; later
file updates never change it. Welcome Packs and loyalty rewards retain named
folders. Missing or invalid dates also keep the release name. Original archives are kept directly under `MMF sources`, preserving their filenames. Only conflicting names use a readable model-name subfolder; multipart archives stay together. Identical files are reused after a full-content comparison. **Re-download release** fetches fresh originals and extracts images again without repackaging or deleting history. Missing recorded archives are highlighted.
Source downloads stay local until archive/image delivery and database recording
succeed. Completed source versions and extraction receipts are recorded separately
from Telegram history. A changed release is rebuilt as a complete set, which can
require downloading its unchanged source archives again; other completed releases
are skipped.
Failed releases are reported and the run continues with the next release.

If a release was published outside the tool, expand it in MMF Queue and choose
**Make release → Just mark as finished**. Confirmation moves the current file versions to Finished releases
without uploading or deleting files, or claiming they were downloaded by this app.
New or updated source files reopen the release for the next addendum.

This integration covers archive downloads from Shared with me, Tribes and Frontiers.
Creators are merged by MMF creator ID, and duplicate models across sources are checked once.
Frontier pledges, add-ons and sign-up bonuses retain their collection names.
For Tribe deliveries without release metadata, the library delivery date supplies the month,
not the model creation date. Explicit release months take priority. A real Tribe
membership takes precedence over a duplicate Frontier entry for unrecorded files.
Opening **MMF → Artists** refreshes creators directly from MMF while showing the saved list immediately. Unsaved selections are preserved; no availability check or download starts.
Individual store purchases and standalone model/PDF downloads are not yet exposed.
MMF's website endpoints can change; Cloudflare or account verification can still
require reconnecting. Library access has been tested live; transfer and repackaging logic
are covered by local tests, and live speed testing remains to be done.

### Windows guided installation (test build)

The Windows ZIP includes an installer and launcher for a dedicated WSL environment.
Double-click `Install.cmd`, accept Windows approval/restart if needed, then use the
desktop shortcut. In Configuration, connect Telegram, import your TOC and choose
either a local folder or an optional network share. A mapped network drive is
not a local disk; use Network share and its UNC path. MMF is optional Beta,
hidden by default. First-time Configuration has four steps: **Telegram & artists
→ Download location → Preferences → Review & finish**. Click **Save and review**
in Preferences, then **Finish first-time configuration** on the final page.
Missing or unsaved settings block finishing and include links to the relevant
step. Other pages unlock only after validation passes. No manual WSL configuration is required. Installation, reboot continuation,
reset, reinstallation and uninstall were tested in a Windows 11 VM, including
preservation of download files. See `INSTALL.md` for validation scope and limitations.

The Table of Contents is the group’s artist-index message, containing links to
individual artist topics. In Telegram, check pinned messages or a topic called
“Table of Contents”, “TOC” or “Index”. Find the message listing artists, then
right-click it (or press and hold on a phone) and copy its message link. Paste
that into Configuration and click **Import artists**. Use the index message
link, not a group invite or an individual artist topic. If you cannot find it
or copy its link, ask the group administrator. Your connected account must
already have access to the group.

After installation finishes, close any remaining **Welcome to WSL** window and
completed installer PowerShell windows. The desktop shortcut starts the server in
the background with a Windows notification-area (tray) icon; no PowerShell window
needs to stay open. Double-click the icon to open the app, or right-click for
**Open Telegram STL Manager**, **View logs**, and **Stop and exit**. Windows may
place the icon under the notification-area arrow. Closing the browser leaves the
server and active transfers running; automatic availability checks still require
an open app page. Use the desktop shortcut again after restarting Windows.
Logs are saved under `%LOCALAPPDATA%\TelegramSTLManager\logs`. The app opens in
your normal Windows browser at `http://127.0.0.1:6093`.

The Git pre-commit hook increments the patch version in `VERSION` for each commit
and refreshes the version shown beside the app copyright. Enable the repository
hooks with `git config core.hooksPath .githooks` when cloning for development.

Copyright © 2026 trumphater77

### Preparing MMF months for Telegram

MMF Download and Re-download automatically extract archive images into `release_images`. **Extract images** can repeat this separately. If no images are found, choose **Download images from MMF** to fetch the gallery images. Next use **Create collage**, save it, then click **Make release** and choose a destination. Building archives and uploading require a valid saved collage; marking an external release finished does not. The app creates a verified 7-Zip set with volumes up to 4000M, an image folder and the saved collage, directly in the monthly folder. Intermediate MMF archives are retained in its `MMF sources` subfolder.

Preparation records which source file versions were included. Later additions or updated downloads use **Make Addendum 1**, then Addendum 2, and so on; unchanged files are excluded. A failed preparation retries the same number. Preparing successfully reserves that release number even before you upload it. Previously downloaded months remain eligible for update checks regardless of the subscription start month. The Make release dialog chooses what happens after preparation: upload to your release channel or run a bot conversation.

Gallery images are fetched only on request. Packaging uses the configured compression level and part size with two threads. Legacy verified 7-Zip sets can be reused only when their contents, compression, cleanup policy and part sizes meet the requested settings.

Prepared MMF releases offer manual Upload and Re-upload actions. Every attempt sends a valid release collage as a Telegram photo first, then the existing archive volumes, and verifies the resulting messages. Re-upload remains available after success, failure, deletion, or interruption; it creates a separate receipt without downloading MMF sources again or creating an addendum. Choose the destination in Configuration. An uncertain attempt may have delivered some messages, so check the Release Pad before retrying.

MMF **Open tasks** tracks publication separately from downloading or uploading. After the release bot successfully publishes a package, use **Confirm released** to move it to **Finished releases**. Direct bot delivery records completion only after the configured bot confirms publication. New or changed source files reopen their month for an addendum; previously published file versions remain recorded.

On the download page, **Download detected** uses the last availability check's exact file queue without scanning subscriptions again. **Check and download all** performs a fresh check of every saved subscription. Starting a detected run replaces its notification with the next scheduled check; subsequent checks can notify about new files. Each new checking cycle hides inactive organizer results from view while preserving their records and any running organizer job.

### MMF archive cleanup and PDFCleaner credit

MMF repackaging excludes known operating-system metadata such as `__MACOSX`, AppleDouble `._*` files, `.DS_Store`, Windows thumbnail databases and `desktop.ini`. Other hidden files, models, images, readmes and licences are retained. Only local working copies are processed; source archives stay intact.

PDF footer processing is a **modified adaptation of PDFCleaner by RC**. Credit for the original tool and its footer-stamp profiles belongs to RC. This integration uses [pypdf content operations](https://pypdf.readthedocs.io/en/latest/modules/generic.html) instead of the original desktop GUI and PyMuPDF pipeline. It recognizes the known bottom-left order stamp and dark footer badge/text patterns. It does not remove arbitrary white text, small images, document metadata, or unknown watermark types, and does not rasterize pages. PDFs without a recognized stamp remain byte-identical. Processing failures stop that release's repackaging and retain the source.

Repack receipts record changed PDF pages/profiles, before/after hashes and additional metadata files omitted. An archive created with an older processing policy is not reused for a new repack under this policy. Already delivered releases are not rewritten automatically.

### Source layout

Application modules live in `app/`, automated tests in `tests/`, and build and
packaging utilities in `tools/`. Windows integration stays in `windows/`; Docker
launchers stay in `docker/`. Local settings, databases and generated state remain
in `data/`, outside version control. Downloads keep their configured locations.

Start a bundled installation with `start.sh`. For source development, run
`python3 tools/build-catalog.py` and `python3 -m app.catalog_server` from the project
root. Run the Python test suite with `python3 -m unittest discover -s tests -t .`.

### Application updates

Managed Windows installations can check and install GitHub Releases from Configuration.
Updates are optional. A red **Update available** link beside the footer version
opens Configuration; it does not install automatically. Public releases need no
GitHub login or access token.
Updates preserve local settings and history, wait for active jobs and verify the
package checksum before installation. The visible updater backs up the previous
version and restores it if the startup check fails. Older installations can use
the standalone `Telegram-STL-Manager-Update.zip` once to gain this feature.
Extract it into a new folder and run **Update.cmd** with the same normal Windows
account, after finishing or stopping active jobs. No administrator elevation,
reset or reinstall is needed. Keep the window open until **Update complete**;
WSL's **The operation completed successfully** is only an intermediate message.
The updater blocks competing app launches while replacing files. Update logs
are under `%LOCALAPPDATA%\TelegramSTLManager\logs\update-*\update.log`.
Linux and Docker use the manual update procedures in their guides, preserving
application state and Docker volumes.
See [Updating an existing installation](INSTALL.md#updating-an-existing-installation).

Maintainers: build the runtime with `python3 tools/package-runtime.py`, then run
`python3 tools/package-update.py` and `python3 tools/package-windows.py`. Publish
`Telegram-STL-Manager-Update.zip` as an asset on a stable GitHub Release tagged
`vX.Y.Z` matching `VERSION`; the updater verifies GitHub's SHA-256 asset digest.
Publish the Windows installer alongside it for new users. Packages must pass the
public-file privacy check and must not contain local sessions or configuration.

Keep `README.md`, `INSTALL.md` and `AI-INSTALL-PROMPT.md` synchronized whenever
installation, dependencies, setup, startup, updates or recovery change. Review
the corresponding packaged instructions and Docker guide before building a
release; editing source documentation does not update already published ZIPs.

Network-share paths may include a trailing backslash, as copied from Windows
Explorer. Connection errors appear directly below **Connect and use this folder**.
If connection fails, correct the details and retry; the entered password stays
in the form for that retry and is cleared after a successful connection.

NAS connection diagnostics distinguish hostname resolution, access denial,
unreachable hosts, timeouts and SMB compatibility errors without displaying
raw credential-bearing output. For a hostname-resolution error, retry with
the NAS LAN IP address. Windows updates refresh the installed NAS helper;
rollback restores the previous helper, while saved share credentials remain intact.

After a successful NAS connection, Configuration reloads and shows the saved
network path and username with a saved-share indicator. The password field
is intentionally blank; it is not returned to the browser.

On Windows, NAS connection first resolves the name inside WSL, then tries the
Windows resolver automatically if needed. If both fail, the connection form
asks for the NAS LAN IPv4 address and keeps the share and subfolder unchanged.
Automatically resolved names are looked up again on reconnect; they are not
permanently replaced by a cached IP. A manually entered IP is saved in the path.

On both Telegram and MMF artist pages, leaving a creator folder blank when
saving uses the artist’s name below the configured download base. Characters
that are invalid in folder names are made safe. Explicit folder choices are
preserved; saving subscriptions does not start downloads or create folders.

### Choosing a release destination

**Make release** opens three choices: upload to **Your Telegram Release Channel**,
release directly through a bot, or **Just mark as finished**. Existing prepared
archives are reused when unchanged; new file versions become numbered addenda.
Channel delivery uses the destination configured in Configuration and still needs
publication confirmation afterwards. Marking finished only records the displayed
file versions and retains the files.

For bot delivery, enter its @username and the creator name exactly as shown by the
bot. The Eve quick flow selects the creator's **This Month** or **Last Month** button,
checks the upload prompt's creator and month, sends the collage and archives,
clicks **Complete**, and waits for **Upload done and saved.** before recording
publication. It supports creators visible in the opening menu. Other creators,
older months, named collections and bots with different menus need **Custom
conversation**. Do not choose a monthly quick flow for a welcome pack or campaign.

Custom conversations use one `send: message`, `click: exact button text`, or
`wait: reply text` step per line, separately before and after file upload. Supported
placeholders are `{creator}`, `{month}` and `{title}`. Enter the bot's final success
reply; completion requires a new matching reply. Commands and callbacks run without
AI. Bot settings are remembered locally. A missing or changed prompt stops the
attempt with an error; inspect the bot conversation before retrying, because some
files or the final command may already have reached it. No real release is sent
until you submit the dialog.
