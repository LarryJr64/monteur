---
name: apprendre-ma-da
description: Apprend la DA (direction artistique) de montage de l'utilisateur à partir de 5 à 10 de ses vidéos déjà montées dans mes-videos/ — rythme, respiration, zooms, sous-titres, mots-clés colorés, emojis, b-roll, bruitages, musique, couleur, hook, CTA, ton — et l'enregistre dans ma-da/. À utiliser quand il dit « apprends mon style », « analyse mes vidéos », « voici ma DA », « apprends comment je monte », « regarde mes vidéos », « mets à jour mon style », « monte comme moi / comme [créateur] », « voici mes inspis », « je veux ce style-là », « ajoute cette vidéo à mon style », ou avant un premier montage si ma-da/profile.json n'existe pas.
allowed-tools: Bash(python *) Bash(python3 *) Bash(py *) Read Write Edit Glob
---

# Apprendre la DA

Tu es un monteur senior qui décortique le style d'un créateur (Hormozi, Gadzhi, Abdaal, créateurs FR…)
pour le reproduire **à l'identique**. Le code mesure, toi tu regardes et tu tranches. L'utilisateur n'est
pas développeur : français simple, pas de jargon ni de chemins inutiles.
Scripts : `python "${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py"` (Mac : `python3` si besoin). Chaque script a
`--help` ; si une option ne passe pas, lis `--help` et adapte.

