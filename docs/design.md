# Monteur — plugin Claude Code de montage auto « dans ta DA »

## Promesse
L'utilisateur donne 2 à 5 vidéos déjà montées (sa DA). Ensuite il pose ses rushs dans un dossier,
dit en une phrase ce qu'il veut, et récupère une vidéo montée dans son style. Il peut corriger en
langage naturel ; chaque correction devient une règle durable.

Cible v1 : reel / TikTok face caméra, vertical 9:16, en français (autres langues OK via Whisper).

## Pourquoi on fait mieux que les packs du marché
1. DA **mesurée par le code** (rythme, silences, zooms, loudness, sous-titres) et pas seulement décrite par l'IA.
2. **Contrôle avant livraison** : `check.py` mesure la vidéo produite et la compare au profil ; hors tolérance → on corrige avant de montrer.
3. **Plan validé** avant rendu (l'utilisateur voit le découpage en texte).
4. **Apprentissage par corrections** (ma-da/STYLE.md, section « Règles apprises »).
5. **Export FCPXML / XML Premiere** pour retoucher dans un vrai logiciel.
6. Transcription **locale et gratuite** (faster-whisper) : aucune clé API, la voix ne quitte pas l'ordi.

## Stack
Python 3.10+ (stdlib + faster-whisper + Pillow + numpy), ffmpeg/ffprobe. Pas de Node, pas de Remotion.
Sous-titres = fichiers .ass brûlés par ffmpeg. Tous les scripts passent par `scripts/common.py`.

## Arborescence du plugin (racine du dépôt)
```
.claude-plugin/plugin.json, marketplace.json
skills/apprendre-ma-da/SKILL.md
skills/monter/SKILL.md
skills/corriger/SKILL.md
scripts/common.py          (fourni)
scripts/doctor.py          vérifie/installe deps ; avec --installer seulement : télécharge Parakeet + Silero, YuNet (repli Whisper)
scripts/transcribe.py      rushs → transcripts JSON + takes.md
scripts/timeline_view.py   PNG filmstrip + waveform + mots pour une plage
scripts/analyze_style.py   vidéos de référence → ma-da/profile.json + images
scripts/render.py          edl.json → mp4
scripts/captions.py        mots → .ass (importé par render.py)
scripts/check.py           mp4 + profile → rapport conforme/non conforme
scripts/export_xml.py      edl.json → .fcpxml + .xml (Premiere xmeml)
tests/                     un test par script (pytest), médias générés par ffmpeg
README.md                  en français, pour un non-développeur
```

## Dossier de travail de l'utilisateur (là où il lance Claude)
```
mes-videos/   ses vidéos déjà montées (références DA)
rushs/        rushs bruts du jour
broll/        (optionnel) plans de coupe
musique/      (optionnel) musiques libres de droits
ma-da/        profile.json, STYLE.md, lexique.txt, frames/   (créé par apprendre-ma-da)
.monteur/     transcripts/, vues/, edl.json, rapports/        (fichiers techniques)
sorties/      vidéos finales + .fcpxml/.xml
```

## Interfaces (contrat entre les modules — ne pas dévier sans le documenter ici)

### Transcript : `.monteur/transcripts/<stem>.json`
```json
{"source": "rushs/a.mp4", "duration": 63.2, "language": "fr", "model": "large-v3-turbo",
 "words": [{"w": "Salut", "start": 0.52, "end": 0.80, "p": 0.97}],
 "silences": [{"start": 3.10, "end": 4.25}]}
```
Nom : `<stem>.json` ; si un autre fichier du même nom a déjà son transcript (ex. `mes-videos/a.mp4` et
`rushs/a.mp4`), `<dossier>_<stem>.json`. Le champ `source` fait foi : les autres scripts retrouvent un transcript
par `common.find_transcript(root, source)` (compare les chemins résolus), jamais par le seul stem.
`<nom>.cache` (à côté) sert au cache de transcribe.py. Un mot / un silence par ligne (lisible avec Read).
Les fragments Whisper (« C » + « 'est », « abonne » + « -toi ») sont recollés en un mot s'ils se touchent (< 0,3 s).
Modèle : `large-v3-turbo` par défaut (décodage séquentiel ; le mode « batched » de faster-whisper saute des
phrases quand on lui passe le lexique), `--rapide` = `small` (~3x plus vite, plus de fautes). Le modèle est
résolu par `common.whisper_model_path` (repli sans liens symboliques sous Windows, WinError 1314).

`.monteur/takes.md` (ou `--takes <chemin>`, ex. pour mes-videos/) : fichiers du dernier lancement seulement, une ligne par phrase :
`[a.mp4 12.34-15.80] Salut tout le monde, aujourd'hui on parle de...`
(phrases = coupées sur ponctuation ou silence > 0.35 s ; un mot à faible confiance marqué `mot?`).
Ligne préfixée `(reprise?) ` si une des 3 phrases suivantes (à moins de 15 s) la recommence (mêmes 3 premiers mots,
ratio difflib > 0.7, ou mêmes 2 premiers mots et début très proche) :
la prise marquée est l'abandonnée, la dernière est en général la bonne.

### Profil DA : `ma-da/profile.json`
```json
{"version": 1, "references": ["mes-videos/v1.mp4"],
 "format": {"width": 1080, "height": 1920, "fps": 30},
 "rythme": {"duree_moyenne_plan_s": 2.1, "mediane_plan_s": 1.8, "coupes_par_minute": 28, "silence_max_s": 0.25},
 "zooms": {"par_minute": 6, "intensite": 1.15, "style": "cut"},
 "sous_titres": {"actif": true, "police": "Montserrat", "gras": true, "taille_pct_hauteur": 0.03,
   "couleur": "#FFFFFF", "couleur_mot_actif": "#FFE600", "contour": "#000000", "epaisseur_contour": 6,
   "position_y_pct": 0.70, "mots_par_bloc": 3, "majuscules": true, "animation": "pop"},
 "audio": {"lufs_cible": -14, "musique": true, "musique_db_sous_voix": -18},
 "broll": {"part_pct": 0.15},
 "hook": {"duree_s": 3},
 "mesures_brutes": {"<fichier>": {"plans": [...], "lufs": -13.8, "duree": 41.0}},
 "a_completer_par_claude": ["sous_titres", "zooms.style", "hook"]}
```
`analyze_style.py` remplit ce qui se mesure (format, rythme, audio, mesures_brutes, estimation zooms)
et met des valeurs par défaut + liste `a_completer_par_claude` pour ce qui demande la vision.
Il exporte aussi `ma-da/frames/<stem>_<t>.jpg` (≈1 image/2 s, max 40) et
`ma-da/frames/<stem>_sheet.jpg` (planche contact) pour que Claude lise sous-titres/couleurs.
`animation` ∈ `pop | rebond | glisse | fondu | karaoke | mot_par_mot | surlignage | aucune` (v3 ; valeur
inconnue → `pop`) ; `couleur_surlignage` = couleur de la boîte du surlignage (défaut `couleur_mot_actif`).
Taille : `taille_pct_hauteur` est tenue au pixel pour toute police embarquée (métriques OS/2 lues par captions.py). `zooms.style` ∈ `cut | doux`.
`taille_pct_hauteur` = hauteur des **capitales** / hauteur de l'image (captions.py convertit en taille de police
libass, ratio 0,45). `taille_pct_hauteur`, `position_y_pct` et `audio.musique` sont **mesurés** par analyze_style
(`mesures_brutes.*.sous_titres_hauteur_pct`, `sous_titres_position_y_pct`, `fond_sonore_db` > -48 dBFS = musique).

### Plan de montage : `.monteur/edl.json`
```json
{"version": 1, "output": "sorties/video.mp4",
 "format": {"width": 1080, "height": 1920, "fps": 30},
 "segments": [{"source": "rushs/a.mp4", "start": 12.34, "end": 15.80,
               "zoom": 1.15, "zoom_center": [0.5, 0.4], "note": "hook"}],
 "captions": true,
 "broll": [{"source": "broll/x.mp4", "at": 3.2, "duration": 1.5, "source_start": 0.0}],
 "music": {"source": "musique/x.mp3", "volume_db": -18},
 "titles": [{"text": "3 erreurs à éviter", "at": 0.0, "duration": 2.0}]}
```
- `segments` dans l'ordre de sortie ; `zoom` 1.0 = pas de zoom ; recadrage 9:16 centré sur `zoom_center` (x,y relatifs).
- `broll.at` / `titles.at` = temps dans la vidéo **finale** ; le b-roll remplace l'image, la voix continue.
- `music` peut être `null` ; ducking sous la voix. Sortie normalisée à `profile.audio.lufs_cible`.
- Les sous-titres sont reconstruits depuis les transcripts (mots dans [start,end] de chaque segment, recalés).
- Coupes : fondu audio 30 ms à chaque raccord ; coupes jamais au milieu d'un mot (render les recale
  sur la fin/début de mot le plus proche si besoin, tolérance 80 ms sauf si ce bord touche le mot voisin).

