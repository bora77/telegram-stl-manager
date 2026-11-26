# Version history

## 0.2.8

- Clarify the project's purpose: subscription downloads, release organization and optional personal archiving in a private Telegram channel. Documentation-only change; downloadable application packages remain at 0.2.7.

## 0.2.7

- Improve packaging-time PDF washing with page progress, signed-document protection and verification of saved content.
- Show the connected Telegram account name, with consistent account status styling and reconnect controls.
- Simplify configuration: contextual Beta labels, clearer STL cleaner explanations and upload-size warnings only above 4000 MiB.
- Remove MyMiniFactory's Beta label while retaining its visibility setting; Gumroad and STL cleaning remain Beta.
- Hide TOC help after a successful import and show a lighter Update artists button for subsequent imports.
- Add the Cults3D importer to Upcoming features.

## 0.2.6

- Add a default-on timestamp privacy option for PDF, JPEG, PNG and WebP files in newly generated release archives.
- Preserve original downloads, image quality and attribution while removing known date metadata from staged copies.
- Show the current file and progress during timestamp cleanup; retain sources if cleanup cannot safely complete.

## 0.2.5

- Retry transient download failures with visible reconnect status and bounded, cancellable waits.
- Resume HTTP transfers with validated ranges and server validators; refresh temporary download links without mixing changed file versions.
- Retain detected Telegram files after incomplete checks and preserve local sources when network-storage writes fail.
- Do not replay uncertain uploads or bot actions automatically.

## 0.2.4

- Load MMF/Gumroad queues from saved status without scanning storage; refresh only visible unfinished releases in the background.
- Skip filesystem checks for finished releases until an explicit action needs their files.
- Align availability checks to shared clock slots across all queues, independent of manual-check times.
- Use consistent next-check and interval status lines across providers.
- Caption dedicated collage images with their model/set name and mixed overviews as Collage; allow the type to be edited.

## 0.2.3

- Create dedicated featured collages for multi-image model sets and shared overviews for single-image models.
- Add editable collage cards, model/set titles, image labels and upload inclusion controls.
- Save explicit collage sets and upload all selected images before archives; forward the same set during bot delivery.
- Keep legacy single-collage releases compatible and reject incomplete or changed collage sets before upload.

## 0.2.2

- Add local, AI-free collage Auto-fill with duplicate removal, source-based grouping, quality preferences and editable fallback selections.
- Show analysis progress and selection explanations, with Shuffle and Undo controls.
- Automatically fill an empty collage when opening Create collage from a release queue; preserve existing drafts and saved images.
- Default new collages to square output and avoid repeating the artist name in generated titles.
- Preserve image provenance during extraction and gallery downloads to improve grouping.

## 0.2.1

- Give Telegram and MMF Auto-download controls the same switch design, aligned with the queue action row.
- Improve Off/On styling and mobile layout while preserving saved settings.

## 0.2.0

- Add independent, saved Auto-download toggles to the Telegram and MMF queues, both off by default.
- Automatically start detected downloads while a manager page is open, using each provider's existing check interval.
- Prevent duplicate automatic starts across tabs and leave failed or stopped runs for manual Resume. Packaging and publishing remain manual.
- Accept the full hostname of the configured local NAS in the mount verification check.

## 0.1.34

- Reorganize the README into platform features, release workflow, setup, troubleshooting and development sections.
- Clarify packaging, publication, addenda and source cleanup, and remove repeated explanations. Application behavior is unchanged.

## 0.1.33

- Use automatic 7-Zip threading by default, with a configurable fixed limit under Release packaging.
- Automatically route Welcome Packs through Eve's Welcome Pack category.
- Send numbered addenda through the original creator/year/month and Append to release, including older months.
- Verify addendum captions correctly when forwarding existing release-channel messages, including older upload receipts.

## 0.1.32

- Show file counts, bytes checked and speed during release input verification.
- Keep progress updates responsive while network storage is busy using a lightweight local status endpoint.
- Use Image-shaped rows as the default for new collages, while preserving saved layouts.
- Support single-image collages with uncropped, proportional preview and export.

## 0.1.31

- Show live source-cleanup progress, current file, verification speed and removal counts; publication confirmation returns immediately.
- Keep confirmation dialogs closable and show a clear timeout message when the server does not respond.
- Queue release workflows and upload to the release channel before anonymously forwarding to a bot; reuse verified channel uploads.
- Recover stale bot buttons and retry completion without resending delivered files.
- Include release gallery images inside prepared archives and exclude obsolete MMF archives from re-downloads and packaging.
- Preserve publication history, clean up verified sources after publication, and keep superseded failed attempts out of finished releases.
- Show archive preparation progress in the release queue and allow existing packages to be re-uploaded independently of new downloads.

