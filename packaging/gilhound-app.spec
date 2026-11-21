import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project = Path(SPECPATH).parent
entry = "stub_entry.py" if os.environ.get("GILHOUND_BUILD_STUB") == "1" else "desktop_entry.py"
analysis = Analysis(
    [str(project / "packaging" / entry)],
    pathex=[str(project)],
    datas=[(str(project / "ui" / "dist"), "ui/dist")],
    hiddenimports=collect_submodules("uvicorn") + ["webview", "webview.platforms.edgechromium"],
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True, name="gilhound-app",
    console=False, upx=False,
)
bundle = COLLECT(exe, analysis.binaries, analysis.datas, name="gilhound-app", upx=False)

