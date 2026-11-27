# Telegram STL Manager — Docker for Linux and macOS

Copyright © 2026 trumphater77

Use this package on Linux or macOS. Docker builds the application and its dependencies;
Python, Go, SQLite and 7-Zip do not need separate host installations. The first build
requires internet access, at least 4 GB available to Docker, and room for staged downloads.

## Launchers

| Action | Linux | macOS |
| --- | --- | --- |
| First installation | `bash Install-Linux.sh` | `Install-macOS.command` |
| Start | `bash Start-Linux.sh` | `Start-macOS.command` |
| Stop | `bash Stop-Linux.sh` | `Stop-macOS.command` |
| Update | `bash Update-Linux.sh` | `Update-macOS.command` |

Install and Start use the same startup routine; installation is needed only once.
On macOS, double-click the `.command` file. If permissions were lost, run it with
`bash` in Terminal. If macOS blocks an unsigned launcher, inspect it and use the
normal Open Anyway approval; do not disable system security.

Legacy `Start.command`, `Stop.command` and `Update.command` remain compatibility
aliases for already-released updaters. If an older updater left only those names,
run a legacy launcher once to restore the OS-specific shortcuts from `support/launchers`.
Use the OS-specific names for new installations.

## First installation

1. Install and start [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/)
   on macOS, or [Docker Engine](https://docs.docker.com/engine/install/) and the
   [Compose plugin](https://docs.docker.com/compose/install/linux/) on Linux.
   Your normal user must be able to run `docker info`; do not run these launchers as root.
2. Extract the complete ZIP into a permanent folder. Keep `support/` intact.
3. Run the Install launcher for your OS and acknowledge the usage notice.
   Linux defaults to `~/Downloads/Telegram STL Manager`; macOS asks for a folder.
   To use another existing writable folder on Linux:

   ```sh
   bash Install-Linux.sh "/path/to/downloads"
   ```

4. Keep the build window open. Your normal browser opens at http://localhost:6093.
5. Complete Configuration: Telegram & artists → Download location → Preferences →
   Review & finish. The selected host folder appears inside the app as `/downloads`;
   keep that as the download base. Save settings and resolve missing requirements
   before clicking Finish first-time configuration.

Mount network shares on the host before selecting them. Docker must have access
and your host user must have write permission. Supplemental host groups are not
passed through to the container. The application uses your UID/GID for its files.
The web port is bound to localhost only; do not expose the current interface to
other computers or give the application a Docker socket or privileged container access.

Only checks made while an app browser page is open are scheduled automatically.
Downloads stay manual unless you enable the provider's Auto-download toggle.
Closing the browser or log window does not stop active work. Stop uses the Stop
launcher; restarting the computer does not automatically start this package.

## Updates and recovery

Download queues warn when local staging storage or the final destination is
nearly full. Both are checked before downloading an archive set/release, with
extra room for verified copying when they share a drive. If space is insufficient,
free space on the named drive and use **Resume**; staged data is retained.
For Docker, staging is in the application data volume; the destination is the
configured download-folder mount. Docker Desktop disk capacity can therefore
differ from free space on your host or NAS.

Leave the app running and use the OS-specific Update launcher. It downloads the
latest stable release, verifies its GitHub SHA-256 digest and version, and builds
before stopping the app. It blocks new tasks and waits for active work to finish,
then copies application state to a new volume and starts the updated container.
Accounts, settings, history, user IDs, timezone and the download-folder mapping
are retained. The external download folder is never duplicated or deleted.

A failed startup restores the original image and data volume. After a failed or
interrupted attempt, use the same Update launcher with `--recover`, for example:

```sh
bash Update-Linux.sh --recover
```

On macOS use `bash Update-macOS.command --recover`. Keep `.updates/current`, its
lock and saved volumes until recovery finishes. Previous images, volumes and
package files remain as recovery copies. Allow disk space for an additional copy
of app state, including unfinished local downloads. Updates need no host Python.

Older packages without the updater/reservation endpoint need one manual upgrade:
finish active work, stop the app, extract the new package and use the same download
folder and named data volume. The initial volume is `telegram-stl-manager-data`.
After guided updates, its active replacement is recorded in `support/data-volume.txt`.
Preserve that file and `support/download-folder.txt` when manually replacing a package.
Never run two packages against the same volume or use `docker compose down --volumes`
for an ordinary update.

The app announces a new version once, with all release notes since your installed
version. You can close it, request a reminder, or reopen it from the footer.
Updates are optional and Docker control stays on the host.

## Files, removal and documentation

To change the host download folder, stop the app and run the Start launcher with
its new path. This changes the mapping; it does not move existing files. The app
still uses `/downloads` internally.

Stopping/removing the container does not delete external downloads. Removing the
application's data volume deletes its settings, accounts, history and staged work;
back it up before any deliberate full removal. Do not remove recovery volumes casually.

`support/app/` contains only the application, user guides and licence notices.
`support/build/` contains the CLI source needed to build the image; it is not copied
into the running app image. Maintainer instructions, internal TODO files, tests and
package-building utilities are excluded from the runtime. The app's public roadmap
is separate from the maintainer's private TODO.

See `support/app/README.md`, `INSTALL.md` and `AI-INSTALL-PROMPT.md` for account setup,
release workflows and AI-assisted installation. Read `support/app/USAGE-NOTICE.md`
for the required acknowledgment. macOS/ARM64 has not been verified on native hardware.
