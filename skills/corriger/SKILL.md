---
name: corriger
description: Applique un retour de l'utilisateur sur une vidéo montée par le monteur — sur cette vidéo (re-montage) ou comme règle durable de sa DA. À utiliser quand il réagit à un montage en mots simples, par ex. « c'est trop lent », « trop de zooms », « le son n'est pas propre », « moins de bruitages », « garde ce passage », « garde la blague », « enlève la 3e phrase », « sous-titres plus gros », « pas d'emojis », « musique trop forte », « l'image est trop sombre », « le début ne marche pas », « à chaque fois fais… », « corrige avec mes notes » (page de review), ou quand il redonne sa version retouchée (« voici ma version finale », « je l'ai retouchée dans CapCut / Premiere », une vidéo ou un .xml/.fcpxml exporté).
allowed-tools: Bash(python *) Bash(python3 *) Bash(py *) Read Write Edit Glob
---

# Corriger un montage

Scripts : `python "${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py"` (Mac : `python3`). Pas de ffmpeg direct : pour
regarder, `timeline_view.py sorties/<nom>.mp4 --de 0 --a <durée>` puis Read. Lis d'abord `.monteur/edl.json`,
`.monteur/takes.md`, `ma-da/profile.json`, `ma-da/STYLE.md` et le dernier rapport `.monteur/rapports/<nom>.json`,
puis lance `corrections.py bilan` (ses corrections qui reviennent, voir 3 bis).

**Sa version retouchée** (vidéo finale CapCut…, ou export `.fcpxml`/`.xml` de Premiere, DaVinci, Final Cut) :
`retouches.py <sa version> --ref-plugin sorties/<notre version>.mp4` (notre `.fcpxml`/`.xml` si elle donne un XML ;
seul script lourd du moment). Il liste les différences classées fort / moyen / faible, chacune avec une règle et
son chemin dans profile.json, et les note dans le journal ; il ne touche pas au profil. Dis-lui en mots simples
les **fort** et **moyen** (au plus 4), puis **une seule question groupée** : « Je retiens ces 3 choses pour la
suite ? » ; un faible seulement s'il saute aux yeux. Les lignes « (cette vidéo) » (phrase retirée, plan changé) =
son contenu : pas de règle. Oui → chaque règle retenue passe par 3 (STYLE.md + profile.json + `verrous`, valeur =
`proposition`, sinon la colonne Durable). Non, ou réponse partielle → rien d'autre.

