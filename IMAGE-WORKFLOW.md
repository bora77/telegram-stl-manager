# Organizer extraction and release collages

Updated 2026-09-13. Image extraction is implemented for downloads and the
organizer's separate manual Apply action.
The Collages page arranges extracted images and saves a full-resolution JPEG on demand.

## Extract images when organizing

**Extract images** is checked by default beside **Apply ready files**. The
organizer also includes matched archives already in monthly folders so it can
fill in missing images, without another Telegram download.

When image data is damaged, the organizer keeps members that decode and pass
their archive checksum, skips unreadable members, and continues organizing.
It can rebuild a damaged ZIP index in a temporary copy using `zip -FF`; original
release archives are never repaired or modified. Warnings remain visible after
completion and are saved with the release in the organizer journal and download
database. Other release failures (including missing parts, unsafe paths, and
unreadable archives) mark that release **Needs review** and continue with the
next release. Failed files are not claimed as downloaded. Known archive parts
must all match the preview before any part is moved. Cancellation and a changed
or disconnected destination mount stop the whole operation. Download extraction
retains its existing strict behavior.

All newly delivered images go directly into:

`artist/YYYY-MM/release_images/<image name>__<stable suffix>.<extension>`

The suffix is derived from the archive identity and original member path. This
keeps repeated image names distinct across nested folders and archives. Original
image bytes are preserved; source paths remain in the database manifest. Existing
older nested image folders are not deleted by this feature.

The fast preview compares exact filenames and byte counts from one CLI scan.
Apply revalidates the saved file identities and the mount, extracts locally,
verifies image delivery, renames original archives within the same share without
overwriting, then records completion in SQLite. Split archives spanning flat and
monthly folders use temporary local copies when required for extraction.

Organization can run alongside downloads. Each worker has its own Stop control.
Only work targeting the same artist/month waits for the other worker. Downloads
recheck history after waiting and skip files the organizer has completed. If a
download has already delivered an identical archive, the organizer verifies both
checksums before removing the redundant flat copy and retains the completed
download record. Different contents remain a conflict. Preview may wait for the
current Telegram transfer; extraction and folder moves do not hold the CLI login.

A durable journal supports stopping and resuming after partial moves, verified
image delivery or a failed database commit. Current operation and per-release
progress appear in the page. Renames show file counts, image transfers show
bytes/speed, and extraction shows image counts and elapsed time.
Release errors stay visible in **Needs review** and the completion warnings.
**Retry skipped releases** retries only unfinished releases, retaining verified
images and partial moves. If an original file is repaired or replaced, run a
fresh preview so its new identity and size can be checked. Completed work and
download records remain intact.

For a skipped split release, **Download missing/incomplete parts & retry** can
fetch the exact missing or size-mismatched parts from the saved Telegram preview.
It shows the filenames and download sizes before starting. This is a separate
manual action; ordinary Retry does not authorize new repair downloads. No new
channel scan is needed. Ambiguous metadata and changed local originals require
a fresh preview.

Repairs use the existing CLI and server selector, with download and NAS-transfer
progress and speed in the organizer. Each incomplete original is preserved in
`data/organizer-repairs/<job>/<message>/previous/`, with its checksum recorded in
the private job journal, before replacement. Verified new parts are delivered
into the monthly folder, and the release is organized and its images extracted.
Download timing and the new file checksum remain in download history. Interrupted
repairs retain their verified download and backup for manual retry; staging is
removed only after the release history commits. The backups are retained locally.

CLI metadata provides actual Telegram message IDs. Existing matched files can
therefore enter normal download history with origin `organizer`, exact names and
sizes, plus a separate organization record. Full archive hashes and transfer
speeds are not fabricated. Existing completed download details are retained when
images are backfilled.

Split 7z volume/header checks avoid a full decompression test of unrelated model
data before image extraction. Other split formats retain integrity checks and
report progress. Extraction speed depends on archive layout and compression.

See [CATALOG.md](CATALOG.md) for operation and verification details.

## Collage workflow in the web interface

1. Open **Collages**, choose an existing artist folder and release month, and
   click **Open images**. No artist is selected initially. Only local release
   images are read; this does not access Telegram.
2. Drag images from the gallery into the collage's numbered slots. Drag between
   occupied slots to swap images. Dropping a new image replaces that slot;
   dragging an image already used elsewhere swaps its position without duplicates.
   Alternatively, click a slot then **Place** under an image. Previous/Next and
   Remove image buttons work with the keyboard and on touch devices.
3. Choose 5–9 slots, Featured image, Image-shaped rows or Equal grid. The first
   slot is the lead image in Featured image. Available outputs are landscape
   3840×2160, portrait 2160×3840 and square 3000×3000. Choose a light/dark background
   and an optional title. Fill every slot to generate a clean preview.
4. Drafts save locally in SQLite. Gallery thumbnails include dimensions, filename
   search, archive grouping when an extraction manifest exists, and a larger view.
   Previews and exports use the same geometry. All images retain their proportions
   and complete contents, with EXIF orientation corrected and transparency handled.
   Small source images produce a resolution notice.
5. Click **Save to Kronos** to render and deliver a new JPEG to the configured
   download folder at `artist/YYYY-MM/`, beside the release archives, named
   `ARTIST-YYYY-MM.jpg` (for example `Example Artist-2026-08.jpg`). Saving a changed
   collage replaces the previous saved collage after keeping a verified local
   backup. The page shows export status and transfer progress; failed
   exports offer **Retry save**. Completed versions offer **Download JPEG**.

Opening images, arranging slots and previewing do not write to the release folder.
Original extracted images are preserved. Old nested release_images folders remain
supported; generated collages are kept outside the source image gallery.

## Implementation and recovery

The Python server uses Pillow locally, with no AI service or Telegram interaction.
Install `python3-pil` and `fonts-dejavu-core` as described in INSTALL.md. JPEG, PNG
and WebP originals are supported. Other extracted formats are shown as unavailable.
Images are bounded to 40 megapixels and 64 MiB; scans allow up to 5,000 images.
The server uses image identifiers and pinned directory handles, rejects symlinks
and paths outside the selected release, and verifies original identities and
checksums before exporting. An original changed since the preview requires a new
preview. Selected images never duplicate within a collage.

Private state lives in `data/collages.sqlite3`; thumbnails, previews and unfinished
JPEG exports live in `data/collages/`. Saved recipes retain image IDs, checksums,
order, layout, output size and title, plus verified output checksums and filenames.
Draft revisions protect against silently replacing edits from another browser tab.
Previous JPEG versions are retained under `data/collages/versions/`, deduplicated
by checksum, and remain downloadable from Saved versions. Back up this local
folder with the collage database if you want to preserve older versions.
A single export worker runs at reduced process priority. Failed delivery retains
its local JPEG for an explicit retry, which rechecks the originals. The existing
verified delivery code rejects unrelated destination files. A replacement journal
allows recovery between moving the previous collage aside and installing its
replacement; failed delivery restores the previous collage when possible.
Export history remains
recorded even when someone later moves the saved JPEG elsewhere.

Run `python3 -m unittest test_collages -v` for temporary local fixture checks of
geometry, complete portrait/landscape fitting, transparency, EXIF rotation, manual
exports, changed originals, path guards, interrupted jobs and delivery retries.
