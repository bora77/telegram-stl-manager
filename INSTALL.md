## Windows guided test package

Download and extract `Telegram-STL-Manager-Windows.zip`, then double-click
`Install.cmd`. This Intel/AMD x64 test package enables WSL when necessary,
imports a dedicated `TelegramSTL` Ubuntu environment, installs its dependencies,
and creates desktop Start/Stop shortcuts. Internet access is needed for Ubuntu
and OS package downloads. Setup requests Windows administrator approval automatically; choose Yes. No right-click or “Run as administrator” is needed;
if Windows needs a restart, setup continues at the next sign-in. An existing
Ubuntu installation and global WSL settings are not modified.

MyMiniFactory is an optional **Beta** feature, hidden by default. Enable
**Show MyMiniFactory** under **Configuration → Beta features** and save to
show its tab and account settings. Hidden MMF features do not run automatic
availability checks. Existing MMF sessions and history are retained.

On a fresh installation, only **Configuration** is accessible. Connect Telegram,
import the Table of Contents, choose a usable download folder, review all settings
and click **Save configuration**, then **Finish setup**. Other tabs unlock only
after these checks pass. If MMF Beta is enabled, connect that account too.

All application setup is under **Configuration**: Telegram sign-in, the private
Table of Contents link, MMF credentials, and the destination. Under **Download
location**, choose **Local folder** (the default) or **Network share**. Local
storage needs no network credentials; an existing Windows folder such as
`C:\Users\YourName\Downloads` is accepted. For a NAS, select **Network share**
and enter its Windows UNC path, username and password. The dedicated environment restores this connection on application
start. No Linux username, terminal commands, fstab editing or manual dependency
installation is required. Local downloads work without a network share.

Setup displays WSL commands, Ubuntu download progress and speed, and dependency
installation output. Application startup uses a tray icon and saves server
output to logs. Passwords and session contents are not printed.

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

The runtime is bundled; Ubuntu is downloaded from Canonical with a pinned SHA-256.
The installer and launcher are currently unsigned and are a Windows test build:
Validated on Windows 11 Enterprise 25H2 in a QEMU/KVM VM: WSL installation,
reboot continuation, browser access, shortcuts, reset, cached reinstallation and
full uninstall. Tests verified preservation of finished local downloads and
external destination files, including a disconnected share configuration. Real
NAS authentication and account downloads were not exercised in that VM.
Use the desktop Stop shortcut before restarting or upgrading; it stops only
`TelegramSTL`, including any unfinished transfers. Do not delete its environment
folder: it contains the local database, settings and staged downloads.

The ZIP’s top level contains only the user-facing `.cmd` launchers and instructions.
Internal scripts, runtime archives and checksums are in `support/`; leave that
folder intact and do not run its files directly.

For repeated installation testing, double-click `Reset-test-install.cmd` and
confirm removal of local app data. It unregisters only this app's WSL environment
and removes its shortcuts/reboot continuation, retaining cached installer files
and Windows logs. Run `Install.cmd` again for a clean app setup. For full removal,
use `Uninstall.cmd` or **Windows Settings → Installed apps → Telegram STL Manager**.
Both remove local sessions, settings, history and unfinished staged downloads after
confirmation. Finished downloads inside the WSL disk are first copied to Windows
Documents and verified; failure cancels removal. All configured external download
destinations, other WSL distributions and the shared WSL feature remain.
The uninstaller verifies the registered environment's storage path before removal.

Build with `python3 tools/package-runtime.py` followed by
`python3 tools/package-windows.py`.

## Bundled application package (Linux x64 / Windows WSL2)

Download `telegram-stl-linux-x64.tar.gz` and verify its accompanying SHA-256 file. Extract it into a writable directory, then run `./start.sh` (or `bash start.sh`). Open `http://localhost:6093`. Optional: `./install.sh` copies it into your per-user application directory without installing a service. Existing installations are never overwritten.

