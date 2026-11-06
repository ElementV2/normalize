# normalize — même volume sonore pour toutes vos vidéos

Petite application Python qui **normalise le son de plusieurs vidéos** pour
qu'elles aient toutes le même niveau perçu. Fini les vidéos où il faut
monter le son, puis la suivante qui vous explose les oreilles.

- Mesure en **LUFS** (norme EBU R128), comme YouTube, Spotify ou la TV
- Normalisation en **2 passes** (`loudnorm` de ffmpeg) : correction linéaire, sans effet de « pompage »
- **La vidéo n'est pas ré-encodée** (copie directe) : rapide et sans perte de qualité
- Interface **graphique** et **ligne de commande**, traitement par lots, en parallèle
- Aucune dépendance Python : seulement Python ≥ 3.9 et **ffmpeg**

## Installation

1. Installer **ffmpeg** et vérifier qu'il est dans le PATH (`ffmpeg -version`) :
   - Windows : `winget install ffmpeg`
   - macOS : `brew install ffmpeg`
   - Linux : `sudo apt install ffmpeg`
2. Récupérer le projet, puis (optionnel) l'installer :
   ```bash
   pip install .
   ```

## Interface graphique

```bash
python -m normalize_video          # ou : normalize-video-gui
```

Ajoutez des vidéos ou un dossier, choisissez le niveau cible, cliquez sur
**Normaliser**. Le tableau affiche le niveau avant / après de chaque vidéo.

> Sous Linux, Tkinter peut nécessiter : `sudo apt install python3-tk`.

## Ligne de commande

```bash
# Normalise toutes les vidéos d'un dossier (fichiers "*_normalise.mp4" créés à côté)
python -m normalize_video mes_videos/

# Vers un dossier de sortie, niveau YouTube, 4 vidéos en parallèle
python -m normalize_video mes_videos/ -o sortie/ --preset youtube -j 4

# Niveau personnalisé
python -m normalize_video a.mp4 b.mov c.mkv --lufs -18

# Juste mesurer le niveau actuel, sans rien modifier
python -m normalize_video mes_videos/ --analyse
```

| Option | Description |
|---|---|
| `-o DOSSIER` | Dossier de sortie (sinon suffixe `_normalise` à côté de l'original) |
| `-r` | Parcourt les sous-dossiers |
| `-p youtube\|web\|broadcast` | -14, -16 (défaut) ou -23 LUFS |
| `-i LUFS` / `--lufs` | Niveau cible personnalisé |
| `--pic dBTP` | Pic vrai maximal (défaut -1.5) |
| `--lra LU` | Plage de sonie cible (défaut 11) |
| `--codec-audio`, `--debit-audio` | Encodage audio (défaut AAC 192k, Opus pour .webm) |
| `-j N` | Nombre de vidéos traitées en parallèle |
| `-y` | Écrase les fichiers de sortie existants |
| `-a` / `--analyse` | Mesure seulement |

## Quel niveau choisir ?

| Usage | Cible |
|---|---|
| YouTube, réseaux sociaux, Spotify | **-14 LUFS** |
| Web, podcasts, usage général | **-16 LUFS** (défaut) |
| Télévision (EBU R128) | **-23 LUFS** |

L'important pour avoir un volume homogène est d'utiliser **la même cible pour
toutes les vidéos**.

## Bon à savoir

- Seule la **première piste audio** est conservée et normalisée ; la vidéo,
  les sous-titres, métadonnées et chapitres sont copiés.
- Les vidéos sans piste audio (ou totalement silencieuses) sont ignorées.
- Les originaux ne sont jamais modifiés.

## Tests

```bash
python -m unittest -v
```
