"""Interface en ligne de commande."""

from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import __version__
from .core import (
    DEFAULT_TARGET_LRA, DEFAULT_TARGET_TP, PRESETS, NormalizeError, Settings,
    check_ffmpeg, default_output_path, find_videos, measure, normalize, probe,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="normalize-video",
        description="Normalise le volume sonore de plusieurs vidéos pour qu'elles "
                    "aient toutes le même niveau (EBU R128, ffmpeg loudnorm).",
        epilog="Exemples :\n"
               "  normalize-video *.mp4\n"
               "  normalize-video dossier/ -o sortie/ --preset youtube\n"
               "  normalize-video dossier/ --analyse\n"
               "  normalize-video --gui",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("entrees", nargs="*", metavar="FICHIER_OU_DOSSIER",
                   help="Vidéos ou dossiers contenant des vidéos.")
    p.add_argument("-o", "--sortie", type=Path, metavar="DOSSIER",
                   help="Dossier de sortie. Par défaut, les fichiers sont créés à "
                        "côté des originaux avec le suffixe '_normalise'.")
    p.add_argument("-r", "--recursif", action="store_true",
                   help="Parcourt les sous-dossiers.")

    g = p.add_argument_group("niveau cible")
    g.add_argument("-p", "--preset", choices=sorted(PRESETS),
                   help="youtube = -14 LUFS, web = -16 LUFS, broadcast = -23 LUFS.")
    g.add_argument("-i", "--lufs", type=float, metavar="LUFS",
                   help="Sonie intégrée cible (défaut : -16).")
    g.add_argument("--pic", type=float, default=DEFAULT_TARGET_TP, metavar="dBTP",
                   help=f"Pic vrai maximal (défaut : {DEFAULT_TARGET_TP}).")
    g.add_argument("--lra", type=float, default=DEFAULT_TARGET_LRA, metavar="LU",
                   help=f"Plage de sonie cible (défaut : {DEFAULT_TARGET_LRA}).")

    e = p.add_argument_group("encodage")
    e.add_argument("--codec-audio", metavar="CODEC",
                   help="Codec audio (défaut : aac, ou libopus pour .webm).")
    e.add_argument("--debit-audio", default="192k", metavar="DÉBIT",
                   help="Débit audio (défaut : 192k).")
    e.add_argument("-y", "--ecraser", action="store_true",
                   help="Écrase les fichiers de sortie existants.")
    e.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                   help="Nombre de vidéos traitées en parallèle (défaut : 1).")

    p.add_argument("-a", "--analyse", action="store_true",
                   help="Mesure seulement le niveau de chaque vidéo, sans rien modifier.")
    p.add_argument("--gui", action="store_true", help="Ouvre l'interface graphique.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def make_settings(args: argparse.Namespace) -> Settings:
    if args.lufs is not None:
        target = args.lufs
    elif args.preset:
        target = PRESETS[args.preset]
    else:
        target = Settings().target_i
    return Settings(
        target_i=target, target_tp=args.pic, target_lra=args.lra,
        audio_bitrate=args.debit_audio, audio_codec=args.codec_audio,
        overwrite=args.ecraser,
    )


def run_analysis(files: list[Path], settings: Settings) -> int:
    width = max(len(f.name) for f in files)
    errors = 0
    for f in files:
        try:
            info = probe(f)
            if not info.has_audio:
                print(f"{f.name:<{width}}  (pas de piste audio)")
                continue
            print(f"{f.name:<{width}}  {measure(f, settings, info=info)}")
        except NormalizeError as exc:
            errors += 1
            print(f"{f.name:<{width}}  ERREUR : {str(exc).splitlines()[0]}")
    return 1 if errors else 0


def run_normalize(files: list[Path], settings: Settings,
                  output_dir: Path | None, jobs: int) -> int:
    lock = threading.Lock()
    total = len(files)
    print(f"Cible : {settings.target_i} LUFS, pic {settings.target_tp} dBTP, "
          f"LRA {settings.target_lra} LU — {total} vidéo(s)\n")

    def task(idx: int, f: Path):
        out = default_output_path(f, output_dir)
        res = normalize(f, out, settings)
        with lock:
            print(f"[{idx}/{total}] {f.name}")
            for m in res.messages:
                print("    " + m.replace("\n", "\n    "))
        return res

    results = []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(task, i, f) for i, f in enumerate(files, 1)]
        for fut in as_completed(futures):
            results.append(fut.result())

    ok = sum(r.ok for r in results)
    skipped = sum(r.skipped for r in results)
    failed = sum(r.error is not None for r in results)
    print(f"\nTerminé : {ok} normalisée(s), {skipped} ignorée(s), {failed} erreur(s).")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.gui or not args.entrees:
        # Sans argument : on lance l'interface graphique si Tkinter est disponible
        try:
            from .gui import main as gui_main
        except ImportError as exc:
            if args.gui:
                print(f"Interface graphique indisponible ({exc}). "
                      "Installez Tkinter (ex. : sudo apt install python3-tk).",
                      file=sys.stderr)
            else:
                parser.print_help()
            return 2
        return gui_main()

    try:
        check_ffmpeg()
        files = find_videos(args.entrees, args.recursif)
    except NormalizeError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    if not files:
        print("Aucune vidéo trouvée.", file=sys.stderr)
        return 2

    settings = make_settings(args)
    if args.analyse:
        return run_analysis(files, settings)
    return run_normalize(files, settings, args.sortie, args.jobs)


if __name__ == "__main__":
    sys.exit(main())