### Rapport de contrôle : `.monteur/rapports/<stem>.json`
```json
{"conforme": false, "ecarts": [{"critere": "duree_moyenne_plan_s", "attendu": 2.1, "mesure": 3.4,
  "tolerance": "±25%", "conseil": "couper plus serré ou ajouter des zooms"}], "mesures": {...}}
```
Critères v1 : durée moyenne des plans (±25 %), plus long silence (≤ silence_max_s + 0.15),
loudness (±1.5 LU), résolution/fps exacts, sous-titres présents si actif, taille des sous-titres (±25 %),
durée totale vs cible du brief (±15 % si donnée).
**Le même code de mesure sert à analyze_style et à check** (fonctions partagées dans un module), sinon la comparaison n'a pas de sens.

### Règles ajoutées après coup
- `render.py` et `check.py` acceptent `--profil <fichier>` (défaut `ma-da/profile.json`) : `corriger` s'en sert pour un réglage valable une seule vidéo (`.monteur/profile_video.json`).
- Les scripts ignorent les clés de profile.json qu'ils ne connaissent pas (Claude en ajoute : hook.type, style_verbal…).
- Police absente → repli propre sur la police embarquée du plugin, puis Arial.
- Un changement de zoom compte comme un plan pour `check.py` (sinon ajouter des punch-ins ne corrige pas « plans trop longs »).