## 0.1.30

- Filter MMF downloads by each file's update date, including additions to older collections, while preserving original release folders.
- Recognize existing archive history and show recorded files alongside new release files.
- Collect additional MMF gallery images even when archives already contain pictures.
- Skip invalid MMF file metadata without aborting availability checks.
- Restart unfinished Telegram transfers from zero and report unavailable file chunks more clearly.
- Include artist tag filters, optional STL cleaning, and the initial setup usage notice.


## 0.1.26

- Add an optional Gumroad Beta with account sign-in, verification codes, artist subscriptions, availability checks and manual downloads.
- Organize Gumroad downloads and images with the shared collage and release workflow, including new files added to older releases.
- Explain how Google sign-in users can set a separate Gumroad password for the connector.
- Apply the testers-only warning to all Beta features.
- Fix MMF image downloads with spaces or Unicode characters in their URLs.
- Compact the release filter and center pagination, showing 20 releases per page.

## 0.1.25

- Speed up MMF availability checks with four concurrent model requests and per-model progress, while continuing to detect updates to older releases.

## 0.1.24

- Store MMF originals directly in MMF sources, using readable folders only for filename collisions; preserve duplicate-named archives from the same model.
- Choose channel delivery, a bot conversation, or manual completion from the Make release dialog.
- Reuse prepared archives for re-upload and show bot conversation progress until publication is confirmed.

- Hide empty error messages in the release dialog.

## 0.1.23

- Allow releases published outside the tool to be marked as finished; new file versions reopen them for addenda.
- Enable the full packaging workflow for named Frontier collections, including numbered addenda.
- Align MMF release rows consistently and improve their mobile layout.
- Add a prominent testers-only warning beside MMF Beta activation and in the installation guides.

## 0.1.22

- Fix Tribe releases hidden by older duplicate Shared-with-me entries.
- Prefer actual Tribe membership over duplicate Frontier entries for unrecorded files.
- Use the MMF delivery month when Tribe release metadata is missing, preserving explicit release months and recorded destinations.
- Show when a release month comes from its MMF delivery date.

## 0.1.21

- Added MMF Tribes and Frontiers alongside Shared with me, with duplicate model detection and preserved download history.
- Refresh MMF creators automatically when opening Artists, preserving unsaved selections and showing saved artists if refresh fails.
- Default empty creator folders to the artist name on both artist pages.
- Keep navigation visible above an independently scrolling content area, including on mobile.
- Explain how to find and import the Telegram Table of Contents during setup.

## 0.1.20

- Automatically use Windows name resolution when WSL cannot resolve a NAS hostname.
- Ask for the NAS LAN IPv4 address when automatic resolution fails, preserving the share and subfolder.
- Resolve saved NAS names again on reconnect instead of pinning an automatically discovered address.

## 0.1.19

- Added specific NAS connection diagnostics without exposing raw credentials.
- Remember the network path and username after connection and show a saved-share indicator.
- Refresh the installed NAS helper during Windows updates and restore it on rollback.

## 0.1.18

- Accept network-share paths with a trailing backslash copied from Windows Explorer.
- Show NAS connection errors beside the connection form.
- Keep the entered password available for retry after a failed connection.

## 0.1.17

- Guided first-time configuration through four steps, ending with Review & finish.
- Blocked setup completion for missing or unsaved settings, with links to the relevant step.
- Updated installation guides and improved unavailable-folder validation.

## 0.1.16

- Added a footer link to version history and upcoming features, together on one page.

## 0.1.15

- Added a prominent latest Windows installer link to the project README.
- Updated the AI installation prompt for guided Windows setup, the tray launcher, optional updates and Docker.

## 0.1.14

- Prevented competing app launches while a Windows update replaces application files.
- Improved startup-failure diagnostics and clarified the update completion message.

## 0.1.13

- Added optional Windows updates through Configuration, with a red update notice beside the footer version.
- Added verified downloads, backups, startup checks and rollback for updates.
- Fixed saving verified files to Windows drives when the filesystem rejects the Linux rename operation.

## 0.1.12

- Show incomplete download runs as errors and retain guidance to resume unfinished work.

## 0.1.11

- Moved application code, tests and maintenance tools into dedicated subdirectories.

## 0.1.10

- Added the Windows tray launcher, browser shortcuts and access to logs.
- Clarified that mapped network drives must be configured as network shares.
