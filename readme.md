CLI flags
- `--server-url` (required) : Base API path.
- `--app-slug` (required)  : Application slug.
- `--channel`              : Track (default `main`).
- `--current-version`      : Local version for comparison.
- `--launch-name`          : Executable name (without `.exe`; defaults to channel).
- `--force`                : Install even if server says no update or version is older.
- `--skip-start`           : Do not launch after update.
- `--timeout`              : Network timeout seconds (default 60).
- `--result-file`          : JSON log path (default `update-result.json`) for caller app to read then delete.
- `--stop-process`         : Extra process names to stop before copying files; repeat the flag for multiple helpers.
- `--preserve-json`        : Keep old json


Behavior
- Fetches `<server>/<app_slug>/<channel>/latest`.
- Installs if `mandatory` is true, `has_update` is true, or newer version detected.
- Resolves relative `download_url`, downloads zip to current dir, verifies size/SHA256 when provided.
- Stops running processes named `<launch>`, `<launch>.exe`, `<app_slug>`, plus any provided via `--stop-process`.
- Extracts zip to temp, copies into current directory, deletes zip, and relaunches `<launch>.exe` unless `--skip-start`.
- Writes result JSON with status/message/versions to `--result-file` for host app; host should read and remove it.

python -m PyInstaller --onefile --name app-updater updater.py
git