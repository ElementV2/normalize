"""Coeur de l'application : mesure et normalisation du volume via ffmpeg.

La normalisation utilise le filtre ``loudnorm`` de ffmpeg (norme EBU R128)
en deux passes :

1. une passe d'analyse mesure la sonie intégrée (LUFS), le pic vrai (dBTP)
   et la plage de sonie (LRA) de la piste audio ;
2. une seconde passe applique la correction en réutilisant ces mesures,
   ce qui permet une correction linéaire (un simple gain, sans « pomper »).

Le flux vidéo est copié tel quel (aucune perte de qualité, traitement rapide),
seule la piste audio est ré-encodée.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm",
    ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".3gp",
}

# Préréglages de sonie usuels (LUFS intégrés)
PRESETS = {
    "youtube": -14.0,   # YouTube, Spotify, la plupart des plateformes web
    "web": -16.0,       # Podcasts, Apple, valeur par défaut raisonnable
    "broadcast": -23.0, # Télévision (EBU R128)
}

DEFAULT_TARGET_I = -16.0
DEFAULT_TARGET_TP = -1.5
DEFAULT_TARGET_LRA = 11.0

ProgressCallback = Callable[[float], None]


class NormalizeError(RuntimeError):
    """Erreur levée lorsqu'une étape ffmpeg/ffprobe échoue."""


@dataclass
class Loudness:
    """Mesures de sonie d'une piste audio."""

    integrated: float      # LUFS
    true_peak: float       # dBTP
    lra: float             # LU
    threshold: float       # LUFS
    offset: float = 0.0    # LU (target_offset calculé par loudnorm)

    def __str__(self) -> str:
        return (f"{self.integrated:6.1f} LUFS | pic {self.true_peak:5.1f} dBTP"
                f" | LRA {self.lra:4.1f} LU")


@dataclass
class MediaInfo:
    duration: float
    has_audio: bool
    audio_codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None


@dataclass
class Settings:
    target_i: float = DEFAULT_TARGET_I
    target_tp: float = DEFAULT_TARGET_TP
    target_lra: float = DEFAULT_TARGET_LRA
    audio_bitrate: str = "192k"
    audio_codec: Optional[str] = None  # None = choisi selon le conteneur
    overwrite: bool = False


@dataclass
class Result:
    source: Path
    output: Optional[Path] = None
    before: Optional[Loudness] = None
    after: Optional[Loudness] = None
    skipped: bool = False
    error: Optional[str] = None
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and not self.skipped


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def tool_path(name: str) -> str:
    """Chemin de ``ffmpeg`` ou ``ffprobe``.

    Ordre de recherche :
    1. variable d'environnement ``NORMALIZE_FFMPEG`` / ``NORMALIZE_FFPROBE`` ;
    2. copie intégrée à l'exécutable (PyInstaller : dossier ``ffmpeg/``) ;
    3. à côté de l'exécutable ou dans un sous-dossier ``ffmpeg/`` ;
    4. le PATH.
    """
    env = os.environ.get(f"NORMALIZE_{name.upper()}")
    if env:
        return env
    exe = name + (".exe" if os.name == "nt" else "")
    bases = []
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            bases.append(Path(sys._MEIPASS))  # type: ignore[attr-defined]
        bases.append(Path(sys.executable).resolve().parent)
    for base in bases:
        for cand in (base / "ffmpeg" / exe, base / exe):
            if cand.is_file():
                return str(cand)
    return shutil.which(name) or name


def check_ffmpeg() -> None:
    """Vérifie que ffmpeg et ffprobe sont disponibles."""
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(tool_path(b)) is None]
    if missing:
        raise NormalizeError(
            "Introuvable : " + ", ".join(missing) + ".\n"
            "Installez ffmpeg (https://ffmpeg.org/download.html) et assurez-vous "
            "qu'il est accessible dans le PATH."
        )


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        **_no_window(),
    )