## 1. Installer / vérifier
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py"
```
Une ligne `[!!]` → demande son accord une fois (« J’installe les outils (ffmpeg, transcription, ~1 Go la
première fois), OK ? »), puis `doctor.py --installer` (délai long : 10 min). Tout s'installe seul. Si ffmpeg
reste introuvable après l'installation : fermer et rouvrir Claude, puis relancer.
`mes-videos/` doit contenir **5 à 10** vidéos montées, représentatives, au format qu'il veut produire. Moins
de 5 : explique que la DA sera moins fiable (2 minimum) ; 0 : voir « Aucune vidéo » plus bas.
**Inspirations** (vidéos d'autres créateurs, pour changer de style) : c'est pareil, dans le dossier qu'il nomme
(`inspi/`…) → `analyze_style.py inspi`. Elles doivent se ressembler entre elles (même style visé) : une vidéo
atypique = une autre DA, propose de la retirer. Le cadrage appris est celui des inspirations : dis-le.
**Aucune vidéo** : propose en une phrase 1 à 3 reels qu'il aime ou un style de départ (`style_depart.py` sans
argument les liste, une ligne chacun) — jamais de questionnaire. **`ma-da/` issu d'un style de départ**
(`"origine": "style de depart"` dans profile.json) : ses références priment. Lance l'analyse normalement (pas
`--force` : ses réglages fixés et ses règles apprises restent), puis retire `origine` et `style_depart` du profil,
refais tous les champs à compléter et réécris STYLE.md d'après ses vidéos en gardant « Règles apprises ».

## 2. Mesurer
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/analyze_style.py" mes-videos
```
Délai long (jusqu'à 10 min : il transcrit les références, ~1 min par minute de vidéo ; `--rapide` si pressé).
Il écrit `ma-da/profile.json` (valeurs mesurées = médianes des références) et dans `ma-da/frames/`, par vidéo :
`<nom>_sheet.jpg` (planche contact), `<nom>_debut.jpg` (0-3 s, le hook), `<nom>_fin.jpg` (3 dernières s, le
CTA) et quelques images isolées. Phrases des références : `.monteur/takes_references.md`.
**La mesure fait foi** : `check.py` compare avec elle. Ne remplace jamais un chiffre mesuré par une
impression. **Seule exception : un faux positif prouvé par l'image** (des « emojis » qui sont en fait les guillemets
jaunes d'une citation, des « mots-clés colorés » qui sont le texte d'un carton ; pas la « part de b-roll » : check.py mesure ta
vidéo avec le même angle mort, garde la valeur mesurée) : corrige la valeur, ajoute le champ à `verrous` (check.py s'y fiera) et écris pourquoi
dans STYLE.md (« emojis 14/min mesurés = guillemets de la citation, aucun emoji réel »).
Le résumé signale une vidéo **atypique** (« ne ressemble pas aux autres ») : demande-lui si c'est voulu ;
sinon qu'il la retire de mes-videos/ et relance.

Ce qui est mesuré (ne pas réinventer) : rythme (plan moyen, coupes/min, plus long silence, **distribution** des
durées de plans `plans_quantiles` 10-25-50-75-90 %, plan médian de l'accroche `hook_plan_s`), **cadrage** (taille du
visage `cadrage.visage_largeur` en part de la largeur, hauteur `visage_y` : le rendu cadre pareil), respiration
(`rythme.respiration_intra_s` / `respiration_phrases_s` / `coupe_avant_mot_s`), zooms (par minute, intensité,
`types` doux/punch/crash/cut, `rampe_s`, `sur_visage`, `une_coupe_sur`, `fin` : le dernier plan est plus large que
l'avant-dernier), sous-titres (présence, taille,
position, couleur, `couleur_mot_cle`, `mots_cles_part_blocs`, `emojis_par_minute`), audio (`lufs_cible` = −14,
diffusion, true peak −1 ; `lufs_reference` = celle du pro, pour info ; musique, `musique_lufs`, `sfx_par_minute`
hors carton, `sfx_types`, `sfx_carton` : bruitages de son carton d'ouverture), `couleur` (luma, contraste,
saturation, température des plans face caméra), b-roll
(`part_pct`, `duree_moy_s`, `jamais_sur_hook`), hook (`duree_s`, `type` deviné, `voix_sous_carton` : entend-on
parler pendant le 1er plan), débit `rythme.debit_mots_s` (mots/s hors hésitations et hors carton muet). `cut` = zoom
sec sur une coupe.
Valeur absente (b-roll sans visage détecté, bruitages…) = défaut : à confirmer à l'œil.

## 3. Regarder (Read sur les .jpg) — ce que le code ne voit pas
Lis toutes les planches `_sheet`, `_debut`, `_fin`, puis 2-4 images isolées par vidéo (sous-titre net,
zoom, b-roll). Note vidéo par vidéo, puis synthétise : **ce qui revient dans la majorité = la DA**, un cas
isolé n'est pas une règle. Champs à remplir (liste `a_completer_par_claude`) :

| Entrée | Ce que tu cherches → champ(s) de profile.json |
|---|---|
| `sous_titres.police` | look de la police → `police` la plus proche parmi celles **embarquées** (rendu identique partout) : Inter Tight (grotesque fine/serrée type SF Pro / Inter → `graisse` 600), Montserrat (ronde : `graisse` 600 si le trait est moyen, 800 s'il est épais, « The Bold Font / CapCut »), Poppins (géométrique, très épaisse, 800), Anton ou Bebas Neue (condensées hautes) ; `graisse` (400/600/800 ; la plus proche embarquée est prise), `interlettrage_pct` (% de l'em : lettres serrées ≈ −2,5, normal 0), `majuscules` ; décris le look dans STYLE.md |
| `sous_titres.couleurs` | confirme `couleur`, `couleur_mot_cle` (mot-clé surligné), `contour`, `epaisseur_contour` (0-8 px en 1080p), `ombre` (nombre, px en 1080p : ombre dure décalée ; un texte est ignoré), `ombre_flou` (px en 1080p : ombre **douce floue** sans contour, ≈ 8-10 → `epaisseur_contour` 0, `ombre` 0), `couleur_mot_actif` (karaoké) ; `mots_cles_part_blocs` ≥ 0,85 = sans doute un mot actif (karaoké) plutôt qu'un mot-clé : tranche |
| `sous_titres.mots_par_bloc` | mots affichés ensemble au plus (1-6) ; `max_caracteres` si le bloc tient toujours sur une ligne courte (compte les caractères des blocs les plus longs, ex. 20) ; `mots_cles_par_bloc` (0-2) |
| `sous_titres.animation` | **lis `<nom>_anim_entree.jpg` (8 images consécutives à l'apparition d'un bloc) et `<nom>_anim_mots.jpg` (une image / 0,1 s)**, puis choisis : `pop` (le bloc grossit un peu en 3 images), `rebond` (grossit, dépasse, revient), `glisse` (monte en apparaissant), `fondu` (apparaît en transparence), `flou` (flou → net en 2-3 images avec un micro-zoom), `karaoke` (tout le bloc visible, le mot dit change de couleur), `mot_par_mot` (les mots apparaissent un à un), `surlignage` (boîte colorée derrière le mot dit → `couleur_surlignage` = couleur de la boîte), `aucune` (apparition sèche). Hésitation : la majorité des vidéos tranche |
| `sous_titres.emojis` | confirme `emojis_par_minute` (0 si aucun) ; ajoute `emojis_style` (au-dessus du texte, sur les émotions / objets…) |
| `zooms.style` | `cut` (le cadre saute) ou `doux` (glisse) — doit coller à `zooms.types` ; **`coupes_echelle`** : sur la planche 2 i/s (étape 3 bis), compte les coupes face caméra dans une même prise (même lieu, même tenue) : le cadre change de taille (serré ↔ large) à la plupart d'entre elles → `true` (le rendu alterne l'échelle à chaque raccord, coupe sèche), la tête saute sans changement de taille → `false`. La mesure ne voit pas ces changements quand la tête bouge (elle n'en compte que quelques-uns) : c'est ton œil qui tranche |
| `hook` | `type` (question, chiffre, choc / « arrête de… », résultat d'abord, histoire), `texte_a_l_ecran` (titre 2-3 s ?), `formule` (structure de la 1re phrase) |
| `broll.usage` | `usage` : quoi (objets dits, captures d'écran, stock, plans tournés), cadrage (plein écran / incrustation), entrée (coupe sèche, whoosh) — détail à l'étape 3 bis |
| `titres` | `titres` : {`actif`, `style` (bandeau, gros texte centré…), `quand` (hook, chiffres, chapitres)} |
| `transitions` | `transitions` : `{"type": "coupe|flash|noir", "couleur": "#..", "quand": "avant un changement de lieu", "par_video": 1}` — repère-les sur la planche 2 i/s de l'étape 3 bis (image unie ou délavée entre deux plans). **Une seule source de vérité** : la sortie d'un graphisme (carton, citation, mosaïque, logo) s'écrit dans `graphismes[].sortie` et **nulle part ailleurs** ; `transitions` ne garde que les transitions entre deux plans filmés, hors graphismes. STYLE.md recopie `graphismes[].sortie` tel quel (même type, même couleur) |
| `style_verbal` | étape 4 |

Remplis avec Edit (pas de script ad hoc), garde les champs mesurés, retire chaque entrée traitée de
`a_completer_par_claude`. Tu peux ajouter des clés ; ne renomme ni ne supprime celles du contrat. Ne touche
pas à `verrous` (réglages imposés par l'utilisateur), sauf pour un faux positif prouvé (voir plus haut) et pour
toute valeur dite « verrouillée » dans STYLE.md : elle est écrite dans profile.json ET ajoutée à `verrous`.

## 3 bis. Habillage (ce qui fait « monté par un pro ») — obligatoire
Les planches de l'étape 2 sautent 1,6 s : un carton ou une citation de 2 s y passe inaperçu. Pour **chaque**
référence : `planche.py <référence>` (2 images/s, secondes affichées) puis Read. Relève tout ce qui n'est pas
l'orateur filmé, avec sa seconde :
- **b-roll** : ses propres vidéos (événements, lieux, coulisses, produits), stock générique, captures d'écran,
  photos ? plein écran ou dans une carte ? un plan par mot ou une phrase entière couverte, clips enchaînés ?
  → `broll.source` ∈ `videos_du_createur | stock | captures | photos | aucun`, `broll.couvre` ∈ `mot | phrase`.
- **graphismes** : carton d'ouverture (fond, anneaux/formes animées, mots qui arrivent, mot fort en couleur),
  citation (guillemets animés, fond assombri), lieu (épingle, nom), logo de marque, chiffre, liste, mosaïque de
  clips dans une carte, transitions (flash, glissé), titres. Pour chacun : quand il arrive (sur quoi dans le
  discours), durée, couleurs exactes, animation.
**Mesure chaque élément de près** : `planche.py <référence> --ips 6 --debut <s - 0,3> --fin <s + durée + 0,3>`
(6 images/s autour de l'élément), puis Read. anim.py lit ces champs (rien n'est lu dans `detail`, qui reste une
note) ; écris-les dans profile.json, un objet par type :
`"graphismes": [{"type": "question|citation|lieu|logo|chiffre|liste|mosaique", "quand": "…", "duree_s": 3,
"detail": "…", "police": "Montserrat", "graisse": 600, "interlettrage_pct": -1.5, "taille_pct": 0.021,
"entree": "disperse", "duree_entree_s": 0.3, "anneaux": "flous", "sortie": "fuite:#F2B36B",
"teinte_fond": "#5A2A55", "sombre_fond": 0.35, "flou_fond": 0, "guillemets": "ronds", "vignette": true,
"sous_titres": false, "rayon": 30, "ombre": 0.8}]`
- `police` / `graisse` / `interlettrage_pct` : celles **des cartons** (mêmes règles que les sous-titres ; absentes =
  celles des sous-titres).
- `taille_pct` : hauteur des capitales des petits mots (question) ou du texte (citation) / hauteur de l'image
  (≈ 0,02 = petit) ; logo : largeur du logo / largeur de l'image (0,55).
- `entree` : mots `monte` (montent un à un depuis le bas) ou `disperse` (arrivent de positions dispersées, flous) ;
  logo `rotation` (grand et tourné → posé), `zoom` (grand → posé) ou `flou` (net depuis flou) ; `duree_entree_s`
  (≈ 0,3 mots, 0,5-0,6 logo).
- `anneaux` (question, mosaïque) : `nets` (fins, concentriques, pointillés), `flous` (halos épais flous décentrés qui
  dérivent), `aucun`.
- `sortie` : `fuite:#hex` (lumière chaude qui monte du bas, floue, puis coupe), `flash:#hex` (aplat de couleur),
  `coupe` (sèche) — la couleur relevée sur l'image.
- citation : `guillemets` `serif` (grands, à empattement) ou `ronds` (petits, gras, sans empattement, ouvrant en bas
  à gauche / fermant en haut à droite) ; fond vidéo `sombre_fond` (0-1, part d'assombrissement), `flou_fond`
  (0 = net), `teinte_fond` (couleur qui teinte l'orateur assombri).
- logo : `vignette` true si le blanc grisonne lentement sur les bords.
- `sous_titres` (chaque type) : sur la planche 6 i/s, les sous-titres de l'orateur **restent-ils** pendant l'élément ?
  `true` / `false`. Absent : gardés sur une mosaïque sans texte propre, masqués sur ceux qui affichent leur propre
  texte (carton, citation, logo avec texte…). Pour les plans filmés (b-roll plein écran) :
  `sous_titres.sur_broll` (`true` par défaut, `false` si la référence les retire sur ses b-roll).
- mosaïque : `rayon` (px en 1080 de large, coins de la carte ; carré = 0) et `ombre` (opacité 0-1 de l'ombre
  portée sous la carte, 0 = carte plate) — relevés sur une image pleine résolution de la carte.
- **`sombre_fond` se mesure, ne se devine pas** : luma moyenne (Pillow, image entière en gris) d'une image de
  l'élément (fond vidéo + texte) et d'une image de l'orateur juste avant, dans la référence ;
  `sombre_fond` = 1 − luma élément / luma orateur (0,67 → 0,33). `teinte_fond` seulement si les canaux R, G, B ne
  baissent pas du même rapport (sinon absent : pas de voile coloré) ; rejoue la mesure sur l'insert rendu.
Et `hook.fond` / `hook.couleur_mot` si un carton ouvre (fond = couleur relevée au centre du carton).
Chaque type relevé = une recette `anim.py` (le gabarit du même nom) dans « Coups de signature » de STYLE.md,
écrite pour être refaite telle quelle, **sans les options que le profil porte déjà** : « Citation forte →
`anim.py citation "<phrase>" --fond <rush> --fond-debut <s> --profil ma-da/profile.json` (orateur assombri,
guillemets jaunes animés), 1,9 s, sans sous-titres ». Ce que les
gabarits ne savent pas faire : décris-le quand même (le monteur le signalera).

## 4. Style parlé (Read `.monteur/takes_references.md`)
Relève, mot pour mot quand c'est utile : les 1res phrases (formules de hook), la longueur des phrases,
tu/vous, tics gardés au montage, la structure (problème → solution, liste « 3 erreurs », histoire, avant/après),
la durée typique, la fin (CTA « abonne-toi », « commente X », « lien en bio », chute sèche). Ajoute :
`"style_verbal": {"duree_typique_s": .., "formules_hook": [..], "structure": "..", "cta": [..], "fin": "cta|chute|question", "registre": "tu|vous"}`.
`structure` liste chaque temps de la référence, y compris les phrases d'évaluation et les connecteurs gardés
(« parce que », « du coup ») : le monteur ne coupe pas ce que la référence garde.

## 5. Lexique
`ma-da/lexique.txt` : un terme par ligne (noms propres, marques, prénoms, anglicismes, jargon, mots `mot?`
que tu sais corriger). Garde les lignes existantes.

## 6. STYLE.md
Écris `ma-da/STYLE.md` (crée ou mets à jour, **sans jamais toucher à « Règles apprises »**) :
```
# Ma DA
## Observations       chiffres mesurés + ce que tu as vu (« un plan toutes les 1,8 s », « mot-clé jaune #FDE047 »)
## Règles de montage  impératives, chiffrées, testables :
                      « pause entre phrases 0,35 s, jamais de silence > 0,25 s »,
                      « zoom toutes les 2 coupes : 50 % punch ×1,12, 40 % doux, 10 % crash sur les chiffres »,
                      « 1 mot-clé jaune sur ~1 bloc sur 3 (chiffres, négations) », « 1 emoji / 15 s »,
                      « b-roll 1,8 s sur les objets dits, jamais sur le hook », « whoosh à chaque b-roll »…
## Coups de signature 2-4 gestes qui le rendent reconnaissable, décrits pour être refaits, avec la recette
                      anim.py de chaque graphisme (étape 3 bis) et le type de b-roll (« ses vidéos de soirées,
                      une phrase entière, clips enchaînés »)
## Style parlé        formules de hook, structure, CTA
## Règles apprises    (ajoutées par les corrections, datées — ne jamais effacer)
```

## 7. Valider avec lui
Résume « ta DA » en 6 à 8 puces en mots de tous les jours (« Rythme très rapide : une coupe toutes les
2 secondes », « Un zoom sur une coupe sur deux, plus fort sur les chiffres », « Sous-titres blancs en capitales,
le mot important en jaune », « Un petit bruit de whoosh à chaque image d'illustration », « Tu ouvres par un
chiffre choc »), plus tes coups de signature (habillage compris : « carton animé d'ouverture », « tes propres vidéos d'événements
en illustration ») et l'éventuelle vidéo atypique. Si la DA utilise ses propres vidéos : « Pour tes montages, dépose
aussi 5-10 clips de tes événements/lieux dans broll/. »
**Bruitages** : les bruitages repérés hors parole (carton, trous) sont fiables : garde-les sans demander et
annonce-le (« j'entends des whooshes sur ton carton, je les garde » ; `audio.sfx_carton`, `sfx_par_minute`).
Seule l'estimation sous la voix demande confirmation : si tu crois qu'il en met aussi sous la voix (le code ne
les entend pas là), une seule question : « Tu mets aussi des bruitages pendant que tu parles (whoosh, pop) ?
Jamais, parfois ou souvent ? » → `audio.sfx_par_minute` ≈ mesure / ~3 de plus / ~12, et ajoute
`audio.sfx_par_minute` à `verrous`. Sans réponse : la mesure hors parole. STYLE.md dit la même chose que le
profil : n'y écris « pas de bruitage » que si `audio.sfx_par_minute` vaut 0 (check.py lit ce verrou dans STYLE.md
et ne contrôle plus les bruitages). Termine par : « C'est bien
ça ? Dis-moi ce qui ne va pas, sinon dépose tes rushs dans rushs/ et dis-moi ce que tu veux. » S'il corrige :
applique (profile.json + STYLE.md) sans relancer l'analyse, et ajoute le champ corrigé à `verrous` pour qu'une
future analyse ne l'écrase pas.

## 8. Ajouter une vidéo (sans tout refaire)
Un reel qu'il a validé ou publié, ou une nouvelle inspi : `analyze_style.py --ajoute <vidéo>` (`--type inspi` pour
une vidéo d'un autre créateur ; défaut `mien`). Seule cette vidéo est mesurée ; les médianes du profil sont
recalculées **pondérées** (`sources` de profile.json : vidéo, type, date, poids) — ses reels comptent double face
aux inspirations, une source de moins de 90 jours double face aux plus anciennes. `verrous` et champs confirmés
ne bougent jamais. Dis-lui en une ligne ce qui a bougé (le résumé affiché), sans questionnaire ; ne refais les
étapes 3-6 que si le résumé montre un vrai changement de style.

