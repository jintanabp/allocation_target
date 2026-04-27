"""
Target Allocation Launcher (Windows)
────────────────────────────────────────────────────────────────────
Goal: 1-click run + auto-update from internal HTTPS URL.

Flow:
1) Check update manifest (latest.json)
2) If newer: download zip, verify sha256, unpack into install dir
3) Pick a free localhost port, start server, open browser

This file is intended to be packaged into an .exe (PyInstaller).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "TargetAllocation"


def _log(msg: str) -> None:
    print(msg, flush=True)

def _log_file() -> Path:
    return _user_data_dir() / "launcher.log"


def _log_to_file(msg: str) -> None:
    try:
        p = _log_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text((p.read_text(encoding="utf-8") if p.is_file() else "") + msg + "\n", encoding="utf-8")
    except Exception:
        pass


def _show_error_popup(title: str, message: str) -> None:
    # Best-effort popup for non-dev users
    try:
        import tkinter  # stdlib
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        pass


def _user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def _install_dir() -> Path:
    # Each version installs into: %LOCALAPPDATA%/TargetAllocation/app
    return _user_data_dir() / "app"


def _state_file() -> Path:
    return _user_data_dir() / "state.json"


def _read_state() -> dict:
    try:
        p = _state_file()
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_state(state: dict) -> None:
    p = _state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f)


def _get_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@dataclass(frozen=True)
class Manifest:
    version: str
    url: str
    sha256: str
    notes: str | None = None


def _fetch_manifest(url: str) -> Manifest:
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    return Manifest(
        version=str(data["version"]),
        url=str(data["url"]),
        sha256=str(data["sha256"]).lower(),
        notes=str(data.get("notes") or "") or None,
    )


def _is_newer_version(latest: str, current: str | None) -> bool:
    if not current:
        return True
    # simple tuple compare: 1.2.3 -> (1,2,3)
    def parse(v: str) -> tuple[int, ...]:
        out = []
        for part in v.strip().split("."):
            try:
                out.append(int(part))
            except Exception:
                out.append(0)
        return tuple(out)

    return parse(latest) > parse(current)


def _unpack_zip(zip_path: Path, dest_dir: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(dest_dir)


def _atomic_replace_dir(src: Path, dst: Path) -> None:
    tmp = dst.parent / (dst.name + ".new")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    if dst.exists():
        # Keep old as backup for quick rollback
        bak = dst.parent / (dst.name + ".bak")
        if bak.exists():
            shutil.rmtree(bak, ignore_errors=True)
        dst.replace(bak)
    src.replace(tmp) if src != tmp else None
    # If src isn't tmp, move src to tmp first
    if not tmp.exists() and src.exists():
        src.replace(tmp)
    tmp.replace(dst)


def ensure_latest(manifest_url: str) -> tuple[Path, str]:
    """
    Returns (app_dir, version).
    The zip must contain the app files at repo root level (Run_Local.bat, backend/, frontend/, runtime/...)
    """
    state = _read_state()
    current_version = state.get("version")
    app_dir = _install_dir()

    _log(f"Checking updates…")
    latest = _fetch_manifest(manifest_url)
    if not _is_newer_version(latest.version, current_version) and app_dir.is_dir():
        return app_dir, str(current_version)

    _log(f"Downloading version {latest.version}…")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        zpath = td / "package.zip"
        _download(latest.url, zpath)
        got = _sha256_file(zpath)
        if got.lower() != latest.sha256.lower():
            raise RuntimeError(f"SHA256 mismatch (got {got[:10]}…, expected {latest.sha256[:10]}…)")

        _log("Installing update…")
        unpack_dir = td / "unpacked"
        unpack_dir.mkdir(parents=True, exist_ok=True)
        _unpack_zip(zpath, unpack_dir)

        # Accept either: zip contains files at root, or a single top-level folder
        children = [p for p in unpack_dir.iterdir()]
        root = unpack_dir
        if len(children) == 1 and children[0].is_dir():
            root = children[0]

        # Move into place
        app_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = app_dir.parent / "app.staging"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(root, staging)
        if app_dir.exists():
            shutil.rmtree(app_dir, ignore_errors=True)
        staging.replace(app_dir)

    state["version"] = latest.version
    _write_state(state)
    return app_dir, latest.version


def run_app(app_dir: Path) -> None:
    port = _get_free_port()
    url = f"http://localhost:{port}/"
    _log(f"Starting server on {url}")

    bat = app_dir / "Run_Local.bat"
    if not bat.is_file():
        raise RuntimeError(f"Run_Local.bat not found in {app_dir}")

    # Start server in separate console window, then open browser
    # Pass port as arg (Run_Local.bat supports it)
    subprocess.Popen(
        ["cmd", "/c", "start", "", "cmd", "/c", f"\"{bat}\" {port}"],
        cwd=str(app_dir),
        shell=False,
    )
    time.sleep(1.5)
    webbrowser.open(url)


def main() -> int:
    # Priority: argv[1] (from .cmd/shortcut) > env
    manifest_url = (sys.argv[1].strip() if len(sys.argv) > 1 else "") or (
        (os.environ.get("TARGET_ALLOC_UPDATE_URL") or "").strip()
    )
    if not manifest_url:
        _log("ERROR: Missing TARGET_ALLOC_UPDATE_URL (URL to latest.json)")
        _log("Ask IT to provide an internal HTTPS URL for updates.")
        return 2
    try:
        app_dir, ver = ensure_latest(manifest_url)
        _log(f"Ready (version {ver}).")
        run_app(app_dir)
        return 0
    except Exception as e:
        msg = str(e)
        _log("ERROR: " + msg)
        _log_to_file(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR: {msg}")
        _show_error_popup("Target Allocation", f"Launcher failed:\n{msg}\n\nLog: {_log_file()}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

