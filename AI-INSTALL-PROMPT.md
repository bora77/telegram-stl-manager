# Prompt for an AI agent: install Telegram STL Manager

Checked against the **v0.1.14** installer and updater on **2026-09-18**.

Copy the prompt below into an agent with terminal/filesystem access on the
**destination machine**. Supply the release package or repository and its
`INSTALL.md`; Docker users also need `docker/README.md` (the Docker package's
`README.md`). Leave unknown preferences blank. Never include passwords, login
codes, QR tokens, session files or private account data in the prompt.

Project: https://github.com/bora77/telegram-stl-manager
Latest release: https://github.com/bora77/telegram-stl-manager/releases/latest

---

Install the existing Telegram STL Manager on this machine. Follow the matching
release's `INSTALL.md` and packaged instructions. This is installation and
configuration, not a request to rewrite the application. Prefer the packaged
installer over a source build. Complete all independent setup, let me perform
required local login/admin steps, and verify the installation.

My preferences:

- Release: [latest stable release, or a specified version/package].
- Operating system and architecture: [detect if unspecified].
- Installation: [new installation, or update an existing installation].
- Download destination: [local folder, or network share and folder].
- Optional MyMiniFactory Beta: [disabled unless requested].
- Existing data: [preserve current settings/history, or a supplied backup].

I authorize installing the required software for the chosen route and checking
my destination using a uniquely named temporary test directory. Reuse answers
already supplied. Ask for missing information, local administrator/login steps,
or a reboot that could interrupt other work. Do not ask me to paste secrets into
chat. Do not uninstall, reset, delete a data volume or replace existing private
state to fix an installation error.

## 1. Choose the correct route

**Windows Intel/AMD x64 — default for Windows testers**

- New installation: obtain `Telegram-STL-Manager-Windows.zip` from the official
  release, verify its published checksum, and extract the entire ZIP into a new
  folder. Double-click `Install.cmd` as the normal Windows user. It requests
  administrator approval automatically; no manual right-click elevation is
  needed. I must approve the Windows prompt.
- The installer enables WSL when necessary, downloads/imports its own Ubuntu
  environment named `TelegramSTL`, installs dependencies and creates shortcuts.
  If a reboot is required, coordinate it with me; setup continues at sign-in.
  Do not separately install Ubuntu, create a Linux user, edit fstab, build Go,
  install Python or install a database/web-server service for this route.
- This is a Windows launcher backed by WSL2, not a native Windows executable.
  Preserve unrelated WSL distributions and global settings. The package is an
  unsigned test build. Do not disable Windows security protections.
- Keep installation output visible. Leave `support/` intact and do not run its
  internal scripts directly. After installation completes, close leftover
  Welcome to WSL and completed installer PowerShell windows.
- Use the desktop shortcut to start the app. Its tray icon keeps the server
  running without an open PowerShell window. Double-click the tray icon to open
  the browser; right-click for View logs or Stop and exit. The icon may be under
  the notification-area arrow. Start the shortcut again after restarting the PC.
- Existing installation: use the app's optional updater, or the separate
  `Telegram-STL-Manager-Update.zip` and `Update.cmd`, as described below. Do not
  run a reset or treat an update as a fresh installation.

**Linux x86-64 — bundled runtime or Docker**

- If supplied the Linux runtime archive, verify its checksum, extract it into a
  writable folder and run `bash start.sh`. Use its bundled Python, SQLite,
  Pillow, pypdf, Telegram CLI and 7-Zip/RAR runtime. Do not install duplicate
  Python, database, web-server or Go packages. Follow `INSTALL.md` for host Linux
  utilities and optional network mounts.
- Docker is also supported on Linux. Follow the Docker package guide and keep
  its persistent volume, rather than running source-install commands on the host.
- Start in the foreground unless I request another startup method. Do not add
  a system service or scheduled task merely to check for available downloads.

**macOS / Docker**

- Use the Docker tester package and its guide. macOS uses Docker Desktop;
  Linux uses Docker Engine with Compose. Obtain missing releases from the
  maintainer rather than inventing an image name or downloading an unrelated app.
- Run `Start.command` (or `bash Start.command`), choose a writable host folder,
  and allow required Docker folder access. Keep `/downloads` as the app's base;
  it maps to that chosen host folder. The package builds the required runtime.
- Keep build output visible. Closing the attached log window does not stop the
  container; use `Stop.command`. Preserve `telegram-stl-manager-data` during
  updates. Never use `docker compose down --volumes` for a routine update.
- Mount network shares on the host before passing them to Docker. Confirm the
  share is actually connected. Do not represent Mac/ARM or NAS behavior as
  tested on this machine unless it has been verified here.

**Source installation — only when requested or needed for the platform**

Follow the source-build sections of `INSTALL.md`, including the matching Python,
Pillow, pinned pypdf, Go, custom Telegram CLI and 7-Zip/RAR requirements. Preserve
`.tools/tdl-stl/src`, `.tools/tdl-stl/gotd`, workspace/lockfiles and licenses; a
stock tdl binary is not a substitute. Use `tools/build-tdl.sh`. Check actual RAR
decoding, not just archive listing. Use `tools/init-local.py` and
`tools/build-catalog.py`, then `python3 -m app.catalog_server`. Do not build or
install these components separately when the selected package already includes
them. Do not install OCR, VNC, Telegram Desktop or an AI API as dependencies.

## 2. Complete setup in the browser

