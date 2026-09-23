# -*- mode: python ; coding: utf-8 -*-
#
# The ONE PyInstaller spec for this project (replaces the older,
# now-deleted MathMemeSpire.spec left over from before the project was
# renamed). Build with build_exe.bat, or directly via:
#     pyinstaller ChadMathSpire.spec
#
# NOTE: `datas` bundles the assets/ folder into the .exe, exactly as
# polyhedral_spire.py's own top-of-file comment calls for (resource_path()
# resolves assets/ via sys._MEIPASS inside the frozen build). Skipping
# this is why the old spec, run as-is, would have shipped an .exe that
# fell back to procedural art/fonts for everything -- assets/ was never
# actually being packaged.

a = Analysis(
    ['polyhedral_spire.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ChadMathSpire',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
