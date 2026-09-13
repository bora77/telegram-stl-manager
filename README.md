# Telegram STL

A local web interface for manual Telegram archive downloads, monthly creator
folders, image extraction and durable download history. The folder organizer
can match existing archives, move them into monthly folders and record them as
already downloaded.

Downloads and folder organization can run together, with independent Stop
buttons. Work on the same artist/month is coordinated; other releases proceed
in parallel. A local CLI service shares one login across independent requests,
so organizer previews can read metadata while a file is downloading. Stopping
one job leaves the other running. The service starts on demand and exits after
one idle minute; it needs no second login or desktop session.

Auto normally reuses comparisons for up to six hours on the current network.
Configuration also offers a comparison before each artist/month: the first large
attachment tests the servers with two-second samples, then the remaining files
and archive parts reuse that result for the manual run. Another data center gets
its own comparison. Small files use a recent result until a large file is available;
failed connections can trigger another test. Connection setup adds time beyond
the samples, so a comparison can take more than ten seconds. It indicates current
throughput, not a guaranteed speed.

After each artist's initial scan, Download all checks only messages newer than
its last successful check. The private `data/telegram-catalog.sqlite3` retains
older file metadata for pending downloads and changes to the selected starting
month. Interrupted scans do not advance the cursor. An organizer preview reads
the full topic and refreshes older metadata, including edited posts.

The application uses Python, JavaScript and a customized Go CLI. It runs without
AI, OCR, Telegram Desktop or a VNC session.

- [INSTALL.md](INSTALL.md): Git/download setup, dependencies, Linux and Windows via WSL2.
- [AI-INSTALL-PROMPT.md](AI-INSTALL-PROMPT.md): setup prompt for a local AI agent.
- [CATALOG.md](CATALOG.md): application workflow and operation.
- [IMAGE-WORKFLOW.md](IMAGE-WORKFLOW.md): image extraction and drag-and-drop collages.

No Telegram source or real creator catalog is bundled. Each installation stores
its authorized source and catalog privately under `data/`. Run
`python3 tools/init-local.py` for blank local templates and follow INSTALL.md.
The web interface is at http://127.0.0.1:6093/ after starting the server.

Before sharing code, run `python3 tools/package-release.py --check`. To create a
downloadable source archive, run `python3 tools/package-release.py`. Both use
`distribution.json`; local data, generated pages, credentials, build caches and
session notes are excluded. Keep Git's public-file allowlist intact.

For development, enable the included commit check once per clone:

```bash
git config --local core.hooksPath .githooks
```

The check validates staged files against the public distribution manifest and
rejects private/generated data and references to the locally configured Telegram
source. It checks the actual staged content, including partially staged edits.
You can also run it manually with `python3 tools/check-git.py` before committing.
