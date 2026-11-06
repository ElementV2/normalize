# Journal des versions

## 1.0.0

Première version.

- Normalisation du volume de plusieurs vidéos au même niveau (EBU R128,
  `loudnorm` de ffmpeg en deux passes), sans ré-encoder la vidéo.
- Préréglages YouTube (-14 LUFS), Web (-16 LUFS) et TV (-23 LUFS), ou niveau
  personnalisé.
- Interface graphique avec glisser-déposer de vidéos et de dossiers.
- Ligne de commande : traitement par lots, en parallèle, mode analyse.
- Exécutable Windows autonome (`NormaliseurVideo.exe`, ffmpeg inclus).
