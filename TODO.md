# TODO

## Upcoming features

These unchecked items appear on the What's new page, linked from the footer. Check an item off when it ships
to remove it from the upcoming list on the next page build.

- [ ] **STL ↔ DRC conversion** — Optional conversion in both directions while organizing releases, with configurable compression and precision.
- [ ] **PDF washer in the organizer** — Optional cleaning of supported PDF stamps, using the RC-based cleaner and preserving original archives.
- [ ] **Linux and Docker updates** — Install updates from the app while keeping accounts, settings and download history.

## Completed

- [x] Create the GitHub repository `bora77/telegram-stl-manager` and push the application source. Release publishing uses the version in `VERSION`.
- [x] Move Python application code, tests and maintenance utilities into `app/`, `tests/` and `tools/`.
- [x] Add the Windows tray launcher, log access and mapped-network-drive setup guidance.
- [x] Build and send Quartz a one-off **0.1.10** update package that preserves his existing configuration, sessions and history. Verify state preservation in the Windows VM.
- [x] Report unfinished Telegram and MMF downloads as errors instead of successful completion, with guidance to resume. Focused downloader/MMF tests passed (62 tests).

## Testing and follow-up

- [x] Diagnose the Windows delivery failure: WSL Windows drives reject `renameat2(RENAME_NOREPLACE)` with errno 22. Add a safe no-overwrite hard-link fallback for delivery and organizer moves, verified on the Windows VM.
- [x] Provide Quartz with a configuration-preserving update containing the latest fixes. He confirmed the v0.1.14 update and resumed downloads succeeded.
- [ ] Reload the local web application safely and verify the error display without interrupting active transfers. Running workers need a subsequent run to use updated worker code.
- [ ] Test loss of internet and recovery end to end: error visibility, preserved progress and successful resume without falsely reporting completion.
- [ ] Resolve the pre-existing full-suite test failures, including outdated release-folder expectations and the commit-hook test fixture. The focused tests pass; the full suite is not green.

## Organizer enhancements

- [ ] Add an optional DRC converter during organization, enabled only when selected by the user. Define the conversion format and handling of originals before implementation.
- [ ] Add an optional PDF washer during organization, reusing the existing PDFCleaner adaptation by RC from MMF repackaging. Preserve original archives, process staged copies, and record cleaning status. Test representative PDFs before enabling it for real releases.

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
- [ ] Add in-app installation for Linux runtimes and Docker. Manual update procedures are documented; the initial installer supports managed Windows/WSL only.
- [x] Test successful updates, interrupted updates and rollback before enabling distribution.

Status: Windows update installation and rollback are implemented and VM-tested. Updates remain optional and require an explicit Install update click. Older installations need the bootstrap Update ZIP once.
