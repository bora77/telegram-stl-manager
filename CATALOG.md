# Telegram STL workflow

The start page is the manual download queue. Artists, Organize folders, Collages and
Configuration share the same navigation and logo. Installation and required
libraries are documented in [INSTALL.md](INSTALL.md).

## Private source and creator catalog

Each installation explicitly configures one Telegram source in private
`data/source.json` and allowed creator topics in private `data/creators.json`.
The source has no built-in name, ID or URL. Blank or invalid configuration blocks
content actions. Only configured catalog topics can be selected by the adapter;
returned chat, topic and attachment identities are checked before use.

The repository and source archive contain blank examples only. Private source
IDs, catalog contents, generated pages, screenshots and session notes must stay
out of Git and release archives. `distribution.json`, the Git ignore rules and
`tools/package-release.py` define the public files. The packager checks public
content for locally configured source references before creating an archive.

## Download a release

1. Set the destination in Configuration. Use a local Linux directory or a NAS
   mounted inside Linux; Windows installations use WSL2.
   Set **Target total download speed (MB/s)** to your connection’s practical
   capacity (default 28 MB/s). The gauge beside total progress shows combined
   speed. Adaptive mode adds a file after a 30-second observation window below
   95% of target, up to three transfers. It favors other data centers while
   keeping older work progressing. Completed releases can be extracted and
   moved before slower downloads finish. One file at a time disables additional
   parallel work; active transfers finish. These settings apply within five
   seconds to an existing run and never start a new run or impose a speed cap.
2. Refresh the server list after logging in to the CLI. Auto compares advertised
   Telegram endpoints for a suitable attachment and caches the fastest result.
   A manual endpoint can be selected per data center. Auto normally reuses
   comparisons for up to six hours. Configuration can instead compare before
   each artist/month and reuse the result across its files and archive parts.
   Connection setup means even short tests can add more than ten seconds.
   The optional **Retest when download speed stays below (MB/s)** setting requests a comparison
   after at least two minutes below the threshold. It uses a 30-second rolling
   average after a ten-second warmup and counts active download time across
   files on the same server. Extraction, moves and idle time are excluded.
   The counter survives release boundaries and long processing gaps; speed
   recovery resets it. Finishing a release does not reset the server choice.
   The current file finishes before the next suitable file triggers that test.
   Below half the threshold, Auto instead pauses the current file and compares
   immediately, then resumes using its saved chunks. At a 20 MB/s threshold,
   the immediate rule applies below 10 MB/s. Both rules use the same warmup
   and rolling average.
   Overlapping transfers use combined throughput for slowdown checks.
   Both slowdown rules have a ten-minute cooldown and keep the current server unless
   another measures at least 10% faster (or the current server fails its test).
   Zero disables this monitor; it only affects Auto selections.
3. In Artists, search the catalog, filter subscribed/unsubscribed creators, and
   choose the creator folder and scope in the same view. Save the selection.
4. Press **Download all subscriptions** on the queue page. It processes every
   eligible saved subscription; there is no background subscription scheduler.
5. Follow download, extraction, image transfer and archive move progress.
6. After Stop, a failure or a worker interruption, **Resume download queue**
   continues the same saved run. Each completed artist check has an atomic
   checkpoint; only unchecked or interrupted artist checks contact Telegram
   again. Completed identities are skipped and verified staged files are reused.
   Resume retains the original artists, scopes and folders, uses current server
   settings, and requires the same source and destination. Incomplete file
   transfers restart. A new **Download all subscriptions** run checks for newer
   posts; Resume does not add new posts to already checked artists.

Older stopped queues can recover their completed checks from download history
and exact cached attachment metadata. If an older creator's metadata is missing,
only that creator is checked again. Resume does not bypass a file error; resolve
the reported issue before retrying when necessary. No run resumes automatically.

Scope offers **From month/year** first and **All (archiving)** second. New
subscriptions default to the previous calendar month; the selected month remains
fixed and includes all later releases. Older subscriptions retain their saved
starting month. The app reads release months from filenames, not Telegram
posting dates. Unclear filenames stay as review items.

