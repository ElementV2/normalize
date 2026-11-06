"""Point d'entrée de l'exécutable Windows (PyInstaller).

- Double-clic : ouvre l'interface graphique.
- Vidéos glissées sur l'icône de l'exe : l'interface s'ouvre avec ces vidéos.
- ``--smoke-test FICHIER.json`` : auto-test utilisé par la CI après la
  compilation (vérifie ffmpeg intégré, glisser-déposer et une normalisation
  complète), puis écrit le résultat dans FICHIER.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path


def smoke_test(report: Path) -> int:
    result: dict = {"ok": False}
    try:
        from normalize_video import gui
        from normalize_video.core import (
            _no_window, check_ffmpeg, measure, normalize, tool_path,
        )

        check_ffmpeg()
        result["ffmpeg"] = tool_path("ffmpeg")
        result["ffprobe"] = tool_path("ffprobe")
        # Dans l'exe, on doit utiliser le ffmpeg intégré, pas celui du système
        bundle = getattr(sys, "_MEIPASS", None)
        result["bundled_ffmpeg"] = bool(bundle) and all(
            Path(result[t]).resolve().is_relative_to(Path(bundle).resolve())
            for t in ("ffmpeg", "ffprobe"))

        tmp = Path(tempfile.mkdtemp())
        src = tmp / "vidéo test.mp4"
        subprocess.run([
            tool_path("ffmpeg"), "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15",
            "-f", "lavfi", "-i", "sine=f=440,volume=0.03",
            "-t", "3", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(src),
        ], check=True, capture_output=True, **_no_window())
        res = normalize(src, tmp / "sortie.mp4")
        result["before"] = res.before.integrated if res.before else None
        result["after"] = measure(tmp / "sortie.mp4").integrated if res.ok else None
        result["error"] = res.error

        app = gui.App([str(src)])
        app.update()
        result["dnd"] = app.dnd
        result["files_loaded"] = len(app.files)
        app.destroy()

        frozen_ok = result["bundled_ffmpeg"] or not getattr(sys, "frozen", False)
        result["ok"] = (res.ok and frozen_ok and result["dnd"] and result["files_loaded"] == 1
                        and abs(result["after"] + 16.0) <= 1.0)
    except Exception:
        result["traceback"] = traceback.format_exc()
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if result["ok"] else 1


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--smoke-test":
        return smoke_test(Path(args[1]))
    from normalize_video.gui import main as gui_main
    return gui_main(args)


if __name__ == "__main__":
    sys.exit(main())
