#!/usr/bin/env python3
"""Oyez Desktop - the browser page in a window of its own.

    oyez-desktop                      open the window
    oyez-desktop --install            add Start menu and desktop shortcuts (Windows)
    oyez-desktop --remove-shortcuts   take them away again

The page and its server run in this one process, so closing the window stops
both. It needs pywebview, an optional extra, so the MCP server itself keeps
its two dependencies:

    uv tool install "oyez-mcp[desktop] @ git+https://github.com/attorneynate/oyez-mcp"

or `pip install pywebview` in a clone's venv. On Windows the window is
WebView2, which Windows 11 includes.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn

import ui

APP_NAME = "Oyez Browser"
# Windows groups a running window with the shortcut that has the same ID, so
# the taskbar shows this app's icon rather than Python's.
APP_ID = "attorneynate.OyezBrowser"
INSTALL_HINT = ('Install it with:\n  uv tool install "oyez-mcp[desktop] @ '
                'git+https://github.com/attorneynate/oyez-mcp"\nor, in a clone\'s venv, '
                "pip install pywebview")


def _data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".local", "share")
    path = Path(base) / "oyez-mcp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _icon_path() -> Path:
    """Write the embedded icon where the window and the shortcuts can find it."""
    path = _data_dir() / "oyez.ico"
    data = base64.b64decode(ICON)
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)
    return path


def _say(message: str, error: bool = False) -> None:
    """Print, or under pythonw, where there is no console, show a message box."""
    if sys.stdout is not None:
        print(message, file=sys.stderr if error else sys.stdout)
    elif sys.platform == "win32":
        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, flags)


def run() -> None:
    try:
        import webview
    except ImportError:
        _say("Oyez Desktop needs pywebview. " + INSTALL_HINT, error=True)
        sys.exit(1)

    # A shortcut starts this under pythonw, with no console. Send what the
    # server would have printed to a log, overwritten on each start.
    if sys.stderr is None:
        sys.stdout = sys.stderr = open(_data_dir() / "desktop.log", "w", encoding="utf-8")
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    port = ui._free_port(0)
    srv = uvicorn.Server(uvicorn.Config(ui.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):  # up to ten seconds
        if srv.started or not thread.is_alive():
            break
        time.sleep(0.1)
    if not srv.started:
        log = _data_dir() / "desktop.log"
        _say("The page's server did not start."
             + (f" Details are in {log}" if log.is_file() else ""), error=True)
        sys.exit(1)

    webview.create_window(
        APP_NAME, f"http://127.0.0.1:{port}/", width=1200, height=860, min_size=(640, 480),
        text_select=True,  # so a quote can be copied out of a transcript
        background_color="#FBFAF7",
    )
    webview.start(icon=str(_icon_path()))  # returns when the window closes
    srv.should_exit = True
    thread.join(timeout=5)


def _shortcut_paths() -> list[Path]:
    from win32com.shell import shell, shellcon

    folders = (shellcon.CSIDL_PROGRAMS, shellcon.CSIDL_DESKTOPDIRECTORY)
    return [Path(shell.SHGetFolderPath(0, f, None, 0)) / f"{APP_NAME}.lnk" for f in folders]


def install_shortcuts() -> None:
    """Start menu and desktop shortcuts that open the window with no console.

    They point at this Python environment and this file, so make them from
    the install you mean to keep: a clone's venv, or `uv tool install`, not a
    one-off `uvx` run whose cached environment can be cleared.
    """
    if sys.platform != "win32":
        _say("Shortcuts are made on Windows only. Elsewhere, run oyez-desktop.", error=True)
        sys.exit(1)
    try:
        import webview  # noqa: F401
    except ImportError:
        _say("Install pywebview before making shortcuts. " + INSTALL_HINT, error=True)
        sys.exit(1)
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pythonw = Path(sys.executable)
    script = Path(__file__).resolve()
    icon = _icon_path()
    for path in _shortcut_paths():
        link = pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
        link.SetPath(str(pythonw))
        link.SetArguments(f'"{script}"')
        link.SetWorkingDirectory(str(script.parent))
        link.SetIconLocation(str(icon), 0)
        link.SetDescription("Supreme Court cases, decisions, and arguments from Oyez")
        store = link.QueryInterface(propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID, pythoncom.VT_LPWSTR))
        store.Commit()
        link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), 0)
        print(f"Made {path}")


def remove_shortcuts() -> None:
    if sys.platform != "win32":
        return
    for path in _shortcut_paths():
        if path.is_file():
            path.unlink()
            print(f"Removed {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="oyez-desktop", description="Browse Oyez's Supreme Court archive in a window of its own.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true",
                       help="add Start menu and desktop shortcuts (Windows)")
    group.add_argument("--remove-shortcuts", action="store_true",
                       help="remove the shortcuts --install made")
    args = parser.parse_args()
    if args.install:
        install_shortcuts()
    elif args.remove_shortcuts:
        remove_shortcuts()
    else:
        run()


# The app icon, a .ico with 16 to 256 pixel images: a courthouse front on the
# page's accent red. Kept here as base64 because the package is plain modules,
# with no folder to ship a data file in; _icon_path() writes it out.
ICON = """
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
"""


if __name__ == "__main__":
    main()
