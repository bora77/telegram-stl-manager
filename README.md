# Telegram STL manager

**A multipurpose manager for your 3D-print subscriptions, release collection and personal cloud archive.**

**[Download the latest Windows installer](https://github.com/bora77/telegram-stl-manager/releases/latest/download/Telegram-STL-Manager-Windows.zip)**
 · [Release notes and other downloads](https://github.com/bora77/telegram-stl-manager/releases/latest)

New users: extract the entire ZIP, double-click **Install.cmd**, and accept the
administrator prompt. The installer sets up WSL automatically; configure the app
in your browser afterward.

Its main purpose is to download releases you own from distribution platforms such
as **MyMiniFactory** and **Gumroad (Beta)**, keep track of new and updated files,
and organize everything by artist and release. It also supports downloading from
your configured Telegram artist catalog. Archives, extracted images, collages and
download history stay together on your local disk or network storage.

The same tool can prepare releases and upload them to **your own private Telegram
channel** for personal archiving, using Telegram's free, unlimited cloud storage.
Per-file upload limits still apply; the manager splits large releases into parts.
This makes it useful both for managing subscriptions and keeping a private cloud
copy of your collection. See [Telegram's storage and file-size limits](https://telegram.org/blog/700-million-and-premium).

The app runs locally and uses your own platform accounts. Everyday operation needs
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
| Direct MMF HTTP client | Python's HTTP and cookie libraries authenticate to MyMiniFactory, read the supported library collections and fetch files. Checks and downloads do not require a separate MMF browser window or Tampermonkey. |
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

The browser checks subscribed creators for available downloads at the interval saved in Configuration (four hours by default) while the tool is open; downloads stay manual unless Auto-download is enabled on that provider’s queue. The Linux x64 runtime package includes Python, the web server, SQLite, Pillow, Telegram CLI and 7-Zip. Run `start.sh`; Windows uses WSL2. See INSTALL.md for first-time account/source setup and supported platforms.

## Network interruptions and recovery

Transient read/download failures retry up to four times with visible reconnect
status and short, cancellable waits. Partial MMF/Gumroad downloads resume using
validated byte ranges and server validators when available; refreshed requests
resolve the current network route and obtain fresh download links. A server
that does not support ranges restarts that partial transfer from zero.
Changed-file, authentication and storage-capacity errors require review rather
than repeated retries. If recovery fails, the queue reports an error and retains
staged files for **Resume**. A failed Telegram availability check retains the
previous detections for affected artists and reports the check as incomplete.

Storage outages do not discard local staged originals or mark an unfinished
transfer complete. Restore the configured share, then Resume. The app does not
force-remount system shares. Uploads, forwarding and bot commands are not blindly
replayed after an uncertain response; check the destination before retrying.

## Optional automatic downloads

Availability checks use fixed clock slots in the application's time zone, shared
by every queue. A four-hour interval means 00:00, 04:00, 08:00, 12:00, 16:00 and
20:00; manual scans do not shift those slots. Fractional intervals use their
corresponding minute boundaries. The first check can run immediately; missed
slots are checked once when a browser page is open again. Docker uses
`STL_TIMEZONE` from its launcher environment for the application clock.

Telegram and MMF each have an independent **Auto-download: Off / On** toggle on
their queue page. Both default to Off and remember their saved state across
browser and server restarts. A manager page must remain open: existing browser
availability checks drive automatic starts, with no new system service or timer.
Enabling a toggle also permits starting already detected, unclaimed files.
MMF must be enabled and connected. Gumroad remains manual.

Turning a toggle off prevents new automatic runs without stopping an active
download. Failed or stopped queues require manual Resume instead of automatic
retries. Keep the download location accessible. MMF image extraction runs as
usual, but packaging, collages and publishing remain manual. Installation agents
must leave Auto-download off unless the user requests it.

## MyMiniFactory

### Connect and select artists

Enable MMF in **Configuration → Subscriber Distribution channels**, then connect under
**MyMiniFactory account** with your MMF username/email and password. Google
sign-in users may need to set a separate MMF password using MMF's password-reset
page first. Passwords are used for login; the session is stored privately on your
machine. No browser extension, separate logged-in browser or AI agent is needed.

In **MMF → Artists**, select creators, assign destination folders, choose a
starting month or **All (archiving)**, and save. Opening this page refreshes the
creator list without starting downloads or losing unsaved selections.

The integration supports **Shared with me, Tribes and Frontiers**. Creators are
merged by MMF creator ID, and duplicate models are checked once. Frontier pledges,
add-ons and sign-up bonuses retain their collection names. Individual store
purchases and standalone model/PDF downloads are not yet exposed.

### Check and download

The queue displays saved database status immediately. Finished releases do not
trigger storage or collage checks. File status for visible, unfinished releases
refreshes separately and is cached for a minute; image contents are not opened
just to display the list. Packaging, saving and uploading validate their selected
inputs when the action runs. MMF availability checks for new file versions are
separate from these local file-status checks.

**MMF → Queue** checks for availability at its configured interval while a manager
page is open. Downloads start manually by default; the queue’s Auto-download toggle can enable automatic starts. Use **Download detected** for files already
found, **Check for downloads** to refresh availability, and **Resume** for an
unfinished run. Failed releases are reported while the run continues with others.

The starting-month cutoff uses each file's **last-updated date**, so an update to
an older collection can still be detected. Undated files are excluded from
month-limited checks; **All (archiving)** includes them. Historical records are
retained even when they are outside the selected scope. Checks inspect older
models too, because their timestamps do not reliably indicate changes to attached
archives. Failed or stopped checks preserve the previous availability results.

### Release names and folders

The queue follows the artist's collections: monthly releases, Welcome Packs,
loyalty rewards and named Frontier collections. Expand a release to see its files.

- Monthly folders use **Artist YYYY-MM**.
- Ordinary releases without a month in their name use the MMF release's creation
  date. The interface identifies inferred months; subsequent file updates do not
  move the release to a different month.
- Tribe deliveries without release metadata use the library delivery date.
- Welcome Packs, loyalty rewards and undated collections keep their names.

Original archives retain their filenames under `MMF sources`. Only conflicting
filenames need a readable model subfolder; multipart archives stay together.
**Re-download release** retrieves fresh originals and extracts images without
packaging a release or deleting history. Missing recorded files are highlighted.

MMF's website endpoints, account checks or Cloudflare protection can change.
Reconnect if the saved session expires.

## Gumroad (Beta)

Gumroad uses the same artist selection, manual download, collage and release
workflow. It is hidden by default and intended for testers only. Gumroad features are incomplete and buggy; do not rely on them for regular use.

Enable **Show Gumroad** in Configuration, save, then connect with your Gumroad email
and password. If you normally sign in with Google, use **Forgot your password?**
on [Gumroad login](https://gumroad.com/login) to set a separate Gumroad password.
Enter any requested email or authenticator verification code.

Select purchased-library creators under **Gumroad → Artists**. The queue has its
own configurable availability-check interval, and revisits older products for
new files. Named products keep their names when no release month is supplied;
purchase dates are not treated as release dates. Membership updates can use their
publication month, and undated releases remain in scope.

Originals go into `Gumroad sources`; extracted images go into `release_images`.
The connector uses authenticated website JSON because the public seller API does
not expose the purchased library. It needs no browser-cookie export, seller API
token or additional background service. Sessions and purchase history stay in
private application data. Website changes or account verification can interrupt
this integration.

## Artist search and tags

On the Telegram and MMF artist pages, leaving the destination folder blank uses
the artist's name below your configured download base. Invalid filename
characters are made safe. Saving subscriptions does not start downloads or
create folders.

Telegram Artists also has a searchable, multi-select **Tags** filter. Tags come
from the bracketed descriptions in your configured Table of Contents. Selecting
several tags matches **any** of them, alongside the name and subscription filters.
The artist's Info panel shows its tags; **Clear** resets the tag filter.

Imports normalize duplicate spellings and exclude administrative notes. Rules
such as `5E → Dungeons&Dragons` and `WH40K → 40K` are reapplied on every import.
Tag assignments stay local.

## Preparing and publishing releases

MMF and Gumroad share this workflow:

1. **Download the release.** Originals are saved and archive images are extracted.
2. **Prepare images.** If needed, use **Download images from MMF** to collect the
   product gallery. Gallery downloads happen only when requested.
3. **Create and save a collage.** Images retain their proportions. New collages
   default to **Image-shaped rows**; choose **Slots → 1** for a single image.
4. **Make release.** Build a verified 7-Zip set and choose where to send it.
5. **Confirm publication.** Bot automation records success from the bot's reply.
   For a manual publication, confirm it in the manager.

A saved collage is required before packaging or uploading. Merely marking an
externally made release as finished does not require one.

### Auto-fill a collage

The collage editor's **Auto-fill** creates an editable set of collage cards.
Models/sets with several useful images get a dedicated collage, with the group
shot featured when a filename hint identifies one. Models with one useful image
are combined into shared overview collages. Each collage contains up to nine
images. Analysis runs locally without AI models or services.
Exact and near-duplicates are removed; resolution, color and group-shot filename
hints guide selection. Model archives, collection subfolders and model names
provide grouping evidence. Unresolved choices use a repeatable random fallback.
**Shuffle** tries alternatives; **Undo auto-fill** restores the previous grid.
**Create collage** from a release queue starts Auto-fill for an empty grid;
existing drafts are preserved. New collages default to square output.
Select a card to edit it. Model/set names appear in the picture title; overview
images have editable model labels underneath. Drag a different image into the
Featured slot to change the lead image. Cards can be added, removed, renamed or
excluded with **Include in upload**. **Save selected collages** saves the checked
cards and records exactly which JPGs belong to the release upload. Unselected or
older JPGs are not included just because they remain in the folder.
Analysis shows progress and a **Why these images?** explanation. Review the result:
unnamed group shots, component sheets and individual models inside a set cannot
always be distinguished by these rules. Only **Save collage** or **Save selected collages** writes the final JPGs.

### What gets saved

A typical release folder contains:

```text
Artist/
└── Artist YYYY-MM/
    ├── Artist YYYY-MM.7z
    ├── Artist-YYYY-MM.jpg
    ├── release_images/
    └── MMF sources/
```

The `.7z` is the prepared release, the `.jpg` is its collage, and
`release_images` holds the previews. The source folder contains the original
downloads used for packaging. Gumroad uses `Gumroad sources` instead.
For a collage set, filenames add the editable card name, for example
`Artist-YYYY-MM-Grinda Bikes.jpg` and `Artist-YYYY-MM-Release Overview.jpg`.
All selected collages are uploaded before the archive volumes. Bot delivery
forwards that same saved set; old or unrelated JPGs are excluded.
Photo captions include the release name followed by the model/set name for a
dedicated image, or **Collage** for a mixed-model overview. The editor's
**Photo caption** selector lets you choose the appropriate type for each card.

Release images are also included **inside the prepared archive**. Original model
filenames are preserved, with model/archive subfolders where needed to avoid
collisions. Large releases use numbered volumes: `.7z.001`, `.7z.002`, and so on.

### Packaging settings

Set these under **Configuration → Release packaging**:

| Setting | Default | Purpose |
| --- | --- | --- |
| Compression level | 7 | Balance compression time and archive size. |
| Maximum part size | 4000 MiB | Split large releases into uploadable volumes. |
| Compression threads | Auto | Let 7-Zip choose, or select a fixed CPU-thread limit. |
| PDF and image timestamp privacy | On | Remove known embedded dates from staged PDF, JPEG, PNG and WebP files. |
| STL cleaner | Off | Optionally process models before packaging. |

Running jobs retain their settings. Preparation shows its current stage, with
file and byte progress during input verification, compression and archive checks.
Generated release archives omit member timestamps; original downloads are not
rewritten.

Timestamp privacy applies only when making a new release archive, including its
release images. It removes known PDF dates and EXIF/XMP/IPTC/PNG date fields;
opaque camera MakerNotes are also removed. Image compression data, orientation,
colour profiles and attribution are retained. Staged file times are normalized;
archive member times remain omitted. Other file formats, existing prepared
archives and original downloads are unchanged. This reduces timestamp exposure,
not all possible identifying information. Signed/encrypted PDFs or malformed
metadata stop packaging with an error and leave sources intact. Progress shows
the current file; no additional dependencies or AI are required.

### New files and addenda

Packaging records the exact source versions included. New or updated downloads
become **Addendum 1**, **Addendum 2**, and so on; already packaged versions are not
included again. Failed preparation retries reuse the same number. A successfully
prepared package keeps its number even before publication.

An older release reappears under **Open tasks** when new file versions are found
within the subscription's selected scope. Previously published versions stay
recorded.

### Choosing a release destination

**Make release** offers three actions:

| Action | What happens |
| --- | --- |
| Upload to Your Telegram Release Channel | Upload the collage and archives to your configured channel. Publication is confirmed separately. |
| Release through a bot | Upload to your release channel first, then forward the messages anonymously to the bot and run its release conversation. |
| Just mark as finished | Record the displayed file versions as published elsewhere, without uploading or deleting files. |

Bot delivery reuses an existing verified channel upload when available.
It does **not** upload the same archive bytes directly to the bot again.

MMF and Gumroad share a persistent queue. You can queue **Build and upload** or
**Release through bot** while another release runs; preparation and delivery
follow in order. Uploads and bot conversations run one at a time. Waiting entries
show their position and can be removed with **Remove from queue**.

### Eve and custom bot conversations

Enter the bot's @username and select its conversation profile. Eve automation
supports creators visible in its opening menu:

- **Initial monthly releases:** select **This Month** or **Last Month** and verify
  the creator/month in the upload prompt.
- **Welcome Packs:** select the creator and **Welcome Pack**, then verify that
  category's upload prompt.
- **Addenda:** select the original creator, year and month, then **Append to
  release**. Older months are supported. Welcome Pack addenda use their category
  instead of a month.

Files are forwarded only after the expected prompt appears. The workflow clicks
**Complete** and waits for the bot's success reply before recording publication.
If an addendum's original release is not found, it stops instead of creating a
new monthly release.

Use **Custom conversation** for other bots, menus or unsupported release types.
Steps use `send: message`, `click: exact button text` or `wait: reply text`,
with `{creator}`, `{month}` and `{title}` placeholders. Configure the steps before
and after sending files, plus the final success reply. These conversations run
without AI; settings are remembered locally.

### Confirm, retry and clean up

**Uploading is not the same as publishing.**

If you publish manually from your release channel, click **Confirm released**
beneath the prepared archive. The confirmation modal records publication
immediately. If the entire release was made outside this tool, use
**Make release → Just mark as finished** instead.

After confirmed publication of a prepared package, cleanup verifies and removes
its unchanged source archives. It keeps the final release archive, collage,
images and download history. Changed sources or cleanup errors are retained for
review. Manual confirmation starts cleanup in the background; the queue shows
the current file, bytes checked, speed and removal count, even when finished
releases are filtered out.

If something fails:

- **Re-upload** sends an existing prepared package again without re-downloading
  its source files or creating another addendum.
- **Retry completion** retries only the bot's final step when all files were
  delivered. It does not resend them and stops if the conversation has changed.
- **Resume upload queue** continues untouched waiting entries after you have
  reviewed the failed attempt. Retry the failed release separately.

Check the Telegram conversation before retrying an uncertain result: a file or
final command may already have arrived. Closing the browser does not stop a
running worker. After a server restart, the upload queue waits for manual resume.

### Optional archive processing

Packaging excludes known operating-system metadata such as `__MACOSX`,
AppleDouble `._*` files, `.DS_Store`, Windows thumbnail databases and
`desktop.ini`. Other hidden files, models, readmes and licences are retained.

PDF footer processing is a **modified adaptation of PDFCleaner by RC**. Credit
for the original tool and footer-stamp profiles belongs to RC. The integration
uses [pypdf content operations](https://pypdf.readthedocs.io/en/latest/modules/generic.html)
to handle recognized order-stamp/footer patterns. It does not remove arbitrary
watermarks or rasterize pages. PDFs without a recognized stamp remain unchanged.
Processing failures stop packaging and retain the sources; receipts record
changes. Existing delivered releases are not rewritten automatically.
Washing happens during **Make release**, before compression, with file/page
progress. Saved page content is checked against the intended result; signed or
encrypted PDFs are refused. Text elsewhere on a page is not removed merely
because it shares the footer's height. Organizing does not wash PDFs.

Optional **Delicates-style** and **Cotton** STL cleaning operates on staged copies. Both STL cleaners are **Beta**, for testers only, and remain under Release packaging.
Both alter geometry and placement, can affect alignment between parts and
supports, and do not guarantee printability or watermark removal. Cotton also
reduces triangle counts and can remove detail. Keep cleaning **Off** unless you
understand those effects.

Delicates-style needs no extra dependency. Cotton requires separately supplied,
trusted Linux binaries matching the application's architecture; they are not
bundled. For setup, executable paths and processing limits, see the STL-cleaning
instructions in [INSTALL.md](INSTALL.md).

## Setup, startup and updates

For complete steps, use [INSTALL.md](INSTALL.md), the
[AI installation prompt](AI-INSTALL-PROMPT.md), or the
[Docker guide](docker/README.md).

### Windows startup

The Windows package installs a dedicated WSL environment. Double-click
`Install.cmd` and follow the visible administrator/restart prompts. No manual WSL
configuration is required.

First-time configuration has four steps: **Telegram & artists → Download location
→ Preferences → Review & finish**. Save the preferences and finish the final
review to unlock the other pages. Missing or unsaved settings are shown with
links to the relevant step.

After installation, close any leftover **Welcome to WSL** or completed installer
PowerShell windows. The desktop shortcut starts the app with a notification-area
icon; no terminal needs to remain open. The tray menu provides **Open Telegram
STL Manager**, **View logs**, and **Stop and exit**. Use the shortcut again after
restarting Windows. Logs are under `%LOCALAPPDATA%\TelegramSTLManager\logs`.

Closing the browser leaves the server and active transfers running. Automatic
availability checks require an open manager page.

### Finding your Table of Contents

Use your Telegram group's artist-index message, often pinned or in a topic named
“Table of Contents”, “TOC” or “Index”. Copy the link to the message that lists
artists and paste it into Configuration → **Import artists**.

Use the index-message link, not an invite link or an individual artist topic.
Your Telegram account must already have access. Ask the group administrator if
you cannot find or copy the index link.

### Updating

Updates are optional. In managed Windows installations, the red **Update
available** link beside the footer version opens the update controls. Public
GitHub releases require no GitHub login or token.

Finish active tasks first. The updater preserves settings and history, verifies
the package checksum, backs up the previous version and restores it if startup
fails. Older installations can use `Telegram-STL-Manager-Update.zip`: extract it
into a new folder and run `Update.cmd` with the same Windows account. No reset,
reinstall or administrator elevation is needed.

Keep the updater open until **Update complete** appears. WSL's **The operation
completed successfully** is only an intermediate message. Update logs are under
`%LOCALAPPDATA%\TelegramSTLManager\logs\update-*\update.log`.

Linux and Docker use their documented manual update procedures, preserving
application state and volumes. See
[Updating an existing installation](INSTALL.md#updating-an-existing-installation).

## Troubleshooting

### Network shares

A mapped Windows network drive is **not a local disk**. Choose **Network share**
and use its UNC path. Paths copied with a trailing backslash are accepted.

Connection diagnostics distinguish hostname-resolution problems, access denial,
unreachable hosts, timeouts and SMB compatibility errors. On Windows, the app
tries WSL's resolver, then Windows' resolver. If both fail, it asks for the NAS's
LAN IPv4 address. Automatically resolved names are looked up again on reconnect;
a manually supplied IP is saved in the path.

After connection, Configuration shows the saved path and username. The password
field is blank because saved passwords are not returned to the browser. A failed
connection retains the entered password for that retry; success clears it.
Update and rollback preserve saved share credentials.

### Telegram file errors and interrupted downloads

`LOCATION_INVALID` means Telegram rejected a file-location request. The downloader
tries smaller requests for the affected range and reports the failing offset if
recovery fails. The file stays incomplete. Try **Resume** later, or try the same
attachment in Telegram; if it also fails there, request a new upload.

To restart one unfinished file, stop the run and choose **Restart from zero** on
that file. **Restart unfinished** resets all unfinished files in the saved run.
Neither action rescans artists or repeats completed downloads. Old local staging
is retained under `redownload-backups` rather than reused.

## Development and packaging

All availability schedulers, including new provider queues, must use the shared
`app.check_schedule.next_check_at` helper rather than adding an interval to the
last manual check timestamp.

Application modules are in `app/`, tests in `tests/`, and build tools in
`tools/`. Windows integration is in `windows/`; Docker launchers are in
`docker/`. Private settings, sessions, databases and generated state remain in
`data/` outside version control.

For source development, build the pages and start the server:

```bash
python3 tools/build-catalog.py
```

```bash
python3 -m app.catalog_server
```

Run tests with `python3 -m unittest discover -s tests -t .`. Enable the repository
hooks with `git config --local core.hooksPath .githooks`; the pre-commit hook checks
public-file privacy and increments the patch version shown in the app footer.

To build distribution packages, run `tools/package-runtime.py` first, then
`tools/package-update.py` and `tools/package-windows.py`; use
`tools/package-docker.py` for the Docker bundle. Stable GitHub releases use a
`vX.Y.Z` tag matching `VERSION`. Publish the installer, update and Docker packages
with their checksums. Public packages must not contain local sessions or settings.

Keep `README.md`, `INSTALL.md`, `AI-INSTALL-PROMPT.md` and affected Docker/package
instructions consistent when installation or recovery behavior changes.
Documentation edits do not update already published ZIPs.

## Usage notice and credits

The software uses your own accounts to access files you are entitled to download.
Access does not grant redistribution rights. Private Telegram uploads are intended
for personal cloud backup where permitted by law and the content licence. Sharing
requires permission or another lawful basis; “free” or “freeware” alone does not
establish that permission.

Installation and first-time browser setup each require explicit acknowledgment
of the usage notice. AI installation agents must leave this acknowledgment to
the user. The notice covers independent backups, possible file loss or damage,
free-of-charge software supplied **AS IS**, warranty exclusions and liability
limits subject to applicable law. Read [USAGE-NOTICE.txt](USAGE-NOTICE.txt).

PDFCleaner adaptation: original tool and footer-stamp profiles by **RC**.

Copyright © 2026 trumphater77
