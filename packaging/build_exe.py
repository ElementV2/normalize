"""Construit l'exécutable autonome avec PyInstaller.

Usage :
    pip install pyinstaller tkinterdnd2
    python packaging/build_exe.py --ffmpeg-dir CHEMIN_VERS_DOSSIER_FFMPEG

Le dossier ffmpeg doit contenir ffmpeg(.exe), ffprobe(.exe) et, pour une
version « shared », les DLL associées. Tout est intégré dans l'exécutable,
qui n'a donc besoin de rien d'autre pour fonctionner.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
NAME = "NormaliseurVideo"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg-dir", type=Path, required=True)
    ap.add_argument("--onedir", action="store_true",
                    help="Dossier au lieu d'un fichier unique (démarrage plus rapide).")
    args = ap.parse_args()

    ffdir = args.ffmpeg_dir.resolve()
    ext = ".exe" if os.name == "nt" else ""
    for tool in ("ffmpeg", "ffprobe"):
        if not (ffdir / f"{tool}{ext}").is_file():
            raise SystemExit(f"{tool}{ext} introuvable dans {ffdir}")

    binaries = []
    for f in sorted(ffdir.iterdir()):
        if f.name in (f"ffmpeg{ext}", f"ffprobe{ext}") or f.suffix.lower() == ".dll":
            binaries += ["--add-binary", f"{f}{os.pathsep}ffmpeg"]

    opts = [
        str(ROOT / "packaging" / "launcher.py"),
        "--name", NAME,
        "--onedir" if args.onedir else "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--paths", str(ROOT),
        "--collect-all", "tkinterdnd2",
        "--add-data", f"{ROOT / 'normalize_video' / 'assets'}{os.pathsep}normalize_video/assets",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        *binaries,
    ]
    icon = ROOT / "packaging" / "icon.ico"
    if icon.is_file():
        opts += ["--icon", str(icon)]
    version_file = ROOT / "packaging" / "version_info.txt"
    if os.name == "nt" and version_file.is_file():
        opts += ["--version-file", str(version_file)]
    PyInstaller.__main__.run(opts)


if __name__ == "__main__":
    main()
