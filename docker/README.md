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