**Notes de la page de review** (« corrige avec mes notes », « j'ai mis mes notes ») :
`review.py sorties/<nom>.mp4 --notes` → chaque note avec son temps et ce qu'il y avait à l'image (morceau de rush,
plan, insert, sous-titre) : c'est ce qui permet de retrouver l'élément exact dans `edl.json` (le morceau de rush
`IMG.mov 62.62-63.32 s` = le segment de mêmes temps source ; un plan = l'entrée `broll` de même `source`). Traite
chaque note comme un retour ci-dessous, toutes dans une seule nouvelle version, puis relance la page sur la `-vN`.

## 1. Cette vidéo ou toujours ?
- **Contenu de cette vidéo** (une phrase, une blague, un passage, le début, un b-roll précis) → cette vidéo seulement.
- **« toujours », « jamais », « à chaque fois », « à l'avenir », « dans mon style »** → règle durable (et
  applique-la aussi à cette vidéo).
- **Réglage de style sans précision** (« trop de zooms », « moins de bruitages ») → cette vidéo, puis demande à
  la livraison, en une phrase : « Je le garde pour les prochaines vidéos ? ».
- Vraiment incompréhensible → une seule question courte, avec deux options concrètes.

**Chaque retour se note**, même « cette vidéo » (c'est ce qui révèle ses habitudes) :
`corrections.py ajoute --categorie <chemin de la colonne Durable, sinon regle:<mot-clé>> --sens -|+ --texte "<ses mots>" --portee video|toujours --video <nom de la vidéo>`.

## 2. Traduire le retour
| Il dit | Cette vidéo (edl.json) | Durable (profile.json) |
|---|---|---|
| trop lent, ça traîne, plus dynamique | retirer redites et « euh », plus de plans (découpe + punch-ins) | `duree_moyenne_plan_s` ×0,85, `respiration_phrases_s` −0,05, `silence_max_s` −0,05 |
| trop rapide, ça saute, on respire pas | moins de découpes, fusionner des segments | ×1,15, `respiration_*` +0,05 |
| trop de zooms / pas assez | retirer / ajouter des zooms (débuts de phrase, mots forts) | `zooms.par_minute` ×0,6 / ×1,5, `une_coupe_sur` +1 / −1 |
| zooms trop forts / trop brusques | `zoom` vers 1,08-1,12 ; `crash` → `punch`, `punch` → `doux` | `zooms.intensite` −0,04 ; baisser `types.crash` |
| son pas propre, souffle, écho | `"audio": {"nettoyage_voix": "fort"}` | `audio.voix_nettoyee: true` + règle « nettoyage fort » |
| voix bizarre, robotique | `"nettoyage_voix": "leger"` (ou `"aucun"`) | idem en règle |
| moins de bruitages / plus / pas de bruitages | retirer des `sfx` (garder entrées de b-roll et crash) / ajouter / `[]` | `audio.sfx_par_minute` ×0,5 / ×1,5 / 0 |
| bruitage trop fort | `volume_db` −4 | règle |
| garde ce passage / la blague / remets X | retrouver X dans takes.md, réinsérer le segment, `"note": "garde"` s'il contient des hésitations à garder | — |
| garde la 1re phrase / la 1re prise | ambigu : 1re phrase du rush (souvent salut/faux départ) ou 1re prise d'une phrase reprise ? Prends celle qui reste dans le sujet et ne casse pas le hook ; dis laquelle et propose l'autre en une ligne | — |
| enlève X, la 3e phrase | retirer le segment (numérotation = plan montré) | — |
| le début ne marche pas | nouveau hook : re-note les candidats (Hook/Flow/Value/Trend, skill monter), autre phrase en tête | `hook.type` / `formule` si « toujours » |
| sous-titres plus gros / petits / ailleurs / autre couleur | — (profil vidéo, voir dessous) | `taille_pct_hauteur` ×1,2 / ×0,85, `position_y_pct`, `couleur*` |
| trop de mots en couleur / pas assez | réduire / ajouter des `mots_cles` | `mots_cles_part_blocs` ×0,6 / ×1,5 |
| pas d'emojis / plus d'emojis | retirer / ajouter des `emoji` | `emojis_par_minute` 0 / ×1,5 |
| trop de b-roll / pas assez | retirer / ajouter des `broll` (sur un mot concret, avec `mot`) | `broll.part_pct` ×0,6 / ×1,5 |
| musique trop forte / pas de musique | `volume_db` −4 dB / `music: null` | `audio.musique_db_sous_voix` −4 / `musique: false` |
| image trop sombre / terne / pas comme d'habitude | `"couleur": "auto"` ; « pas de correction » → `null` | `couleur.luma` / `saturation` ±0,04 |

Tout réglage de style **pour cette vidéo seulement** (zooms, rythme, bruitages, emojis, b-roll, sous-titres,
audio, couleur) : copie `ma-da/profile.json` en `.monteur/profile_video.json`, applique-y le chiffre de la colonne
Durable et passe `--profil .monteur/profile_video.json` à render **et** check — sinon check mesure la vidéo
corrigée contre l'ancien style et la signale hors tolérance. Ne touche pas au profil de l'utilisateur pour un
cas ponctuel.

## 3. Règle durable
1. Ajoute à la fin de `ma-da/STYLE.md`, section « Règles apprises » (crée-la si besoin) :
   `- AAAA-MM-JJ : <règle impérative et testable> (« <ses mots> »)` — date du jour. Une règle plus ancienne qui
   contredit la nouvelle : barre-la (`~~…~~`) sans l'effacer.
2. Mets à jour le chiffre dans `ma-da/profile.json` (colonne Durable) **et ajoute son chemin à `verrous`**
   (ex. `"verrous": ["zooms.par_minute"]`) : une nouvelle analyse de ses vidéos ne l'écrasera plus.
3. Note-la `--portee toujours` (corrections.py) : elle ne sera plus proposée.

## 3 bis. Ce qui revient
`corrections.py bilan` affiche `À PROPOSER : <catégorie> <sens> sur N vidéos (« ses mots »)` : même demande sur
au moins 2 vidéos, pas encore durable. À la livraison, une phrase : « Tu m'as demandé moins de zooms sur deux
vidéos : je le retiens pour la suite ? » Oui → section 3.

## 4. Re-monter et livrer
Modifie `.monteur/edl.json` (nouvelle sortie `-v2`, `-v3`… : ne jamais écraser une version livrée), puis comme
dans la skill monter : `render.py` → `check.py --edl .monteur/edl.json` (2 boucles max) → regarde 3-4 images du
passage corrigé (timeline_view) → `export_xml.py`. Réponds en 3 lignes : ce que tu as changé, si c'est devenu
une règle, le chemin de la nouvelle vidéo et le résultat du contrôle. Ajoute : « Si tu retouches dans
CapCut/Premiere, redonne-moi ta version finale (vidéo ou export XML) : j'apprends de tes retouches. » Jamais « c'est corrigé » sans rapport
de check.py.