This package includes a relocatable CPython 3.12 runtime, Python's HTTP web server and SQLite database engine, Pillow, pypdf, the compiled Telegram CLI, and 7-Zip with the RAR codec. No separate Python, pip, Go compiler, database server, or web server installation is needed. SQLite databases are created locally on first use; no existing user's database, account session or Telegram source configuration is included. The package uses the [Astral standalone Python distribution](https://github.com/astral-sh/python-build-standalone), with its download SHA-256 pinned in the runtime builder.

Supported runtime target: Linux x86-64 with glibc, tested on Ubuntu/Zorin 24.04. The OS still provides normal Linux utilities (`bash`, `findmnt`) and any required network-share mount support. On Windows, install WSL2 with Ubuntu 24.04 (`wsl --install -d Ubuntu-24.04`), then use `Start-Windows.cmd`, or run `bash start.sh` inside that distribution. For the tested guided Windows installer, use the Windows ZIP described above; this is not a native Windows executable. ARM and macOS packages are not provided.

First launch creates blank local configuration and a local `downloads` directory. Configure your own Telegram login, approved source/TOC, artist catalog and destination using the setup steps below. The bundled CLI is `.tools/tdl-stl/tdl`; substitute `runtime/python/bin/python3` for `python3` in those steps. You can skip dependency installation and CLI compilation. Keep the application folder writable; private settings and SQLite files live in its `data` directory. Do not replace this directory when upgrading. The CLI's per-user authentication location is described below and must never be distributed.

The availability timer lives in the browser: while the app is open, it requests a metadata-only check at the configured interval (four hours by default; adjustable from 15 minutes to 7 days in Configuration), using saved incremental cursors and the selected creator scopes. Completed or explicitly ignored files are excluded. No download starts automatically, and no scheduled service is installed. Browsers may delay checks while sleeping or suspending tabs; an overdue check runs when the page becomes active again. A check already requested can finish after the tab closes.

Maintainers: `python3 tools/package-runtime.py` builds the bundled archive from the public allowlist and locally built CLI/7-Zip binaries. The builder downloads the pinned Python runtime, Pillow and pypdf wheels; recipients do not need those build-time downloads. The existing source-only package remains available separately.

---

# Install Telegram STL manager on another machine

This guide installs the current application: the web interface, customized
Telegram CLI, manual download queue, download history, image extraction and
folder organizer. For an AI-assisted installation, use
[AI-INSTALL-PROMPT.md](AI-INSTALL-PROMPT.md).

Last checked against the source on **2026-09-17**. Linux operation has been
tested on Zorin/Ubuntu 24.04, x86-64. Windows uses **WSL2 with Ubuntu 24.04**.
The guided Windows ZIP was tested in a Windows 11 VM as described above; the
manual source installation below is an alternative for advanced users.

## 1. Choose the installation route

| Machine | Route |
| --- | --- |
| Ubuntu 24.04 or a compatible Zorin/Mint installation | Follow steps 2–10 in a Linux terminal. |
| Windows 11, or Windows 10 meeting Microsoft's WSL requirements | Complete the Windows preparation below, then steps 2–10 inside Ubuntu. Use your normal Windows browser. |
| Another Linux distribution | Supply the equivalent packages in step 3, or use an Ubuntu 24.04 virtual machine for the same commands. Validate the destination filesystem in step 6. |
| ARM64 Linux / ARM64 Windows with WSL2 | Build the CLI and obtain archive packages for ARM64. Do not reuse this machine's x86-64 binaries. This architecture has not been tested here. |

**Native Windows Python is not supported by the present code.** The workers use
Linux `fcntl`, `resource`, process groups, `findmnt`, directory file descriptors,
and glibc's `renameat2(RENAME_NOREPLACE)`. Installing Windows Python and Windows
7-Zip alone is insufficient. WSL1, arbitrary network-drive mappings and every
Linux/filesystem combination are not claimed to work.

Have an existing Telegram account with access to the approved group, a writable
download destination, and sufficient local disk space. Allow space for the
largest complete archive set, extracted images and any nested archives, plus
the enforced 1 GiB reserve. Source builds also need several GiB for Go and its
module/build caches. An 8 GiB or larger machine is a practical starting point;
the exact requirement depends on the archives. Keep the computer awake during
transfers.

### Windows preparation

In **PowerShell as Administrator**, install WSL2 and Ubuntu:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart Windows if requested. Open **Ubuntu 24.04** from Start and create your
Linux username and password. The Linux username need not be `bora`.

In PowerShell, verify the distribution uses version 2:

```powershell
wsl --list --verbose
```

If it shows version 1, run `wsl --set-version Ubuntu-24.04 2` and wait for the
conversion. If installation is unavailable, follow Microsoft's
[WSL installation instructions](https://learn.microsoft.com/en-us/windows/wsl/install),
which also state the Windows and virtualization prerequisites.

Store the project, SQLite database and staging files **inside Ubuntu's home
directory**, such as `/home/yourname/Projects/telegram-stl`. Use `/mnt/c` only to
bring in an installation archive. Microsoft also recommends keeping Linux
projects on the Linux filesystem for performance.
[WSL filesystem guidance](https://learn.microsoft.com/en-us/windows/wsl/filesystems).

Mount an SMB destination from **inside Ubuntu** using step 6. A working
`\\kronos\STL` connection in Windows does not establish that Linux mount. Avoid
using a mapped Windows drive as the application's database or staging location.

## 2. Get the application from Git

Git is the intended distribution route. The repository URL has not been assigned
in this guide yet: replace `REPLACE_WITH_REPOSITORY_URL` with the actual URL
provided by the owner. Use that project's repository, not the upstream `tdl`
repository. The custom CLI source is essential; a stock `tdl` download does not
contain this application's `stl` commands.

On the new Linux machine, or inside Ubuntu on Windows:

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates
mkdir -p "$HOME/Projects"
cd "$HOME/Projects"
git clone --recurse-submodules 'REPLACE_WITH_REPOSITORY_URL' telegram-stl
cd telegram-stl
git rev-parse HEAD
```

Clone into a new directory. For a private repository, authenticate using your
usual Git credential manager or SSH setup; do not put access tokens in the URL.
Record the commit printed by the last command when reporting an installation.
If you will edit or commit code, enable the bundled privacy check in this clone:

```bash
git config --local core.hooksPath .githooks
```

Before each commit, it checks the staged sources against the distribution
manifest and rejects private state or locally configured Telegram source details.
Run `python3 tools/check-git.py` to check the index manually.

If the owner specifies a release tag, check it out before continuing and run
`git submodule update --init --recursive` if the repository uses submodules.

### Required source files

The repository supplies the **already modified** `.tools/tdl-stl/src` and
`.tools/tdl-stl/gotd` trees, directly or through pinned submodules, plus
`tools/build-tdl.sh`. Preserve their module/workspace lockfiles and licenses.
If using submodules, their commits must include our modifications. Do not apply
the included patches again to already patched sources.

The Git checkout and downloadable archive contain **no configured Telegram
source or real catalog**. Each installation keeps its source IDs, creator links
and folder inventory locally under ignored `data/`. Generated HTML/CSV, history,
logins, jobs and local session notes also stay out of distribution.

### Alternative: downloadable source archive

To prepare a source archive from a complete checkout, run:

```bash
python3 tools/package-release.py
```

This uses the explicit `distribution.json` allowlist and checks the selected
files for references to the locally configured source before packaging. It
creates `dist/telegram-stl-source.tar.gz` and refuses to overwrite an existing
archive. Use `--output /path/to/a-new-name.tar.gz` to choose another filename.
Only blank examples are included; it never packages the local `data/` directory,
generated pages, authentication, build caches or compiled binaries.

On the destination, create an empty directory instead of cloning:

```bash
mkdir -p "$HOME/Projects/telegram-stl"
cd "$HOME/Projects/telegram-stl"
tar -xzf /path/to/telegram-stl-source.tar.gz --strip-components=1
```

For a Windows Downloads folder, the archive path might be
`/mnt/c/Users/YourWindowsName/Downloads/telegram-stl-source.tar.gz`.

### Check either installation route

```bash
ls catalog-server.py build-catalog.py templates/app-layout.html \
  examples/source.example.json examples/creators.example.json \
  .tools/tdl-stl/src/go.work .tools/tdl-stl/gotd/go.mod
```

Use a fresh directory. If replacing an existing installation, follow the
migration section at the end instead of overwriting its state.

Before the first Git commit or any distribution, run
`python3 tools/package-release.py --check` and review the files being staged.
The project's Git ignore rules allow only public application sources and blank
examples. Do not force-add local data, generated pages, historical notes or the
whole `.tools/` directory. If a repository already contains private material,
ignore rules do not remove it from existing commits or history; clean that
repository before distributing it.

## 3. Install packages and libraries

### Required and optional dependencies

| Component | Version / package | Purpose |
| --- | --- | --- |
| Python | 3.12 tested; use Ubuntu 24.04's `python3` | Web server, workers, database and file handling. |
| Python standard library | `sqlite3`, `fcntl`, `resource`, `ctypes`, `zoneinfo`, `http.server`, `subprocess`, `hashlib`, etc. | Already supplied by the Python/OS packages. Pillow is the additional image library, installed through APT below. |
| pypdf | `pypdf==6.14.2` | Targeted PDF footer-stamp processing during MMF repackaging. |
| Pillow | `python3-pil`, 10.2.0 tested | Collage metadata, thumbnails, previews and JPEG rendering; includes JPEG/PNG/WebP support. |
| DejaVu fonts | `fonts-dejavu-core` | Consistent collage title rendering. |
| SQLite library | Supplied with Python's SQLite module and OS dependencies | Durable history and organization records; no database server. |
| Git | Distribution `git` package | Clone the project, retrieve any submodules and identify the installed commit. |
| `7zip` **and** `7zip-rar` | Tested Ubuntu packages: `23.01+dfsg-11` and `23.01-4` | Archive listing/extraction, including compressed RAR images. |
| `zip` | Distribution package | Rebuild damaged ZIP indexes in temporary copies to recover readable images. |
| glibc and C++ runtime | `libc6`, `libstdc++6`, installed through APT dependencies | Native archive executable/codecs and Linux rename operations. |
| `util-linux` | Distribution package | `findmnt`, `mountpoint` and mount tools. |
| `iproute2` | Distribution package | Local route detection for Auto server comparisons. |
| `tzdata` | Distribution package | The app uses `Europe/Berlin` for release-scope calendar defaults and older saved baselines. |
| `cifs-utils` | Required for SMB/NAS storage | Mounts `//server/share` inside Linux or WSL2. |
| `ca-certificates`, `curl`, `tar`, `coreutils` | Distribution packages | Downloads, certificate validation, extraction and checksum checks during setup. |
| Go | **1.25.8** tested; source currently requires at least 1.25.8 | Build-time only, for the customized CLI. No Go compiler is needed during normal operation. |
| Go modules | Locked by the included module/workspace files | `tdl` v0.20.4, patched `gotd/td` v0.140.0, Cobra/Viper, bbolt and their dependencies are compiled into the executable. |
| systemd user services | `systemd`, `systemd-sysv`, `dbus-user-session` | Optional background startup; foreground operation also works. |
| Node.js | Optional; Node 18+ supports the supplied scope tests | Tests only. The frontend has no npm install or Node server requirement. |
| Modern browser | Firefox, Chromium, Edge, etc. | Web interface at `http://127.0.0.1:6093/`. |

Telegram Desktop, VNC/noVNC, OCR/Tesseract, GTK, AI services and an AI
subscription are not needed to run the current application. Some old GUI scripts
remain in the source tree; they are historical tools, not installation steps.
Collages use Pillow locally; no AI or image-generation service is used.

On **Ubuntu 24.04 / compatible systems**, run:

```bash
sudo apt-get update
sudo apt-get install -y software-properties-common
sudo add-apt-repository -y universe
sudo add-apt-repository -y multiverse
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-pil fonts-dejavu-core git ca-certificates curl tar coreutils \
  util-linux iproute2 tzdata cifs-utils 7zip 7zip-rar zip
python3 -m pip install --user --break-system-packages pypdf==6.14.2
```

Use a normal Linux account for the application, not root. `sudo` above is for
package installation. Ubuntu supplies RAR decoding through the separate
[7zip-rar package](https://packages.ubuntu.com/noble/7zip-rar); merely seeing RAR
in a file-format listing does not prove its decoder is installed.

Confirm the decoder, image library, font and standard-library support:

```bash
7z i
python3 -c "import pypdf; print(pypdf.__version__)"
python3 -c "from PIL import Image, ImageFont, features; print(Image.__version__); assert features.check('jpg') and features.check('webp'); ImageFont.truetype('DejaVuSans.ttf', 24)"
python3 -c 'import sqlite3, fcntl, resource, ctypes; from zoneinfo import ZoneInfo; print("SQLite", sqlite3.sqlite_version); print(ZoneInfo("Europe/Berlin")); print("renameat2 available:", hasattr(ctypes.CDLL(None), "renameat2"))'
```

In the **Codecs** section of `7z i`, find `Rar1`, `Rar2`, `Rar3` and `Rar5`.
The `renameat2` check must print `True`.

The existing Bora installation uses a private matching pair under
`.tools/7zip/runtime/usr/lib/7zip/`. The application prefers that executable when
it exists, otherwise it uses system `7z`, then `7zz`. The fresh source package
above omits this machine-specific bundle, so your newly installed system packages
are used. A source-only Git checkout should likewise exclude it. If transferring a larger project copy, move an incompatible old
`.tools/7zip/runtime` aside before testing. Never combine an arbitrary old codec
with a different archive executable.

For distributions without these APT packages, install equivalent dependencies
and a 7-Zip build with RAR decoding from the distribution or the
[official 7-Zip download page](https://www.7-zip.org/download.html). Verify the
selected executable with a compressed RAR, then run the filesystem checks below.
An Ubuntu 24.04 VM is the reference fallback if those checks fail.

## 4. Build the customized Telegram CLI

Skip the compiler download only if you already have a compatible Linux Go
compiler and `go version` reports 1.25.8 or newer. A Windows `go.exe` is not the
compiler to use inside WSL. The build script prefers its local Go directory over
`PATH`, so do not carry over a compiler for the wrong architecture.

The following installs the **tested** Go 1.25.8 in the fresh project, without
changing your system Go installation. This is a reproducibility pin, not a claim
that 1.25.8 is the latest version. Release files/checksums are published by
[Go](https://go.dev/dl/).

Run from the project directory:

```bash
case "$(uname -m)" in
  x86_64)
    stl_arch=amd64
    stl_go_sha=ceb5e041bbc3893846bd1614d76cb4681c91dadee579426cf21a63f2d7e03be6
    ;;
  aarch64|arm64)
    stl_arch=arm64
    stl_go_sha=7d137f59f66bb93f40a6b2b11e713adc2a9d0c8d9ae581718e3fad19e5295dc7
    ;;
  *) echo 'This guide covers 64-bit x86 and ARM.'; exit 1 ;;
esac
mkdir -p .tools/tdl-research
stl_go_archive=".tools/go1.25.8.linux-${stl_arch}.tar.gz"
curl --fail --location --output "$stl_go_archive" \
  "https://go.dev/dl/go1.25.8.linux-${stl_arch}.tar.gz"
printf '%s  %s\n' "$stl_go_sha" "$stl_go_archive" | sha256sum --check -
```

Continue only after the checksum prints `OK`. Do not unpack over an existing
`.tools/tdl-research/go` tree:

```bash
tar -xzf "$stl_go_archive" -C .tools/tdl-research
.tools/tdl-research/go/bin/go version
bash tools/build-tdl.sh
.tools/tdl-stl/tdl stl --help
```

The first build downloads the locked Go dependencies and can take several
minutes. The script limits compilation to two CPUs and a 2 GiB Go memory target,
uses `CGO_ENABLED=0`, and installs the result as `.tools/tdl-stl/tdl`.
It writes a binary checksum to `.tools/tdl-stl/build.json`.

Help must list **files**, **servers**, **download** and **serve** under `stl`. An “unknown
command stl” error means this is the wrong binary. Keep
`.tools/tdl-stl/src/go.work`: it connects the local `core` and `extension` modules
and replaces `github.com/gotd/td` with the sibling patched `../gotd` tree.
Do not disable workspace mode or substitute an upstream precompiled `tdl`.

## 5. Initialize private settings and generate the pages

From the project directory, create blank local files:

```bash
python3 tools/init-local.py
```

This creates `data/source.json`, `data/creators.json` and
`data/incoming-folders.json` with private file permissions. Existing files are
kept. The examples contain no live group, topic or creator. Keep the source
fields blank until step 7; the empty web interface and offline tests can run
without a Telegram source.

Generate the pages and check the local code:

```bash
python3 build-catalog.py
python3 -m unittest discover -p 'test_*.py'
```

The page builder should generate three HTML pages and the creators CSV from the
local catalog (initially empty). The test suite uses
temporary fixtures and mocked Telegram calls, without needing Telegram login or production NAS
access. It covers extraction, no-overwrite delivery, history and organizer resume.

Optional JavaScript scope tests:

```bash
sudo apt-get install -y nodejs
node --test selection-rules.test.js
```

These exercise scope defaults, calendar boundaries and folder matching. Browser automation/Playwright is not required
for installation; use the manual UI checks in step 8.

## 6. Configure the destination

### Option A: local Linux storage

Create an ordinary local folder, for example:

```bash
mkdir -p "$HOME/STL-INCOMING"
```

Select its full Linux path in Configuration later. Inside WSL, prefer this Linux
filesystem location over `/mnt/c` for the first functional test.

### Option B: Kronos or another SMB server

Create or obtain a NAS account with read, create, rename and delete access to the
intended STL directory. A dedicated account scoped to those folders is enough;
NAS administration rights are not required for daily use.

The original layout is:

```text
Windows share:       \\kronos\STL
Linux mount:         /mnt/kronos-stl
Download base:       /mnt/kronos-stl/!! 3D STLs - INCOMING
Example destination: .../- Atlan Forge/2026-08/
```

Do not run `setup-kronos-stl.py` unchanged on another machine: that historical
helper hardcodes the Linux account `bora`. Use the generic procedure below.

1. Create a root-only credential directory, then open a local editor:

   ```bash
   sudo install -d -m 700 /etc/samba/credentials
   sudoedit /etc/samba/credentials/telegram-stl
   ```

   If the credential file already exists, reuse/review it instead of blindly
   replacing it. Enter the following **in the editor**, substituting your own
   values. Add `domain=...` only if your NAS requires a domain.

   ```ini
   username=YOUR_NAS_USERNAME
   password=YOUR_NAS_PASSWORD
   ```

   Save and protect it:

   ```bash
   sudo chown root:root /etc/samba/credentials/telegram-stl
   sudo chmod 600 /etc/samba/credentials/telegram-stl
   ```

   Enter secrets locally; do not paste them into AI chats or shell command
   arguments. This follows the credential-file mechanism documented by
   [mount.cifs](https://manpages.ubuntu.com/manpages/noble/man8/mount.cifs.8.html).

2. Mount the share with your actual Linux UID/GID:

   ```bash
   stl_uid=$(id -u)
   stl_gid=$(id -g)
   sudo mkdir -p /mnt/kronos-stl
   sudo mount -t cifs '//kronos/STL' /mnt/kronos-stl \
     -o "credentials=/etc/samba/credentials/telegram-stl,uid=${stl_uid},gid=${stl_gid},file_mode=0600,dir_mode=0700,serverino,nosuid,nodev"
   findmnt -T /mnt/kronos-stl -o TARGET,SOURCE,FSTYPE
   ls -ld '/mnt/kronos-stl/!! 3D STLs - INCOMING'
   ```

   Do not mount over an already mounted path. If the Incoming folder does not
   exist on a new share, create it as the normal application user after mounting.
   `serverino` preserves server-provided file identities used by organizer resume.

   For another NAS, substitute `//YOUR_SERVER/YOUR_SHARE` and use a separate
   mountpoint such as `/mnt/stl-storage`; set Configuration to a directory there.
   `/mnt/kronos-stl` is intentionally reserved by the code for `//kronos/STL`.
   To fix Kronos hostname resolution, add the correct LAN address to local name
   resolution rather than weakening the mount-identity check. Leave an unmounted
   mountpoint root-owned and empty so it cannot become an accidental download
   directory.

3. Optionally make the mount persistent. Back up `/etc/fstab`, edit it with
   `sudoedit`, and add one entry if none exists for this mount. Replace `1000`
   below with the outputs of `id -u` and `id -g` respectively:

   ```fstab
   //kronos/STL /mnt/kronos-stl cifs credentials=/etc/samba/credentials/telegram-stl,uid=1000,gid=1000,file_mode=0600,dir_mode=0700,serverino,nosuid,nodev,_netdev,nofail,x-systemd.automount,x-systemd.mount-timeout=15s 0 0
   ```

   On systemd Linux/WSL, run `sudo systemctl daemon-reload` after editing. The
   optional systemd settings are covered in step 9. On a system without systemd,
   use a manual mount for each session or its own supported boot mechanism.

### Check the destination's actual file operations

Before using real releases, run this from the project directory with the chosen
base path as the argument. It creates a unique temporary test directory **inside
that destination**, checks verified delivery and an archive-style rename, then
removes only its own test files. This does not access Telegram or import history.

```bash
python3 - '/mnt/kronos-stl/!! 3D STLs - INCOMING' <<'PY'
from pathlib import Path
import sys, tempfile
from file_delivery import deliver, mount_identity
from organizer_apply import PinnedFolder, identity

base = Path(sys.argv[1]).expanduser().resolve(strict=True)
print('Destination mount:', mount_identity(base)[:3])
with tempfile.TemporaryDirectory(prefix='.telegram-stl-install-', dir=base) as remote:
    with tempfile.TemporaryDirectory(prefix='telegram-stl-install-') as local:
        source = Path(local) / 'check.bin'
        source.write_bytes(b'Telegram STL manager installation check\n')
        target, size, checksum = deliver(source, remote, 'Test Artist', '2026-01', 'check.bin')
        assert target.read_bytes() == source.read_bytes()
        pinned = PinnedFolder(remote, 'Test Artist')
        try:
            record = {'source': '2026-01/check.bin', 'destination': '2026-02/check.bin',
                      'identity': identity(target.stat()), 'move_started': True}
            pinned.move(record)
            assert (Path(remote) / 'Test Artist/2026-02/check.bin').read_bytes() == source.read_bytes()
        finally:
            pinned.close()
print('Destination read/write, verified delivery, rename and cleanup passed.')
PY
```

For local storage, pass `"$HOME/STL-INCOMING"` instead. If the check fails, fix the
mount, permissions or filesystem support before using Apply. If WSL reports CIFS
is unsupported, update WSL and verify its kernel supports CIFS; otherwise use an
Ubuntu VM with direct NAS access. Do not replace no-overwrite renames with
overwriting operations to make a failing filesystem pass.

## 7. Log in to Telegram on this machine

Use your own authorized Telegram account. Configure the one source you intend
to use locally; no source is supplied or inferred by the installer.

1. Open `data/source.json` in a local text editor. Set `chat_id` and
   `toc_message_id` to positive integer IDs from your chosen Table of Contents
   link. A private forum link has the form `https://t.me/c/CHAT_ID/MESSAGE_ID`;
   the first number is the positive chat ID used here, without a `-100` prefix.
   These values are never to be added to source code or Git.
2. Populate `data/creators.json` with the creator topics you authorize from that
   TOC. Each entry in `creators` needs `name`, its full `topic_url`, and
   `within_approved_group: true`. A topic URL must belong to the configured
   chat. For example, this is a **template with placeholders**, not a working
   catalog:

   ```json
   {
     "source": {"name": "Local catalog", "url": "YOUR_TOC_URL"},
     "creators": [
       {"name": "Example Artist", "topic_url": "YOUR_CREATOR_TOPIC_URL", "within_approved_group": true}
     ]
   }
   ```

   Enter the actual URLs only in the private local file. The app does not
   automatically discover a new TOC or join groups; importing a different TOC
   is a local setup step. An empty creator list is valid until you are ready.
3. `data/incoming-folders.json` may keep its empty folder list initially. The
   Configuration page reads the chosen destination's directories at runtime.
4. Run `python3 build-catalog.py` again after changing the local catalog. Do not
   change the configured source while a worker is active. A change of source
   should use a fresh local installation/state directory to keep catalogs,
   subscriptions, transfer receipts and history consistent.

The Python adapter and custom CLI both enforce the configured chat and selected
creator topic. Missing or invalid source configuration blocks content actions;
it does not fall back to a built-in group.

In a real interactive terminal, as the Linux user who will run the server:

```bash
cd "$HOME/Projects/telegram-stl"
umask 077
mkdir -p "$HOME/.local/share/telegram-stl-tdl/data"
chmod 700 "$HOME/.local/share/telegram-stl-tdl" "$HOME/.local/share/telegram-stl-tdl/data"
.tools/tdl-stl/tdl \
  --storage "type=bolt,path=$HOME/.local/share/telegram-stl-tdl/data" \
  -n telegram-stl-trial login -T qr
```

Scan the terminal QR with Telegram on your phone under **Settings → Devices →
Link Desktop Device**. If prompted, enter your two-step verification password
directly in the terminal. Do not send the QR/login code/password to an AI agent.
Successful login is reported in the terminal.

The `telegram-stl-trial` namespace is a retained internal name and must match the
adapter. The current CLI uses its built-in Telegram application configuration;
there is no BotFather token or AI API key to obtain. It connects directly through
the Telegram CLI, so a desktop Telegram window is not required afterward.

Use this command rather than `.tools/tdl/login-trial.py`: that older helper points
to the original trial binary, which the fresh package does not include. Login
again only to replace/repair the login deliberately, with all workers stopped;
`tdl login` can overwrite the selected namespace's existing session.

## 8. Start and configure the web application

From the project directory, run in the foreground first:

```bash
python3 catalog-server.py
```

Open **http://127.0.0.1:6093/**. On Windows, use the same URL in your Windows
browser; WSL supports accessing Linux web applications via localhost.
[Microsoft networking guidance](https://learn.microsoft.com/en-us/windows/wsl/networking).

Configure the application:

1. Open **Configuration**, enter the full **Linux** destination path from step 6,
   and save. The default is `/mnt/kronos-stl/!! 3D STLs - INCOMING`.
2. Press **Refresh server list** to check the saved CLI login. Leave each data
   center on **Auto (fastest)** initially. This refresh reads Telegram server
   configuration and does not download an attachment.
3. Open **Artists**, find a creator in the approved catalog, choose a creator
   folder and a scope, then save. For an initial small test, save only one creator
   and choose a recent month whose releases you intend to download.
4. Check that all four navigation links and the shared logo work and that the
   queue shows the saved creator/scope.
5. When ready for actual transfers, click **Download all subscriptions** once.
   This processes all eligible files in the saved scope; it is not a single-file
   diagnostic button. Auto normally reuses recent comparisons for up to six
   hours. Configuration can instead compare at the first large file of each
   artist/month and reuse that choice for its remaining files and parts.
   Those tests use two-second samples per server, plus connection setup; a
   comparison can take more than ten seconds. Another data center or a failed
   connection can require another comparison.
   Set **Target total download speed (MB/s)** in Configuration to your practical
   connection capacity (default 28). The speed gauge appears beside total progress.
   Adaptive mode runs up to three transfers, adding a file after a 30-second
   window below 95% of target and favoring another data center when possible.
   Slow downloads keep running; faster completed releases can be processed first.
   Choose **One file at a time** to stop adding parallel work. Target and mode
   changes apply to an active run within five seconds. This is not a speed cap.
   Allow local space for simultaneous files, queued archive parts and extraction;
   the scheduler reserves active transfers’ remaining sizes plus 2 GiB. It pauses
   new starts when completed files waiting for processing reach about 8 GiB.
   For a speed-triggered comparison, set **Retest when download speed stays below (MB/s)** in
   Configuration, for example `20`. Zero (the default on a fresh installation)
   disables it. Auto requires at least two minutes below the threshold using
   a 30-second rolling average after each file's first ten seconds. Only active
   download time counts, including across consecutive files; speed recovery
   resets it. Release boundaries and long processing gaps preserve the counter
   and the server choice. The current file finishes before the next suitable
   file triggers this comparison. If the measured average is below half the
   threshold (below 10 MB/s when set to 20), Auto pauses the current file and
   compares immediately, without the two-minute wait. It resumes using chunks
   already saved locally. Overlapping transfers use combined throughput for slowdown checks.
   Both slowdown rules have a ten-minute cooldown and
   require a 10% improvement to switch working servers. Failed comparisons
   keep the previous server.
6. Check download speed, extraction progress, image delivery and archive moves.
   Delivered images belong directly in `creator/YYYY-MM/release_images/`, with
   stable name suffixes to avoid collisions. History persists if releases move
   out of Incoming later.
   If a run stops or fails, **Resume download queue** reuses its saved artist
   checks and pending files. It skips completed identities and reuses verified
   staging; incomplete file transfers restart. Only unchecked or interrupted
   creator checks run again. Resume keeps the original artists, scopes and
   folders, requires the same source and destination, and uses current server
   settings. Resolve any persistent error before retrying. Use a new
   **Download all subscriptions** run to check all selected creators for new posts.
7. To organize existing files, choose an artist/folder under **Organize folders**
   and run **Preview** first. **Apply ready files** changes the selected folder and
   records matches. It also extracts missing images from already organized
   archives when its image option is checked. Stop/Resume use the saved job.
8. Under **Collages**, open a release with extracted images, drag 5–9 images into
   the numbered grid slots, then drag between slots to swap them. Drafts and
   previews stay local. **Save to Kronos** explicitly writes a new full-size JPEG
   under `creator/YYYY-MM/`, beside the release archives, named `ARTIST-YYYY-MM.jpg`.
   Further saves replace that collage and keep previous versions locally under
   `data/collages/versions/`. See IMAGE-WORKFLOW.md for layouts,
   keyboard controls and retry behavior.

The CLI service starts automatically for a Telegram operation and shares one
login between downloads and organizer previews. Stopping a job cancels only its
own request. Its private Unix socket and log are under the CLI state directory;
it exits after one minute without requests. No additional service installation
or Telegram login is needed. Before logging in again, finish/stop active jobs
and let the idle service exit. During an upgrade from the older CLI, its current
standalone transfer must finish once before the shared service can start.

Saving subscriptions/settings, logging in, starting the server and opening pages
never start a download batch or apply an organizer plan. Choose **From month/year**
or **All (archiving)**. New subscriptions default to the **previous calendar month**;
the selected starting month stays fixed and includes all later releases. Older
saved scopes retain their starting month. Release months are inferred from filenames, not
Telegram posting dates; uncertain names remain review items.

The server listens on loopback and checks the Host/Origin. Keep the documented
localhost URL; opening the machine's LAN IP will not work with these guards.
There is no built-in remote-user authentication. For access from another machine,
use an explicitly configured SSH tunnel to localhost rather than changing the
server binding or removing the guards.

## 9. Optional background startup

### Linux or systemd-enabled Ubuntu in WSL

Install service support if needed:

```bash
sudo apt-get install -y systemd systemd-sysv dbus-user-session
```

For WSL, check `ps -p 1 -o comm=` inside Ubuntu. If it is not `systemd`, merge this
section into `/etc/wsl.conf` without replacing its other settings:

```ini
[boot]
systemd=true
```

Then close your WSL work, run `wsl --shutdown` in PowerShell, and reopen Ubuntu.
This shuts down **all** WSL distributions, so do it only when their other work can
stop. Follow Microsoft's [systemd instructions](https://learn.microsoft.com/en-us/windows/wsl/systemd)
if the user service bus is still unavailable. Systemd services alone do not
guarantee that a WSL instance stays alive.

Stop the foreground server with Ctrl+C **while no worker is running** before
starting the service. Create a user unit using the new machine's project path.
This generator refuses to overwrite an existing unit:

```bash
python3 - <<'PY'
from pathlib import Path
root = Path.cwd().resolve()
assert (root / 'catalog-server.py').is_file(), 'Run from the project directory.'
assert not any(c in str(root) for c in '\n\r'), 'Use a project path without line breaks.'
def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
unit = Path.home() / '.config/systemd/user/telegram-stl-catalog.service'
unit.parent.mkdir(parents=True, exist_ok=True)
text = ('[Unit]\nDescription=Telegram STL manager web interface and manual downloader\nAfter=network.target\n\n'
        '[Service]\nType=simple\nWorkingDirectory=' + str(root).replace('%', '%%') + '\n'
        'ExecStart=/usr/bin/python3 ' + quote(root / 'catalog-server.py') + '\n'
        'UMask=0077\nRestart=on-failure\nRestartSec=3\n\n'
        '[Install]\nWantedBy=default.target\n')
with unit.open('x') as out:
    out.write(text)
print('Created', unit)
PY
systemctl --user daemon-reload
systemctl --user enable --now telegram-stl-catalog.service
systemctl --user status telegram-stl-catalog.service
```

Run these commands as the application user, without `sudo`. This enables the web
server for that user's session. It does not start/resume download or organization
jobs. On a dedicated Linux server, `sudo loginctl enable-linger YOUR_LINUX_USER`
can keep the user's services available after logout; enable that only if desired.

For Windows, Ubuntu must be launched after Windows starts. The simplest approach
is to open Ubuntu and keep that terminal available during work. For a Windows
login shortcut, configure Task Scheduler **At log on**, under your Windows user,
with program `wsl.exe` and arguments:

```text
-d Ubuntu-24.04 --exec /bin/bash -lc "systemctl --user start telegram-stl-catalog.service; exec /usr/bin/sleep infinity"
```

Use the actual distribution name from `wsl --list --verbose`. The long-lived WSL
command keeps an ordinary Linux process running while the service operates.
Keep the task running, disable any automatic execution-time limit for that task,
and test startup after a Windows login. Windows sleep/shutdown still interrupts
work; systemd is not a guarantee against WSL termination. If services are not
wanted, simply run `python3 catalog-server.py` in an open Ubuntu terminal instead.

## 10. Backup, migration and troubleshooting

### What lives where

| Location | Contents / handling |
| --- | --- |
| `data/collages.sqlite3`, `data/collages/` | Private collage selections, preview recipes, cached thumbnails and export recovery; keep local and out of Git. |
| `data/source.json`, `data/creators.json`, `data/incoming-folders.json` | Private source IDs, local catalog and folder inventory. Never include in Git or a downloadable release. |
| Project `data/download-history.sqlite3` | Download/import history and image manifests. Back up for continuity. |
| `data/run.json`, `data/download-plans/` | Private download run and completed artist checks for Resume. Keep with the original staging, source and destination; do not publish or transplant unfinished runs. |
| `data/settings.json`, `data/subscriptions.json` | Destination settings, creator choices and scopes. Optional migration; review paths. |
| `data/run.json`, `data/organizer-apply.json`, `data/organizer-jobs/`, `data/organizer-work/` | Machine/job-specific state. Keep for recovery on the original machine. Do not transplant unfinished jobs to a different mount or machine. |
| `data/telegram-servers.json`, `data/telegram-server-speeds.json` | Rebuildable endpoint lists and comparisons; refresh on a new machine. |
| `~/.local/share/telegram-stl-tdl/data` | Private Telegram authentication. Log in afresh on the new machine. |
| `~/.local/share/telegram-stl-tdl/transfers` | Local transfer receipts and payload staging. |
| `~/.local/share/telegram-stl-desktop/staging` | Local release staging; the legacy name does not require Telegram Desktop. |
| `/etc/samba/credentials/telegram-stl` | Private SMB credential file; set up locally on each machine. |
| NAS or selected destination | Original release archives and extracted images. |

To replace a machine while retaining history, finish or deliberately stop current
operations, then stop the old web service. Keep its complete recovery data intact.
Use a fresh clone/source installation on the new machine and transfer a consistent
SQLite backup separately. In the old project directory, the following makes a
new backup file and refuses to overwrite an existing one:

```bash
python3 - <<'PY'
from pathlib import Path
import sqlite3
target = Path('download-history-backup.sqlite3')
with target.open('xb'):
    pass
source = sqlite3.connect('file:data/download-history.sqlite3?mode=ro', uri=True)
destination = sqlite3.connect(target)
try:
    source.backup(destination)
finally:
    destination.close()
    source.close()
print('Created', target)
PY
```

Restore this as `data/download-history.sqlite3` on the new machine **before first
server startup**, or while the new server is stopped; preserve any existing target
database rather than overwriting it. For migration of your own installation, transfer your private source/catalog
files and subscriptions/settings separately if wanted; never include them in
a public release. Review the destination in Configuration. Start a fresh Telegram
login. Keep the old instance stopped when switching the library to the new one.

Historical destination paths remain historical. Deduplication still works after
paths change, but an old split companion may need its recorded path made available
before reuse. Unfinished organizer journals contain device/inode identities and
absolute paths and must not be edited to force them onto a different filesystem.

### Common problems

| Symptom | Check |
| --- | --- |
| Missing or invalid `data/source.json` | Initialize blank local files, then configure your own source privately in step 7. |
| `unknown command stl` | Use/build `.tools/tdl-stl/tdl` from the supplied custom source. |
| Go toolchain is too old or wrong architecture | Check `go.mod`, `go.work` and the local Go directory preferred by the build script. |
| `No module named fcntl` / `resource` | Run Linux Python in WSL2, not native Windows Python. |
| RAR lists normally but extraction says `Unsupported Method` | Verify the executable actually selected by the app has the matching RAR codec. |
| “ambiguous member name or listing” on ordinary RAR metadata | Use the current `release_images.py`, including the empty-final-field fix. |
| `Address already in use` on 6093 | Stop a duplicate foreground server or use the existing service. Do not kill a running worker. |
| Telegram authentication/CLI failure | Check Internet access, group membership and the exact login storage/namespace; renew login interactively while idle. |
| No images for an older completed download | Use organizer Preview, then explicitly Apply with image extraction enabled. |
| NAS unavailable or access denied | Check from inside Linux/WSL, verify the CIFS source and the destination operation test. |
| `renameat2` / unsupported filesystem error | Use supported local Linux storage or a direct CIFS mount that passes step 6; retain no-overwrite semantics. |
| Linux URL works but Windows URL does not | Check WSL localhost forwarding/VPN/firewall configuration without exposing the app to the LAN. |
| Service cannot connect to user bus | Reopen the Linux login session; check systemd and `dbus-user-session`. Foreground launch is an alternative. |
| “File changed since preview” | Preview again for a new plan, or inspect the saved unfinished plan before Resume. Do not discard its journal to hide partial moves. |
| Extraction takes longer on some releases | Nested/solid archives may require substantial decoding; the July speed result is not a guarantee for every archive. |

Useful local diagnostics:

```bash
systemctl --user status telegram-stl-catalog.service
journalctl --user -u telegram-stl-catalog.service -n 50 --no-pager
tail -n 50 data/worker.log
tail -n 50 data/organizer.log
curl --fail http://127.0.0.1:6093/api/queue
```

Logs/queue output can include creator names and file paths. Share only the relevant
error if asking for help; never attach authentication databases or NAS credentials.
See [CATALOG.md](CATALOG.md) for the application workflow and implementation notes.

## Optional MyMiniFactory connection

Open **Configuration → MyMiniFactory account** in the manager and connect with your MMF username/email and
MMF password. Google-only accounts may first need an MMF password from the site's
Forgot password flow. Select creators in Shared with me, their destination folders
and starting months, then save and check availability. Start downloads manually.
No additional Python libraries, browser runtime, extension, daemon or scheduled
service is required. Python's standard library provides HTTP/TLS, cookie sessions
and SQLite; image extraction uses the package's existing 7-Zip installation.

Sessions and MMF database/partial files live under `data/mmf/`. Treat this directory
as private account data and exclude it from shared packages and Git. The app does
not save your password. Reconnect when MMF expires or invalidates the session.

MMF downloads are automatically repacked after manual start into one 7-Zip set
per MMF release folder, with volumes limited to 4000M (4000 MiB). The release name
is used for the archive and destination folder, including names like Welcome Pack. Allow local free space for the original downloads,
unpacked contents and compressed output. Repacking uses the existing bundled
7-Zip; no extra library is needed. Original downloads are removed from staging
only after verified delivery and completion recording.

## Docker on macOS and Linux

The optional Docker tester package has a separate [step-by-step guide](docker/README.md).
Install Docker Desktop on macOS, or Docker Engine with Compose on Linux, then
extract `Telegram-STL-Manager-Docker.zip` and run `Start.command`. First startup
builds the image with visible output. Account configuration remains in the app.
The selected host destination is mapped to `/downloads`; application data uses
the persistent `telegram-stl-manager-data` Docker volume. Do not remove that
volume during updates. Public packages contain no saved credentials or sessions.
