# Version history

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
