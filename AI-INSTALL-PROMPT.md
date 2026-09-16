# Prompt for an AI agent: install Telegram STL manager

Copy the prompt below into an agent that has terminal/filesystem access on the
**destination** machine. Give it the actual Git repository URL (or project files)
and [INSTALL.md](INSTALL.md). The distribution contains only blank source/catalog
templates; real configuration stays local to the destination machine.
Replace the bracketed preferences if known; the agent can ask for missing values.
Do not put passwords, login codes, authentication databases or QR tokens in the
prompt.

---

Install the existing Telegram STL manager application on this machine, following the
project's `INSTALL.md`. This is an installation task, not a request to redesign
or rewrite the application. Read the actual source and guide before choosing
commands. Complete the setup and appropriate verification, then give me concise
instructions for normal use.

My preferences:

- Git repository: [actual repository URL; optionally a release tag or commit].
- Installation folder: [default: ~/Projects/telegram-stl inside Linux/WSL].
- Operating system: [detect Linux or Windows; ask only if detection is unavailable].
- Destination: [local Linux folder, or SMB share and folder; example:
  //kronos/STL and !! 3D STLs - INCOMING].
- Startup: [foreground initially; offer/implement a user service if requested].
- Telegram source: [configure my own source and approved creator links locally; no built-in group].
- Existing history: [fresh installation, or preserve my supplied SQLite backup].

I authorize installing required dependencies, building the CLI, preparing this
project directory, configuring my chosen destination/service and verifying them
with temporary test files. Reuse answers I have already supplied. Ask only for
missing information, credentials I must enter locally, required administrator
interaction or an action that would interrupt unrelated work. Never ask me to
paste a password, Telegram code, QR token or private session into chat.

Follow these requirements:

1. **Establish the environment and preserve existing work.** Detect OS,
   architecture, Linux username, project path, running service/jobs, disk space
   and destination. Do not assume `/home/bora`, UID 1000 or an existing Kronos
   mount. Do not overwrite a nonempty installation, database, systemd unit,
   credential file or fstab entry blindly. Reuse compatible configuration and
   make a backup before changing existing settings.

2. **Use the supported platform route.** The app currently requires Linux APIs:
   `fcntl`, `resource`, process groups, directory file descriptors, `findmnt` and
   glibc `renameat2(RENAME_NOREPLACE)`. On Windows use WSL2 with Ubuntu 24.04 and
   Linux Python; keep the application, SQLite and staging inside the Linux home
   filesystem. Do not claim native Windows support or bypass filesystem checks.
   Coordinate a necessary WSL install/reboot/shutdown with me because other work
   may be running. WSL services alone do not guarantee the instance stays alive.

3. **Clone and check the installation payload.** Install Git and clone my actual
   repository into a new directory, including any pinned submodules. Use my
   specified release/commit when supplied and record the installed commit.
   Authenticate privately; do not put tokens in clone URLs. Require the Python/HTML/JS sources,
   `templates/`, `tools/build-tdl.sh`, `examples/`, `distribution.json`,
   and the already patched
   `.tools/tdl-stl/src` plus `.tools/tdl-stl/gotd` trees. Preserve their workspace,
   module lockfiles and licenses. Do not invent a project clone URL or assume a
   stock upstream tdl binary has this project's commands. Missing custom sources
   must be obtained from the owner, not silently replaced. Use
   `python3 tools/init-local.py` to create blank private source/catalog files
   without replacing existing ones. Keep the public distribution free of
   actual group names, IDs, links, catalogs, screenshots and session notes. Never
   put these in code, examples, tests, Git or release archives. Use the explicit
   distribution manifest and `tools/package-release.py --check`; do not weaken
   ignore rules or force-add private data.

4. **Install every required dependency.** On the reference Ubuntu route this is
   Python 3.12, `python3-pil` (Pillow), `fonts-dejavu-core`, `7zip` **and `7zip-rar`**, `zip` (damaged image ZIP recovery), `util-linux`, `iproute2`, `tzdata`,
   `git`, `ca-certificates`, `curl`, `tar`, `coreutils` and `cifs-utils` for SMB. APT
   resolves libraries such as SQLite, glibc and the C++ runtime. Python uses its standard library plus Pillow, installed through APT. Verify
   JPEG/WebP decoding and `ImageFont.truetype("DejaVuSans.ttf", 24)`.
   Systemd/user D-Bus are for optional background startup. Node.js is optional
   for the supplied JavaScript tests. Do not install VNC, OCR, Telegram Desktop,
   GTK or an AI API as runtime requirements.

5. **Verify RAR decoding, not just listing.** The base Ubuntu 7zip package can
   recognize RAR without the separate decoder. Check the `Codecs` section for
   Rar decoders and, when available, test a small compressed RAR in temporary
   local storage. The app prefers
   `.tools/7zip/runtime/usr/lib/7zip/7z`, otherwise system `7z`, then `7zz`.
   Confirm the executable actually used is compatible with this architecture
   and has the matching RAR codec. Do not leave an incompatible copied binary
   taking precedence over a correct system installation. The listing parser
   must retain the fix for empty final fields such as `NT Security = `.

6. **Build our actual Telegram CLI.** Follow `tools/build-tdl.sh` using Linux Go
   1.25.8 or a compatible newer compiler. The tested Go downloads/checksums are
   in INSTALL.md. Use official/distribution sources, verify downloads and retain
   Go checksum verification. Preserve `src/go.work`, its local core/extension
   modules and `replace github.com/gotd/td => ../gotd`. Do not double-apply patches
   to already patched trees. Verify `.tools/tdl-stl/tdl stl --help` lists `files`,
   `servers` and `download`. Go is needed only for builds.

7. **Prepare the destination as Linux sees it.** For NAS storage, mount CIFS
   directly inside Linux/WSL using the intended account and actual UID/GID.
   Passwords belong in a root-only local credential file entered by me; do not
   read or echo an existing credential file into tool/chat output. The historical
   `setup-kronos-stl.py` hardcodes user `bora` and must not be run unchanged for
   another user. `/mnt/kronos-stl` is guarded for exactly `//kronos/STL`; use an
   appropriate separate mountpoint for another NAS. Preserve mount-identity,
   stable-file-identity, verified-delivery and no-overwrite checks. Validate read,
   write, atomic rename and cleanup in a uniquely named temporary test directory
   under my chosen base using the procedure in INSTALL.md. Do not touch real
   creator files during installation testing.

8. **Configure the source privately and handle login interactively.** Have me
   set my chosen chat ID and TOC message ID in local `data/source.json`, and
   approved creator links in local `data/creators.json`, following INSTALL.md.
   No actual Telegram source is shipped. Do not enumerate/join other groups,
   send messages or remove scope guards. Blank or invalid source configuration
   must block content actions. Have me use the production CLI's QR login in my
   terminal with storage `~/.local/share/telegram-stl-tdl/data` and namespace
   `telegram-stl-trial`. I enter any 2FA password locally. Reuse an authorized
   working login; otherwise create a fresh one. Never print private auth files
   or import Desktop/another person's credentials. Do not ask me to publish
   source IDs or catalog links to complete installation.

9. **Preserve downloaded history deliberately.** A fresh install starts with
   an empty database. If I supply a history backup, restore it consistently while
   the new server is stopped, preserving any existing database. Review migrated
   subscriptions and destination settings. Do not copy unfinished run/organizer
   journals onto a new machine: their paths, device IDs and inode identities are
   machine-specific. Keep old recovery data intact. Log into Telegram afresh
   rather than including authentication in an installation package.

10. **Run and verify the web app.** Regenerate pages with
    `python3 build-catalog.py`. Start `python3 catalog-server.py` as the normal
    Linux user, confirm `http://127.0.0.1:6093/` loads, and check Queue, Artists,
    Organize folders, Collages and Configuration with consistent navigation. On Windows,
    also check the same URL from the Windows browser. Set the chosen Linux
    destination path. A server-list refresh may validate the CLI login without
    downloading an attachment. Keep the loopback bind and Host/Origin guards;
    do not expose port 6093 to the LAN or Internet as a workaround.

11. **Keep all content actions manual.** Setup, saving subscriptions, service
    startup and opening a page must never trigger downloads or organization.
    Collage drafts and previews stay local; only the explicit Save action may
    write `ARTIST-YYYY-MM.jpg` beside the release archives in their monthly folder.
    Replacing a known collage must first preserve its previous version locally.
    Keep checksum checks, replacement recovery and protection of unrelated files.
    Test drag/drop and saving using
    temporary image fixtures, not real release folders.
    Do not click Download all subscriptions, Apply ready files or Resume as an
    installation test unless I explicitly request the real operation. If I
    authorize a download test, make its creator and scope concrete first; that
    button processes every eligible saved subscription. Organizer Preview is a
    read-only alternative within the approved creator scope. Images from a
    real operation go directly into `creator/YYYY-MM/release_images/`; preserve
    original archives and durable history. Scope choices are From month/year
    and All (archiving). New subscriptions default to the previous month and
    retain that starting month. Do not add AI decisions, schedules or GUI automation.

12. **Configure startup only as chosen.** Use the user's actual project path in
    a systemd user unit; do not run the app as root. On Windows, account for WSL
    launch/lifetime and use the documented foreground or Task Scheduler route
    if wanted. A web-service restart does not resume a job. Do not restart a
    service, unmount storage or shut down WSL while a real worker is active.

13. **Verify and hand over.** Run the supplied Python tests and optional
    JavaScript scope tests. Use temporary fixtures
    for extraction/delivery checks. Do not claim Windows/ARM/NAS testing that you
    have not performed. Finish with the web URL, installation path, runtime and
    decoder versions, destination, startup method, verification results, any
    remaining limitation, and the next manual button for me to use. If an
    administrator or login step requires me, finish all independent setup first
    and give the exact local step still needed.

Leave the installed application able to run without an AI agent or an open
Telegram desktop window. Make only changes needed for installation, preserve the
current feature set, and update the local install notes for any platform-specific
adjustment you actually make.

If given the bundled Linux x64 runtime package, use its runtime/python/bin/python3 and start.sh. Do not install another Python, database server, web server, or Go compiler. Initialize user-specific Telegram/source settings; never distribute account sessions or databases. Windows requires WSL2 Ubuntu 24.04. Do not install a recurring scheduler: availability checks are triggered by an open browser only.