Archives download to local staging. Images are extracted locally and delivered
into a flat `creator/YYYY-MM/release_images/` folder. Archive delivery is verified
before completion is committed and local payloads are removed. Existing differing
files are never overwritten. Temporary staging is retained on failure for retry.
Unusual names inside an archive are mapped to safe local extraction names,
including Windows-style paths. Original archives and image bytes are preserved;
name changes are recorded with the extraction result. Links and ambiguous
metadata still require review.

SQLite history records Telegram attachment identities, exact sizes, transfer
metrics, destinations, archive checksums and image manifests. Later runs skip
completed identities even when the original Incoming paths no longer exist.

## Organize existing folders

Choose one artist and folder, then run **Preview**. A single CLI listing supplies
exact filenames and byte counts for matching local archives. Preview does not
move files, extract images or import download history. It shows proposed monthly
folders, matched files, size differences, duplicates and unclear filenames.
A fresh idle page starts with no artist selected. Returning in the same browser
tab restores the selected folder, preview and filter. A running operation restores
its artist, folder, progress and file table automatically, including a saved job
resumed after a newer preview was made. Changing the artist keeps other artists'
warnings hidden. Typing in Find artist selects a matching creator and updates its
folder. Preview reveals the results and Apply controls;
progress bars and Stop appear only during an operation. The table and counts
update as exact matches arrive and as files are moved and recorded. Paused jobs
keep a Resume control with concise errors and expandable diagnostic details.

**Apply ready files** is a separate manual action. It rechecks file identities,
verifies the destination mount, extracts images when selected, and moves matched
archives into monthly folders using atomic no-overwrite renames. Matched archives
already in monthly folders can also receive missing images.

After a release succeeds, its Telegram identities are recorded as downloaded
with origin `organizer`. This prevents later download runs from fetching those
files again, even if they move elsewhere. Organizer imports do not invent archive
hashes or historical download speeds. Existing completed download details remain
intact when images are backfilled. Unmatched or ambiguous files are not imported.

Stop and Resume use a durable job journal. Partial moves, delivered images and
uncommitted history can be recovered without repeating completed steps. Do not
transplant unfinished journals to a different machine or mount: their device,
inode and path identities belong to the original operation.
RAR part names with dot, underscore, space or hyphen separators are grouped
together. Resume repairs older per-part plans, preserving completed file counts,
history and image names; archive parts already moved into monthly folders are
reunited locally with their pending parts for extraction.

## Runtime and maintenance

The web server binds to localhost on port 6093 and validates Host/Origin headers.
It has no remote-user authentication. Keep it on localhost; use a deliberately
configured SSH tunnel if remote access is needed.

The runtime uses Python's standard library, the customized `.tools/tdl-stl/tdl`
binary, 7-Zip with a working RAR decoder, local staging and the configured
filesystem. Go is needed for builds only. No AI service is involved.

`python3 build-catalog.py` regenerates private local HTML/CSV from the catalog.
Shared navigation lives in `templates/app-layout.html`. A source/configuration
change must be coordinated with idle workers; a web-service restart does not
start or resume a batch. Avoid restarting the service during a real operation.

See INSTALL.md for QR login, optional systemd startup, Windows/WSL lifetime,
backups, migration and diagnostics. Never distribute private authentication,
source settings, catalogs, databases or jobs with application code.

## Verification

Run `python3 -m unittest discover -p 'test_*.py'` and
`node --test selection-rules.test.js`. Tests use synthetic source IDs, temporary
files and mocked Telegram calls. The Python suite covers scopes, exact metadata,
progress, cancellation, verified delivery, image extraction and organizer resume.
Go tests cover configured source validation and attachment identity checks.

Packaging checks cover the public-file list and private-source references.
Archive extraction and folder organization checks preserve source archives on
failure and reject changed files, symlinks and destination conflicts.
