# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Futures Desk console.

Build from the repository root::

    pip install pyinstaller
    pyinstaller packaging/futures-desk.spec

The result is ``dist/futures-desk/`` - a folder, not a single file, and that is
the whole point of this spec.

WHY A FOLDER AND NOT ``--onefile``
----------------------------------
A one-file build unpacks itself into a temporary directory on every run and
deletes it on exit. Anything the application writes there is gone when it
closes, and anything it reads there is whatever was frozen at build time. For
this application that would mean the risk configuration silently reverting to
the build's defaults every time it restarted - the single worst failure mode a
risk tool can have, because the numbers on screen would stop being the numbers
in force.

The folder build puts ``futures-desk`` (or ``futures-desk.exe``) at the top of a
directory the user owns. ``futures_agents.ui.paths.project_root`` resolves to
that directory via ``sys.executable``, never ``sys._MEIPASS``, so:

    dist/futures-desk/
        futures-desk[.exe]      <- the executable
        desk-config.json        <- your risk configuration      (external)
        desk-account.json       <- your account state           (external)
        assets/                 <- the UI itself                (external)
        data/                   <- journal database             (external)
        _internal/              <- Python runtime; do not edit

Everything outside ``_internal/`` is editable with a text editor and re-read on
the next request. Nothing about the desk's behaviour is compiled in.

The UI assets are ALSO copied into the bundle as a fallback, so a user who
deletes ``assets/`` still gets a working screen rather than an error - but the
external copy always wins when present. See ``ProjectPaths.assets_dir``.
"""

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
ROOT = os.path.abspath(os.getcwd())

# The bundled fallback copy of the UI. The launcher prefers the external
# `assets/` beside the executable and only falls back to this.
datas = [
    (os.path.join(ROOT, "futures_agents", "ui", "assets"),
     os.path.join("futures_agents", "ui", "assets")),
]

# The risk and configuration layers are imported normally, but the agent roster
# is probed reflectively by `staffing()`, so its modules are named explicitly.
hiddenimports = (
    collect_submodules("futures_agents.risk")
    + collect_submodules("futures_agents.ui")
    + ["futures_agents.config", "futures_agents.schema", "futures_agents.timeutil"]
)

a = Analysis(
    [os.path.join(ROOT, "packaging", "desk_entry.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trimmed because the console needs none of them and they add tens of
    # megabytes. The deterministic risk path is pure standard library.
    excludes=["tkinter", "matplotlib", "numpy.random._examples", "pytest",
              "IPython", "notebook", "PIL", "PySide6", "PyQt5"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="futures-desk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A console window is kept on purpose: the startup banner prints which
    # folder the application is reading, which is the first thing anyone needs
    # when a number looks wrong. It is also where Ctrl-C goes.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="futures-desk",
)
