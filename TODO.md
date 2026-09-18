# TODO

## Self-updater

- [ ] Choose the GitHub repository for public, versioned release packages. Keep private test packages and credentials separate.
- [ ] Check periodically for newer releases and show **Update available** in Configuration. Installation starts only when the user clicks **Install update**; users do not need Git installed.
- [ ] Wait for downloads, organizing, packaging and other active jobs to finish before updating, and prevent new jobs during installation.
- [ ] Download and verify the update package before applying it.
- [ ] Back up the database, settings and previous application version; handle database migrations and restore the matching backup if startup fails.
- [ ] Replace application files, restart the server and verify that it starts successfully.
- [ ] Preserve sessions, subscriptions, history and downloaded files. Never replace or delete external storage.
- [ ] Update Windows installations inside their existing WSL environment without reinstalling Ubuntu.
- [ ] Support Linux runtime updates and document Docker updates by replacing the container while retaining persistent data volumes.
- [ ] Test successful updates, interrupted updates and rollback before enabling distribution.

Status: planned, not implemented.