def _no_window() -> dict:
    """Évite l'ouverture d'une console sous Windows (mode GUI)."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
    return {}


def find_videos(paths: list[str | Path], recursive: bool = False) -> list[Path]:
    """Retourne la liste des vidéos à partir de fichiers et/ou dossiers."""
    found: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            found.extend(sorted(f for f in it
                                if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS))
        elif p.is_file():
            found.append(p)
        else:
            raise NormalizeError(f"Chemin introuvable : {p}")
    # Supprime les doublons en conservant l'ordre
    seen: set[Path] = set()
    unique = []
    for f in found:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def probe(path: Path) -> MediaInfo:
    """Lit la durée et les caractéristiques de la première piste audio."""
    proc = _run([
        tool_path("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if proc.returncode != 0:
        raise NormalizeError(f"ffprobe a échoué : {proc.stderr.strip()}")
    data = json.loads(proc.stdout or "{}")
    duration = float(data.get("format", {}).get("duration") or 0.0)
    audio = next((s for s in data.get("streams", [])
                  if s.get("codec_type") == "audio"), None)
    if audio is None:
        return MediaInfo(duration=duration, has_audio=False)
    return MediaInfo(
        duration=duration,
        has_audio=True,
        audio_codec=audio.get("codec_name"),
        sample_rate=int(audio["sample_rate"]) if audio.get("sample_rate") else None,
        channels=audio.get("channels"),
    )


def _loudnorm_filter(s: Settings, measured: Optional[Loudness] = None) -> str:
    f = f"loudnorm=I={s.target_i}:TP={s.target_tp}:LRA={s.target_lra}"
    if measured is not None:
        f += (f":measured_I={measured.integrated}"
              f":measured_TP={measured.true_peak}"
              f":measured_LRA={measured.lra}"
              f":measured_thresh={measured.threshold}"
              f":offset={measured.offset}"
              ":linear=true")
    return f + ":print_format=json"


_JSON_BLOCK = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def _parse_loudnorm(stderr: str, prefix: str) -> Loudness:
    matches = _JSON_BLOCK.findall(stderr)
    if not matches:
        raise NormalizeError("Impossible de lire les mesures de loudnorm :\n"
                             + stderr[-1500:])
    d = json.loads(matches[-1])

    def num(key: str) -> float:
        v = d[f"{prefix}_{key}"]
        # Le silence total renvoie "-inf"
        return float(v) if v not in ("-inf", "inf", "nan") else float("-inf")

    return Loudness(
        integrated=num("i"),
        true_peak=num("tp"),
        lra=num("lra"),
        threshold=num("thresh"),
        offset=float(d.get("target_offset", 0.0)),
    )


def _run_with_progress(cmd: list[str], duration: float,
                       progress: Optional[ProgressCallback]) -> tuple[int, str]:
    """Lance ffmpeg en suivant sa progression (``-progress pipe:1``)."""
    cmd = cmd[:1] + ["-nostdin", "-progress", "pipe:1", "-nostats"] + cmd[1:]
    stderr_chunks: list[str] = []
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", **_no_window(),
    ) as proc:
        assert proc.stdout is not None and proc.stderr is not None
        stderr = proc.stderr

        # Lecture de stderr dans un thread pour éviter tout blocage du pipe
        t = threading.Thread(target=lambda: stderr_chunks.extend(stderr), daemon=True)
        t.start()

        for line in proc.stdout:
            if progress and duration > 0 and line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                progress(max(0.0, min(1.0, us / 1e6 / duration)))
        proc.wait()
        t.join()
    return proc.returncode, "".join(stderr_chunks)


# ---------------------------------------------------------------------------
# API principale
# ---------------------------------------------------------------------------

def measure(path: Path, settings: Settings | None = None,
            progress: Optional[ProgressCallback] = None,
            info: Optional[MediaInfo] = None) -> Loudness:
    """Passe 1 : mesure la sonie de la première piste audio."""
    settings = settings or Settings()
    info = info or probe(path)
    if not info.has_audio:
        raise NormalizeError("Aucune piste audio dans ce fichier.")
    cmd = [tool_path("ffmpeg"), "-hide_banner", "-i", str(path),
           "-map", "0:a:0", "-af", _loudnorm_filter(settings),
           "-vn", "-sn", "-dn", "-f", "null", "-"]
    code, err = _run_with_progress(cmd, info.duration, progress)
    if code != 0:
        raise NormalizeError(f"Analyse impossible :\n{err[-1500:]}")
    return _parse_loudnorm(err, "input")


def _choose_audio_codec(output: Path, settings: Settings) -> list[str]:
    if settings.audio_codec:
        codec = settings.audio_codec
    else:
        ext = output.suffix.lower()
        codec = {
            ".webm": "libopus",
            ".avi": "libmp3lame",
            ".wmv": "wmav2",
            ".flv": "aac",
        }.get(ext, "aac")
    args = ["-c:a", codec]
    if codec not in ("pcm_s16le", "pcm_s24le", "flac"):
        args += ["-b:a", settings.audio_bitrate]
    return args


def default_output_path(source: Path, output_dir: Optional[Path] = None,
                        suffix: str = "_normalise") -> Path:
    if output_dir is None:
        return source.with_name(f"{source.stem}{suffix}{source.suffix}")
    return output_dir / source.name


def plan_outputs(files: list[Path], output_dir: Optional[Path] = None,
                 suffix: str = "_normalise") -> list[Path]:
    """Calcule les chemins de sortie d'un lot, sans collision.

    Avec un dossier de sortie, deux vidéos de même nom venant de dossiers
    différents s'écraseraient : la seconde devient « nom (2).mp4 », etc.
    """
    used: set[str] = set()
    outputs = []
    for f in files:
        out = default_output_path(f, output_dir, suffix)
        n = 2
        while str(out.resolve()).lower() in used:
            base = default_output_path(f, output_dir, suffix)
            out = base.with_name(f"{base.stem} ({n}){base.suffix}")
            n += 1
        used.add(str(out.resolve()).lower())
        outputs.append(out)
    return outputs


def normalize(source: Path, output: Path, settings: Settings | None = None,
              progress: Optional[ProgressCallback] = None,
              log: Optional[Callable[[str], None]] = None) -> Result:
    """Normalise une vidéo : mesure (passe 1) puis correction (passe 2).

    La progression rapportée va de 0 à 1 sur l'ensemble des deux passes.
    """
    settings = settings or Settings()
    result = Result(source=source, output=output)

    def say(msg: str) -> None:
        result.messages.append(msg)
        if log:
            log(msg)

    def sub_progress(offset: float, scale: float) -> Optional[ProgressCallback]:
        if progress is None:
            return None
        return lambda x: progress(offset + x * scale)

    try:
        if output.resolve() == source.resolve():
            raise NormalizeError("Le fichier de sortie ne peut pas être le fichier source.")
        if output.exists() and not settings.overwrite:
            result.skipped = True
            say(f"Ignoré (existe déjà) : {output}")
            return result

        info = probe(source)
        if not info.has_audio:
            result.skipped = True
            say(f"Ignoré (pas de piste audio) : {source.name}")
            return result

        result.before = measure(source, settings, sub_progress(0.0, 0.4), info)
        say(f"Avant : {result.before}")

        if result.before.integrated == float("-inf"):
            result.skipped = True
            say("Ignoré : la piste audio est silencieuse.")
            return result

        output.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = str(info.sample_rate or 48000)
        cmd = [
            tool_path("ffmpeg"), "-hide_banner", "-y", "-i", str(source),
            # On garde la vidéo, la 1re piste audio (normalisée), les sous-titres
            # et les métadonnées / chapitres.
            "-map", "0:v?", "-map", "0:a:0", "-map", "0:s?",
            "-map_metadata", "0", "-map_chapters", "0",
            "-c:v", "copy", "-c:s", "copy",
            "-af", _loudnorm_filter(settings, result.before),
            *_choose_audio_codec(output, settings),
            # loudnorm sur-échantillonne à 192 kHz : on revient à la fréquence d'origine
            "-ar", sample_rate,
        ]
        if output.suffix.lower() in (".mp4", ".m4v", ".mov"):
            cmd += ["-movflags", "+faststart"]
        cmd.append(str(output))

        code, err = _run_with_progress(cmd, info.duration, sub_progress(0.4, 0.6))
        if code != 0:
            # Certains sous-titres ne sont pas copiables dans le conteneur cible :
            # on retente sans eux.
            if "-map" in cmd and "0:s?" in cmd:
                i = cmd.index("0:s?")
                retry = cmd[:i - 1] + cmd[i + 1:]
                code, err = _run_with_progress(retry, info.duration,
                                               sub_progress(0.4, 0.6))
                if code == 0:
                    say("Attention : sous-titres non conservés (format incompatible).")
            if code != 0:
                if output.exists():
                    output.unlink()
                raise NormalizeError(f"Encodage impossible :\n{err[-1500:]}")

        try:
            result.after = _parse_loudnorm(err, "output")
            say(f"Après : {result.after}")
        except NormalizeError:
            pass

        if progress:
            progress(1.0)
        say(f"OK -> {output}")
    except NormalizeError as exc:
        result.error = str(exc)
        say(f"ERREUR : {exc}")
    except Exception as exc:  # pragma: no cover - sécurité
        result.error = f"{type(exc).__name__}: {exc}"
        say(f"ERREUR : {result.error}")
    return result
