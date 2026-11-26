# Telegram STL Manager for Windows

Copyright © 2026 trumphater77

Extract the entire ZIP into a folder before running a launcher.

- **Install-Windows.cmd**: install the app and its dedicated WSL environment.
  Read and acknowledge the usage notice, then accept the administrator prompt.
  If a restart is needed, installation resumes at the next sign-in.
- **Start-Windows.cmd**: start the installed app and open its web interface.
- **Stop-Windows.cmd**: stop the app and its transfers, with confirmation.
- **Update-Windows.cmd**: update an existing installation using this package.
  Finish active tasks first and use the same Windows account. No reinstall or
  reset is needed. Settings, accounts, subscriptions and history are preserved.
- **Uninstall-Windows.cmd**: remove the app, preserving external download files.

Configure the app at http://127.0.0.1:6093 in your normal Windows browser.
Use the desktop/tray shortcut for everyday startup. Completed installer or WSL
welcome windows may be closed; no PowerShell window needs to remain open.
Logs are under `%LOCALAPPDATA%\TelegramSTLManager\logs`.

Leave `support/` intact; its files are used by the launchers. Updates remain
optional. Existing versions can also use the app's in-app updater, which uses a
compatibility update payload with its original filenames.

See the bundled INSTALL.md and AI-INSTALL-PROMPT.md for detailed instructions.
