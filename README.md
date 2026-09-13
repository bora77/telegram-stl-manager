# Telegram STL

A local web interface for manual Telegram archive downloads, monthly creator
folders, image extraction and durable download history. The folder organizer
can match existing archives, move them into monthly folders and record them as
already downloaded.

Downloads and folder organization can run together, with independent Stop
buttons. Work on the same artist/month is coordinated; other releases proceed
in parallel. Telegram metadata scans share the login between file transfers.

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
