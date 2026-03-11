#!/usr/bin/env python3
"""
Generic app updater for Windows/Unix.

Workflow:
- GET <server>/<app_slug>/<channel>/latest returning JSON per spec.
- If mandatory or newer version/has_update, download zip at download_url.
- Verify size and SHA256 when provided.
- Stop running processes matching launch name/app slug.
- Extract zip to temp, copy into current directory, relaunch launch.exe unless skipped.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from typing import Optional


def log(msg: str) -> None:
    print(f"[app-updater] {msg}")


def fetch_json(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8"))


def download_file(url: str, dest: str, timeout: int = 60) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def sha256sum(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def version_to_int(v: str) -> Optional[int]:
    """
    Convert dotted version to weighted integer: major*10000 + minor*100 + patch...
    This keeps ordering correct for cases like 2.0.0 vs 1.9.10.
    """
    if not v:
        return None
    parts = v.split(".")
    value = 0
    weight = 1
    try:
        for p in reversed(parts):
            num = int(p or 0)
            value += num * weight
            weight *= 100
        return value
    except ValueError:
        return None


def stop_processes(names: list[str]) -> None:
    for name in names:
        if not name:
            continue
        candidates = {name, f"{name}.exe"} if not name.lower().endswith(".exe") else {name}
        for exe in candidates:
            # Windows taskkill; on Unix just pkill best-effort.
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/IM", exe, "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.run(
                    ["pkill", "-f", exe],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )


def copy_tree(src: str, dst: str) -> None:
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_root = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(target_root, exist_ok=True)
        for d in dirs:
            os.makedirs(os.path.join(target_root, d), exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(target_root, f))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generic app updater.")
    parser.add_argument("--server-url", required=True, help="Base API path, no trailing slash.")
    parser.add_argument("--app-slug", required=True, help="Application slug.")
    parser.add_argument("--channel", default="main", help="Update channel/track.")
    parser.add_argument("--current-version", help="Local version for comparison.")
    parser.add_argument("--launch-name", help="Executable name without .exe; defaults to channel.")
    parser.add_argument("--force", action="store_true", help="Force update regardless of version/has_update.")
    parser.add_argument("--skip-start", action="store_true", help="Skip launching after update.")
    parser.add_argument("--timeout", type=int, default=60, help="Network timeout seconds.")
    parser.add_argument("--result-file", default="update-result.json", help="Path to write JSON result log.")
    args = parser.parse_args()

    def write_result(status: str, message: str, extra: dict | None = None) -> None:
        data = {
            "status": status,  # updated | skipped | failed
            "message": message,
            "server_version": None,
            "current_version": args.current_version,
            "channel": args.channel,
            "app_slug": args.app_slug,
            "mandatory": False,
            "forced": bool(args.force),
        }
        if extra:
            data.update(extra)
        try:
            with open(args.result_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"Failed to write result file {args.result_file}: {e}")

    def exit_with(status: str, message: str, code: int, extra: dict | None = None) -> None:
        write_result(status, message, extra)
        if status == "failed":
            log(message)
        sys.exit(code)

    launch_name = args.launch_name or args.channel
    latest_url = "/".join([args.server_url.rstrip("/"), args.app_slug, args.channel, "latest"])

    log(f"Checking update from {latest_url}")
    latest = fetch_json(latest_url, timeout=args.timeout)
    if not latest:
        exit_with("failed", "No data returned from server.", 1)

    mandatory = bool(latest.get("mandatory") or latest.get("latest", {}).get("mandatory"))
    has_update = bool(latest.get("has_update"))

    latest_info = latest.get("latest") or {}
    new_version = latest_info.get("version")

    server_num = version_to_int(new_version) if new_version else None
    current_num = version_to_int(args.current_version) if args.current_version else None

    # Decide force when mandatory and server is newer or unknown.
    if mandatory and (current_num is None or server_num is None or current_num < server_num):
        args.force = True

    if not args.force:
        if current_num is not None and server_num is not None:
            if current_num >= server_num:
                msg = f"Local version ({args.current_version}) is same or newer than server ({new_version}); skip update."
                exit_with("skipped", msg, 0, {"server_version": new_version, "mandatory": mandatory})
        elif not has_update:
            exit_with("skipped", "Server reports no update.", 0, {"server_version": new_version, "mandatory": mandatory})
    else:
        if mandatory:
            log("Mandatory update (forced).")
        else:
            log("Forced update requested.")

    if mandatory:
        log("Mandatory update.")

    dl_path = latest_info.get("download_url")
    if not dl_path:
        exit_with("failed", "download_url missing in response.", 1, {"server_version": new_version, "mandatory": mandatory})

    if not urllib.parse.urlparse(dl_path).scheme:
        download_url = urllib.parse.urljoin(latest_url, dl_path)
    else:
        download_url = dl_path

    zip_name = os.path.basename(urllib.parse.urlparse(download_url).path)
    zip_path = os.path.join(os.getcwd(), zip_name)

    log(f"Downloading: {download_url} -> {zip_path}")
    download_file(download_url, zip_path, timeout=args.timeout)

    expected_size = latest_info.get("size")
    if expected_size is not None:
        actual_size = os.path.getsize(zip_path)
        if int(expected_size) != actual_size:
            exit_with(
                "failed",
                f"File size mismatch (expected {expected_size}, got {actual_size}).",
                1,
                {"server_version": new_version, "mandatory": mandatory},
            )

    expected_hash = latest_info.get("sha256")
    if expected_hash:
        actual_hash = sha256sum(zip_path)
        if actual_hash.lower() != expected_hash.lower():
            exit_with("failed", "SHA256 hash mismatch.", 1, {"server_version": new_version, "mandatory": mandatory})

    stop_processes([launch_name, args.app_slug])

    with tempfile.TemporaryDirectory(prefix="app-update-") as tmp:
        log(f"Extracting to {tmp}")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        log("Copying updated files into current directory")
        copy_tree(tmp, os.getcwd())

    os.remove(zip_path)

    exe_name = f"{launch_name}.exe" if os.name == "nt" else launch_name
    exe_path = os.path.join(os.getcwd(), exe_name)
    if not args.skip_start:
        if os.path.exists(exe_path):
            log(f"Launching {exe_path}")
            if os.name == "nt":
                os.startfile(exe_path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen([exe_path])
            log("Update finished successfully.")
        else:
            log(f"Launch target {exe_path} not found after update.")
    else:
        log("Skip launching as requested.")

    exit_with("updated", "Update finished successfully.", 0, {"server_version": new_version, "mandatory": mandatory})


if __name__ == "__main__":
    sys.exit(main())