### Moteur de transcription (v2)
Défaut : **Parakeet TDT 0.6B v3** via onnx-asr (poids CC-BY-4.0, runtime MIT), modèle dans `~/.cache/monteur/parakeet-tdt-0.6b-v3` ; repli automatique Whisper large-v3-turbo. Mots de remplissage marqués `"filler": true`.

## Version 2 — « identique à la personne » (contrat, rétrocompatible avec v1)

Contraintes : 100 % open source / gratuit, tout en local (aucune clé obligatoire, pas de Gemini), CPU sans GPU,
Windows + Mac, plugin Claude Code. Sources des règles : research_notes/Montage vidéo IA automatisé/
(teardown_meilleures_apps.md, briques_open_source.md, api_gratuites_voix_style.md).

### Propriété des fichiers (un seul propriétaire par fichier)
| Lot | Propriétaire | Fichiers |
|---|---|---|
| A Son | ingé son | scripts/audio.py, assets/sfx/ (+LICENCES), tests/test_audio.py |
| B Parole & rythme | ingé parole | scripts/transcribe.py, scripts/pacing.py, tests/test_transcribe.py, tests/test_pacing.py |
| C Image & rendu | ingé image | scripts/render.py, scripts/captions.py, scripts/reframe.py, scripts/export_xml.py, scripts/fonts/, assets/emoji/, tests correspondants |
| D DA & cerveau | analyste DA | scripts/measure.py, scripts/analyze_style.py, scripts/check.py, scripts/doctor.py, skills/*, README.md, tests correspondants |
| — | coordinateur | scripts/common.py, docs/design.md |
Besoin d'un changement dans un fichier d'un autre lot → le demander (message au coordinateur), ne pas l'éditer.

### Interfaces entre lots (fonctions appelées par render.py)
- `pacing.normalize_edl(edl: dict, root: Path, profile: dict) -> dict` (lot B) : appelée en premier par render.
  Recalage sur les mots, respiration, découpe des silences internes, b-roll borné à sa phrase. Écrit aussi
  `.monteur/edl.rendu.json` (ce qui est vraiment rendu) pour export_xml et check.
- `audio.prepare_voice(wav_in, wav_out, mode, profile) -> dict` (lot A) : nettoyage voix (`mode` ∈ auto|leger|fort|aucun) ; renvoie {"applique": bool, "bruit_db":…, "methode":…}.
- `audio.finish_mix(voice_wav, edl_rendu, profile, root, wav_out) -> dict` (lot A) : musique + ducking + bruitages + loudness finale.
  Render produit d'abord la piste voix montée (wav), puis appelle finish_mix, puis muxe avec la vidéo.
- `reframe.face_track(video, t0, t1) -> list[(t, cx, cy, taille)]` (lot C) : centres lissés (repli : dernier centre connu, puis 0.5/0.4).

### Ajouts au plan de montage (edl.json, tous optionnels)
```json
{"version": 2,
 "segments": [{"source": "rushs/a.mp4", "start": 12.34, "end": 15.80,
   "zoom": 1.12, "zoom_type": "punch", "zoom_center": "visage",
   "mots_cles": ["pire", "erreur"], "emoji": "☕", "note": "hook"}],
 "captions": true,
 "broll": [{"source": "broll/x.mp4", "at": 3.2, "duration": 1.5, "source_start": 0.0, "mot": "café"}],
 "sfx": [{"type": "whoosh", "at": 3.1, "volume_db": -14}],
 "music": {"source": "musique/x.mp3", "volume_db": -18},
 "audio": {"nettoyage_voix": "auto"},
 "couleur": "auto"}
```
- `zoom_type` ∈ `aucun | doux` (poussée lente sur tout le segment) `| punch` (rampe 12-18 images, aisée) `| crash` (rampe 4-6 images, 1.25-1.35).
  Absent → v1 (`zoom` statique). `zoom_center` = `"visage"` (défaut v2) ou [x,y].
- `mots_cles` : mots du segment à surligner (couleur `sous_titres.couleur_mot_cle`, 110-120 %) ; max 1 par bloc.
- `sfx.type` = nom de fichier (sans extension) dans assets/sfx/ ; `at` = temps final. Le son commence 3-5 images avant l'image visée.
- `couleur: "auto"` = rapprocher luminosité/contraste/saturation/température des rushs de celles mesurées sur les références (`profile.couleur`) ; `null` = rien.

### Ajouts au profil (profile.json, mesurés par analyze_style quand possible)
```json
{"rythme": {"respiration_intra_s": 0.18, "respiration_phrases_s": 0.38, "coupe_avant_mot_s": 0.08},
 "zooms": {"types": {"doux": 0.5, "punch": 0.4, "crash": 0.1}, "rampe_s": 0.5, "sur_visage": true, "une_coupe_sur": 2},
 "sous_titres": {"couleur_mot_cle": "#FDE047", "mots_cles_par_bloc": 1, "emojis_par_minute": 4, "zone_sure": true},
 "audio": {"sfx_par_minute": 5, "sfx_types": ["whoosh", "pop"], "voix_nettoyee": true, "musique_lufs": -30},
 "couleur": {"luma": 0.52, "contraste": 0.21, "saturation": 0.34, "temperature": 0.03},
 "broll": {"duree_moy_s": 1.8, "jamais_sur_hook": true}}
```
Respiration mesurée **sur les transcripts des références** (écarts entre mots dans ses vidéos montées) : c'est
ce qui rend le tempo identique. Défauts si non mesurable : intra 0.18, phrases 0.38, avant mot 0.08 s.

### Contrôle v2 (check.py) — critères ajoutés
respiration médiane (±30 %), zooms/min (±35 %), part de b-roll (±40 %), sfx/min si la DA en a (±50 %),
écart couleur (luma/saturation ±0.08), aucun mot coupé (vérifié sur edl.rendu + transcripts), pas de b-roll sur hook.

### Lancement des scripts depuis les skills
`python "${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py" ...` — chaque script a `--help`, sort en code ≠ 0
avec un message français clair en cas d'erreur, et imprime un résumé court (Claude lit stdout).

## Version 3 — voix off, rythme en distribution, cadrage, coupes à 10 ms (06/11/26)

Objectif : « je donne mes vidéos, puis un rush + une voix off (+ une consigne), et ça se monte exactement dans mon
style », avec un abonnement Claude à 20 $ (Claude ne fait que le sens ; mesure, rythme, rendu et contrôles sont du code).
Choix écartés : MCP (aucun gain de résultat, la transcription n'est lancée qu'une fois par montage), pilotage d'un
logiciel de montage (fragile, payant ou non pilotable ; l'export FCPXML/xmeml suffit pour retoucher), modèle
image-texte local (Claude lit une planche de 24 plans pour ~2 500 jetons : meilleur et moins lourd à installer).

### Mesures ajoutées (measure.py → profile.json)
- `rythme.plans_quantiles` : durées de plans à 10-25-50-75-90 % (médiane des références) ; `rythme.hook_plan_s` :
  plan médian des 3 premières secondes. Le rythme d'un créateur est une distribution, pas une moyenne.
- `cadrage.visage_largeur` (largeur du visage / largeur de l'image, 30e centile des plans face caméra = cadre de base)
  et `cadrage.visage_y` (hauteur du centre du visage). `audio.true_peak_max_db`, `images_noires_max`.
- `dispersion` couvre aussi plan médian, coupes/min, plan de l'accroche ; check accepte une valeur dans la plage
  min-max de SES références (±5 %) même hors tolérance autour de la médiane.

### Rendu
- Cadrage : zoom de base par segment face caméra = `reframe.base_zoom` (visage ramené à `visage_largeur`, jamais de
  dézoom, borné à 2,0) ; visage placé à `visage_y`. Non exporté dans le FCPXML (shortcut connu).
- Coupes : `pacing.Envelope` (niveau du son par 10 ms) **élargit** une coupe qui mangerait une attaque ou une fin de
  mot mal datée par la transcription ; ne resserre jamais.
- Correction couleur finale mesurée sur ce qui est à l'écran (segments non recouverts + b-roll) et appliquée après
  les b-roll. Un plan plus court que sa place garde sa dernière image (`tpad`).

### Mode voix off (edl `"mode": "voix_off"`)
- `scripts/plans.py rushs broll` → `.monteur/plans.json` (index), `plans.md` (une ligne par plan, description écrite
  par Claude une fois, conservée aux relances), `plans_N.jpg` (planches de 24 vignettes numérotées).
- `scripts/plans.py --voix voix/x.wav` → `.monteur/cadence.md` : créneaux au rythme de la DA (quantiles tirés dans un
  ordre court/long, accroche à `hook_plan_s`), coupes sur des débuts de mots, de préférence entre deux phrases.
- edl : `segments` = la voix (fichier son accepté : fond noir sous la voix) ; `broll` = un plan par créneau
  `{"plan": "P12", "voix_t": <s dans la voix>, "mot": "..", "decalage": 0}`. pacing les met bout à bout sur toute la
  durée (premier à 0, chacun jusqu'au suivant, dernier jusqu'à la fin), sans sortir du plan quand c'est possible.
- check : pas de critère b-roll ni cadrage en voix off ; `images_noires` détecte une voix non couverte.

### Contrôle v3 (check.py)
`plans_courts_s` / `plans_longs_s` (quantiles 25/75 %, ±25 %), `hook_plan_s` (±30 %), `cadrage_visage` (±25 %),
`images_noires`, `saturation_son` (true peak > max(0, références) + 0,1 dB).
