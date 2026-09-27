# Telegram STL Manager — Docker test package

Copyright © 2026 trumphater77

This package builds the app for Linux x86-64 or ARM64, including Intel and
Apple Silicon Macs through Docker Desktop. It contains public source only:
no accounts, passwords, sessions, creator selections or download history.
The initial build needs internet access and may take several minutes. Allow
at least 4 GB of memory for Docker and space for the build plus staged releases.

## First start on macOS

1. Install [Docker Desktop for your Mac](https://docs.docker.com/desktop/setup/install/mac-install/)
   and start it. Complete Docker's own first-time setup.
2. Extract this ZIP into a permanent folder. Double-click **Start.command**.
   This test launcher is not signed. If macOS blocks it, inspect the script and
   use the normal macOS Open Anyway approval for this file; do not disable system security.
   If executable permissions were lost during extraction, run
   `bash Start.command` from Terminal inside the extracted folder.
3. Choose a writable download folder when prompted. For the first test, use a
   local folder. Grant Docker folder access if requested.
4. Leave the Terminal window open while the image builds. All build output is
   visible. Your browser opens at **http://localhost:6093** when the app is ready.
5. Complete initial Configuration. The selected host folder appears inside the
   app as **`/downloads`**; keep that as the download base. Set accounts, source,
   and other settings in the web interface. Follow Telegram & artists →
   Download location → Preferences → Review & finish. Click **Save and review**,
   resolve any listed missing or unsaved settings, then **Finish first-time
   configuration** to unlock the artist selection and other pages.

Only the app interface needs a browser. Telegram Desktop, Tampermonkey, Python,
Go and 7-Zip do not need installing separately on your Mac.

## First start on Linux

Install [Docker Engine](https://docs.docker.com/engine/install/) and its
[Compose plugin](https://docs.docker.com/compose/install/linux/). Your normal
user must be able to run `docker info`. Run **`bash Start.command`** from the
extracted folder. The default destination is `~/Downloads/Telegram STL Manager`.
To choose another existing folder, run:

```sh
bash Start.command "/path/to/your/download folder"
```

The launcher uses your user and group IDs for files, so downloads are not owned
by root. Shared folders must be writable by that user directly; supplemental
host groups are not passed into the container. Do not start this launcher as root.

## Stop, restart and update

**Stop.command** stops only this application's container. Closing the log window
or pressing Ctrl+C detaches the logs; the app stays running. Start.command starts
it again. It does not automatically start after a computer restart.

For an update, stop the old package, extract the new one and start it. Select the
same download folder. The named Docker volume `telegram-stl-manager-data` retains
settings, account sessions, history and local staging across image/container
replacement. Do not run two copies against this volume at once.

To change your host download folder later, stop the app and run Start.command
with the new folder path as shown above. This changes the mapping only; it does
not move existing files. Inside the app the base remains `/downloads`.

Download files live outside Docker's application volume. Container removal does
not delete them. Removing the named volume would delete accounts, history and
staged work, so **do not use `docker compose down --volumes` for routine updates**.
To uninstall while retaining data, stop the app and remove its container/image
in Docker Desktop, then remove the extracted package. Keep the named volume if
you want to restore the installation later.

## Network folders and limitations

Mount a network share on the host first, then pass an existing writable folder
to Start.command. Docker Desktop must be allowed to share that path. Keep the
share mounted during the run. The launcher refuses a missing folder rather than
creating it, but a host mount point alone is not proof that a network share is
still connected. Local storage is the recommended first test; disconnected-share
behavior on macOS needs separate testing. No share credentials are bundled.

The web port is published only on host localhost. This package does not expose
the application to other computers. Existing installations using port 6093 must
be stopped before launching this one. Availability checks run while the web UI
is open; downloads still start manually.

Builds select the host's native architecture. Mac/ARM64 behavior has not yet
been tested on real hardware. This is a tester build, not a signed Mac app.

Validated in a clean Ubuntu 24.04 x86-64 VM: Docker installation, image build,
healthy first start, blank setup, localhost host checks, non-root delivery to a
host folder, content verification, overwrite protection, Stop and recreation.
SQLite data, a synthetic session marker and host files survived container
replacement. All 32 setup, collage and MMF repackaging tests passed in the
container. No real account login/download or Mac network-share test was performed.

## Optional Gumroad Beta

All Beta features are incomplete and buggy, for testers only. Gumroad is hidden
and disabled by default. Enable **Show Gumroad** in **Configuration → Beta
features**, save, then connect in **Gumroad account** using your Gumroad email
and password. If you normally use Google sign-in, use **Forgot your password?**
on [Gumroad login](https://gumroad.com/login) with the same account email to set
a separate Gumroad password. Enter that password here, not your Google password.
If asked, enter the emailed or authenticator verification code.
The password is used only for sign-in; the session is kept locally under
`data/gumroad/`. No logged-in browser, browser-cookie export, seller API token,
or additional background service is required. Reconnect if Gumroad expires the
session. Website verification or endpoint changes can interrupt this Beta.

Use **Gumroad → Artists** to select purchased-library creators, destination
folders and scope. **Gumroad → Queue** has Download detected, Check for
downloads, Resume, Stop and Manage artists. Checks run on a separate configurable
interval while the manager is open in a browser; downloads only start manually.
Older products are checked again for newly added files. Named products and
collections keep their names when no release month is supplied; purchase dates
are never treated as release dates. Membership updates can use their publication
month. Undated releases remain in scope.

Downloads keep artist filenames in each release's `Gumroad sources` folder,
extract images into a flat `release_images` folder, and use the shared collage
and release-packaging workflow. Account sessions, purchase links and history
stay in the private data directory and must not be included in public packages.
Gumroad uses authenticated website JSON because its public seller API does not
expose the purchased library. No additional Python dependency is required.

### Required usage acknowledgment

Before installation changes begin, read the usage notice and explicitly acknowledge
it (Windows: choose **Yes** in the dialog; Linux installer and Docker launcher:
type **I AGREE**). Declining stops installation. Windows may ask again when it
starts elevated prerequisite setup or resumes after a restart. Docker remembers
the installation acknowledgment for that extracted package.

First-time browser configuration separately displays the same notice in a required
modal. Read it, tick the acknowledgment checkbox, and select **Acknowledge and
continue**. Closing the tab declines; setup stays locked. The acknowledgment is
saved locally with its time and notice fingerprint. AI installation agents must
leave these acknowledgments to the user and must not accept on their behalf.
The tool supplies no illegal software, accounts, credentials or third-party access
rights. It uses users’ own accounts (for example, MyMiniFactory) to download content
they have purchased, licensed or otherwise have permission to access; this does
not automatically grant redistribution rights. Private Telegram uploads are intended
for personal cloud backup where permitted by law and the content licence. Sharing
with others requires redistribution permission or another lawful basis; “free” or
“freeware” alone does not establish that permission.
The notice covers lawful use, unauthorized sharing of copyrighted material,
possible file loss or damage, independent backups, acceptance of the free-of-charge
software "AS IS" without warranties, and liability limits subject to applicable law. See `USAGE-NOTICE.txt` for the full text.

### Optional STL cleaning during Make release

In **Configuration → Release packaging → STL cleaner during Make release**,
choose **Off** (default), **Delicates-style**, or **Cotton**, then save. Cleaning
runs on all unpacked `.stl` files, including uppercase extensions, during Make
release for MMF and other supported store downloads. Downloaded source archives
are retained unchanged. Each Make release snapshots the saved cleaning choice; a manually started retry
uses the current saved cleaning settings.

Delicates-style is a portable implementation inspired by STL Washer Delicates
1.6.1. It clears headers, normals and attributes, shuffles triangles, translates
the mesh and perturbs coordinates slightly. It fixes the original centring and
unsafe-write behavior. It requires no new dependency. Cotton uses the separately
supplied STL Washer Cotton executables, variants `a0`–`a5`; these are **not bundled**
with public packages. Extract trusted Linux binaries matching the app's x64 or
ARM64 architecture into a folder accessible inside its Linux/WSL/container
environment, mark the executables runnable (`chmod u+x stlwasher-cotton-a*`), and
enter that folder in **Cotton executable folder**. Alternatively place them under
`data/stl-cleaners/x64` or `data/stl-cleaners/arm64` in the application directory
and leave the override blank. Windows drive paths must be expressed as WSL paths;
Docker paths must refer to a folder mounted inside the container. Missing or
incompatible Cotton binaries block enabling Cotton with an actionable error.

Cotton also reduces triangle counts and can remove substantial fine detail.
Both modes alter geometry and placement and can break alignment between separate
parts and supports. No watermark-removal guarantee is made. Processing uses staged
copies, supports cancellation, and rejects empty or structurally invalid/non-finite
output before replacement. This validates file structure, not printability. The
queue shows the current filename, file count and progress; Cotton exposes only
file-level progress and elapsed time, while Delicates also reports progress within
a file. Archives nested inside downloaded archives block cleaning with an explanatory
error rather than silently leaving enclosed STLs unprocessed. Unpack those nested
archives first or keep cleaning off. Cleaning receipts record before/after checksums. Generated release archives
omit member timestamps; original downloads are not rewritten.
