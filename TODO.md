# TODO

## Upcoming features

These unchecked items appear on the What's new page, linked from the footer. Check an item off when it ships
to remove it from the upcoming list on the next page build.

- [ ] **Cults3D importer** — Import owned Cults3D downloads into the manager, with artist/release organization and download-history tracking. Investigate supported account access and download methods before implementation.

- [ ] **STL ↔ DRC conversion** — Optional conversion in both directions while organizing releases, with configurable compression and precision.
- [x] **PDF washer during Make release** — Clean recognized footer stamps on staged PDFs before release compression, preserving original downloads. Show file/page progress and verify saved content; no organizer washing.
- [x] **PDF and image timestamp privacy** — Configurable, default-on cleanup of known date fields in PDF/JPEG/PNG/WebP staged contents during Make release, with file progress, lossless image handling and normalized staged times. Generated archives omit member times; original downloads and existing prepared archives remain unchanged. Other formats and arbitrary identifying marks are outside this feature's scope.
- [x] **Simplified Docker updates (Linux and macOS)** — Included in 0.2.9: verified updates, preserved state, corrupted-package rejection, rollback and interrupted-update recovery. Docker control stays on the host; the app receives no Docker socket or privileged host access.
- [ ] **Packaging on another device** — Optionally run archive preparation, compression and verification on a more powerful computer, with shared-storage access, progress reporting and recovery after connection interruptions. Keep release management and uploads in the main app.

## Completed

- [x] Create the GitHub repository `bora77/telegram-stl-manager` and push the application source. Release publishing uses the version in `VERSION`.
- [x] Move Python application code, tests and maintenance utilities into `app/`, `tests/` and `tools/`.
- [x] Add the Windows tray launcher, log access and mapped-network-drive setup guidance.
- [x] Build and send Quartz a one-off **0.1.10** update package that preserves his existing configuration, sessions and history. Verify state preservation in the Windows VM.
- [x] Report unfinished Telegram and MMF downloads as errors instead of successful completion, with guidance to resume. Focused downloader/MMF tests passed (62 tests).

## Testing and follow-up

- [x] Implement update notices with all release notes since the installed version, once per new version, optional reminders and safe text rendering; included in 0.2.9.
- [x] Reorganize browser assets into `web/`, supporting guides into `docs/`, HTML fragments into `templates/`, and generated pages into private `data/pages/`.
- [x] Retain historical private machine notes and the obsolete desktop launcher under `data/notes/` and `data/legacy-desktop/`, outside public packages.
- [x] Add public README screenshots with demonstration data and explain detection of later supported models, bonus files and corrected files in older releases.
- [x] Credit RC for the PDF cleaner, Anne O'Nymous for the STL washers and Quartz for testing.
- [x] Prepare the 0.2.9 release with the Docker updater, update notices, repository cleanup, refreshed installation guides, credits and screenshots; final package verification is part of publication.

- [ ] Add a separate thank-you credit for tester Goldfinger; Quartz and RC are already credited above.

- [x] Diagnose the Windows delivery failure: WSL Windows drives reject `renameat2(RENAME_NOREPLACE)` with errno 22. Add a safe no-overwrite hard-link fallback for delivery and organizer moves, verified on the Windows VM.
- [x] Provide Quartz with a configuration-preserving update containing the latest fixes. He confirmed the v0.1.14 update and resumed downloads succeeded.
- [x] Reload the local web application safely without interrupting active transfers. Browser checks cover setup, connected-account display, configuration controls and update notices; real outage error-display validation remains below.
- [ ] Test loss of internet and recovery end to end: error visibility, preserved progress and successful resume without falsely reporting completion.
- [x] Add controlled recovery tests for dropped HTTP connections, byte-range resume, expired download links, changed validators, bounded reconnects, cancellation, uncertain Telegram writes and disconnected-storage cleanup. These tests do not disconnect the user's network.
- [ ] Revalidate and resolve the remaining pre-existing full-suite failures, including outdated release-folder expectations. The commit-hook fixture is corrected and its tests pass; a full-suite rerun is still outstanding.

## Organizer enhancements

- [ ] Add an optional DRC converter during organization, enabled only when selected by the user. Define the conversion format and handling of originals before implementation.
- [x] Keep PDF washing in release packaging, not organization, as requested. Reuse the RC-based cleaner on staged copies and record changes in packaging receipts.

## Self-updater

- [x] Choose the source and release repository: `bora77/telegram-stl-manager`.
- [x] Decide distribution access: public GitHub source and releases, without a website or announcement. No user token is needed for the default release source.
- [x] Publish versioned release packages using GitHub release metadata and SHA-256 asset digests. Keep credentials, sessions, history and private test packages out of public distribution.
- [x] Check daily while the browser is open; show a red **Update available** link beside the footer version and controls in Configuration. Installation starts only when the user clicks **Install update**; users do not need Git installed.
- [x] Wait for downloads, organizing, packaging and other active jobs to finish before updating, and prevent new jobs during installation.
- [x] Download and verify the update package before applying it.
- [x] Back up the database, settings and previous application version; handle database migrations and restore the matching backup if startup fails.
- [x] Replace application files, restart the server and verify that it starts successfully.
- [x] Preserve sessions, subscriptions, history and downloaded files. Never replace or delete external storage.
- [x] Update Windows installations inside their existing WSL environment without reinstalling Ubuntu.
- [x] Implement the host-side Docker update launcher: verify package digest/version, build before downtime, block new work and wait for active tasks, copy state to a new volume, preserve mappings, retain the original image/volume, roll back on failure and recover interrupted transactions. Verified in the Ubuntu x86-64 VM; native macOS/ARM64 remains unverified. Windows/WSL keeps its existing in-app updater; development checkouts are excluded from package replacement.
- [x] Test successful updates, interrupted updates and rollback before enabling distribution.

Status: Windows update installation and rollback are implemented and VM-tested. Updates remain optional and require an explicit Install update click. Older installations need the bootstrap Update ZIP once.