Open `http://127.0.0.1:6093` in the normal host browser. A fresh installation
allows only Configuration until setup is complete:

1. Connect my own Telegram account using the app's login workflow. I enter any
   code or password locally; never expose session files or authentication data.
2. Configure my private Table of Contents/source and import its artist catalog
   through the UI. Explain that the TOC is the artist-index message with links
   to artist topics. Help me find it in pinned messages or a Table of Contents,
   TOC or Index topic. I copy the message link by right-clicking (or pressing
   and holding on a phone), paste it into Configuration, then click Import
   artists. Use the index message, not a group invite or individual artist topic.
   If it cannot be found or its link copied, ask the group administrator; my
   connected account must already have access.
   No Telegram group or catalog is built in. Do not enumerate,
   join, message or scrape other chats as an installation test.
3. Choose Local folder or Network share. Windows accepts local Windows paths;
   a mapped network drive is **not** a local disk, regardless of its drive
   letter. Use Network share and the UNC path for NAS storage. Enter credentials
   locally in Configuration. Do not assume a particular server, username or
   mount point. For Docker, follow its host-mount procedure instead.
4. Follow the four setup steps: Telegram & artists → Download location →
   Preferences → Review & finish. Click Save and review in Preferences, then
   Finish first-time configuration on the final page. Resolve every missing
   or unsaved setting listed there using its link back to the relevant step.
   Other pages stay locked until validation passes. MMF is optional Beta,
   hidden by default; connect that account if I choose to enable it.

Keep application state, SQLite and staging in the package-managed Linux
filesystem or Docker volume. Do not move them onto the network destination.
Preserve destination identity checks, verified delivery and no-overwrite
protection. Test writes only in a disposable test directory, never against real
creator files. Never delete the configured download destination during setup,
update, troubleshooting or uninstall.

## 3. Verify normal operation without starting content jobs

- Confirm the web page loads and the displayed version matches the chosen
  release. Verify Telegram Queue/Artists, Organize folders, Collages and
  Configuration; MMF navigation appears only if its Beta is enabled.
- MMF supports Shared with me, Tribes and Frontiers archive downloads. Opening
  MMF Artists refreshes creators directly from MMF, showing saved artists immediately
  and preserving unsaved selections. It does not start a file check or download. Frontier collections keep their names.
For Tribe deliveries without release metadata, the library delivery date supplies the month,
not the model creation date. Explicit release months take priority. A real Tribe
membership takes precedence over a duplicate Frontier entry for unrecorded files.
- Confirm saved configuration and any existing subscriptions/history survive.
  Fresh installations must contain no previous user's sessions or private data.
- Downloads and organization start manually. Do not click Download detected,
  Resume, Apply, upload or publish as a test unless I authorize that actual
  content operation and scope. A download can process multiple subscriptions.
- Availability checks are metadata-only and triggered by an open browser at
  the configurable interval. They do not start downloads. Telegram and visible
  MMF queues have their own checks. Do not add cron, Task Scheduler or a daemon
  to perform recurring checks. Closing the browser does not stop a running job.
- Release folders use the application's current artist/month naming and retain
  named packs/bonuses. Images go in the release's flat `release_images` folder;
  do not introduce a different layout during installation.
- Keep localhost binding and Host/Origin checks. Do not expose the app to the
  LAN/Internet to work around browser access problems.
- Use temporary fixtures for any source-build tests. Report the tests actually
  performed; do not claim platforms, accounts or shares were verified otherwise.

## 4. Updates, recovery and diagnostics

Windows updates are optional. The red Update available link beside the footer
version opens Configuration; it does not install automatically. Check for
updates and Install update are available there on managed Windows installations.
Public GitHub releases require no GitHub login or shared access token.

For an older version without the updater, download the official Update ZIP,
verify its checksum, extract the entire ZIP into a new folder, then double-click
`Update.cmd` using the same normal Windows account and type UPDATE. Finish or
stop active jobs first. No administrator elevation, reset or reinstall is needed.
Keep the update window open until **Update complete: v...** appears. WSL's
**The operation completed successfully** is an intermediate message, not final
completion. The updater blocks competing app launches during file replacement,
backs up settings/sessions/history and restores the previous version if its
startup check fails. External download destinations are not removed.

If installation/update fails, read the actual error and inspect logs instead of
repeating destructive setup. Windows logs are under
`%LOCALAPPDATA%\TelegramSTLManager\logs`: startup folders contain `server.log`
and `server-errors.log`; update folders contain `update.log`. The tray's View
logs opens its startup log folder. After rollback, include both the failed
startup and restored startup logs when diagnosing. Keep private logs local;
share only relevant, reviewed excerpts, never backup archives or sessions.

On Linux/Docker, follow the documented manual update route and preserve local
state/volumes; do not claim the Windows in-app installer applies there. Restore
user-supplied history backups only with the app stopped and existing state
backed up. Do not transplant unfinished jobs with machine-specific paths and
file identities onto another machine without the documented migration process.

## 5. Hand over

Give me the installed version, browser URL, startup/stop method, download
location, verification results and any remaining local step. Explain how to
select artists and save subscriptions, check availability, and manually start
downloads. The finished app must work without an AI agent, Telegram Desktop or
an open Windows PowerShell window.

Make only changes necessary for installation. Keep account data, private source
links, catalogs and credentials out of Git and distributable packages. Use the
project's distribution allowlist and privacy check when packaging; never weaken
those checks to include local configuration.

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
