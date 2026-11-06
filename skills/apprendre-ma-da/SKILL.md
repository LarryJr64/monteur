---
name: apprendre-ma-da
description: Apprend la DA (direction artistique) de montage de l'utilisateur à partir de 5 à 10 de ses vidéos déjà montées dans mes-videos/ — rythme, respiration, zooms, sous-titres, mots-clés colorés, emojis, b-roll, bruitages, musique, couleur, hook, CTA, ton — et l'enregistre dans ma-da/. À utiliser quand il dit « apprends mon style », « analyse mes vidéos », « voici ma DA », « apprends comment je monte », « regarde mes vidéos », « mets à jour mon style », « monte comme moi / comme [créateur] », ou avant un premier montage si ma-da/profile.json n'existe pas.
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
de 5 : explique que la DA sera moins fiable (2 minimum) ; 0 : dis-lui d'en déposer et arrête-toi.

## 2. Mesurer
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/analyze_style.py" mes-videos
```
Délai long (jusqu'à 10 min : il transcrit les références, ~1 min par minute de vidéo ; `--rapide` si pressé).
Il écrit `ma-da/profile.json` (valeurs mesurées = médianes des références) et dans `ma-da/frames/`, par vidéo :
`<nom>_sheet.jpg` (planche contact), `<nom>_debut.jpg` (0-3 s, le hook), `<nom>_fin.jpg` (3 dernières s, le
CTA) et quelques images isolées. Phrases des références : `.monteur/takes_references.md`.
**La mesure fait foi** : `check.py` compare avec elle. Ne remplace jamais un chiffre mesuré par une
impression ; si une image contredit une mesure, dis-le dans STYLE.md et garde la mesure.
Le résumé signale une vidéo **atypique** (« ne ressemble pas aux autres ») : demande-lui si c'est voulu ;
sinon qu'il la retire de mes-videos/ et relance.

Ce qui est mesuré (ne pas réinventer) : rythme (plan moyen, coupes/min, plus long silence), respiration
(`rythme.respiration_intra_s` / `respiration_phrases_s` / `coupe_avant_mot_s`), zooms (par minute, intensité,
`types` doux/punch/crash/cut, `rampe_s`, `sur_visage`, `une_coupe_sur`), sous-titres (présence, taille,
position, couleur, `couleur_mot_cle`, `mots_cles_part_blocs`, `emojis_par_minute`), audio (LUFS, musique,
`musique_lufs`, `sfx_par_minute`, `sfx_types`), `couleur` (luma, contraste, saturation, température), b-roll
(`part_pct`, `duree_moy_s`, `jamais_sur_hook`), hook (`duree_s`, `type` deviné). `cut` = zoom sec sur une coupe.
Valeur absente (b-roll sans visage détecté, bruitages…) = défaut : à confirmer à l'œil.

## 3. Regarder (Read sur les .jpg) — ce que le code ne voit pas
Lis toutes les planches `_sheet`, `_debut`, `_fin`, puis 2-4 images isolées par vidéo (sous-titre net,
zoom, b-roll). Note vidéo par vidéo, puis synthétise : **ce qui revient dans la majorité = la DA**, un cas
isolé n'est pas une règle. Champs à remplir (liste `a_completer_par_claude`) :

| Entrée | Ce que tu cherches → champ(s) de profile.json |
|---|---|
| `sous_titres.police` | look de la police → `police` la plus proche parmi Montserrat, Poppins, Inter, Bebas Neue, Anton, Oswald, Roboto, Arial Black, Impact (« The Bold Font / CapCut » → Montserrat ; condensée haute → Anton/Bebas) ; `gras`, `majuscules` ; décris le look dans STYLE.md |
| `sous_titres.couleurs` | confirme `couleur`, `couleur_mot_cle` (mot-clé surligné), `contour`, `epaisseur_contour` (0-8 px en 1080p), `couleur_mot_actif` (karaoké) ; `mots_cles_part_blocs` ≥ 0,85 = sans doute un mot actif (karaoké) plutôt qu'un mot-clé : tranche |
| `sous_titres.mots_par_bloc` | mots affichés ensemble (1-6) ; `mots_cles_par_bloc` (0-2) |
| `sous_titres.animation` | **lis `<nom>_anim_entree.jpg` (8 images consécutives à l'apparition d'un bloc) et `<nom>_anim_mots.jpg` (une image / 0,1 s)**, puis choisis : `pop` (le bloc grossit un peu en 3 images), `rebond` (grossit, dépasse, revient), `glisse` (monte en apparaissant), `fondu` (apparaît en transparence), `karaoke` (tout le bloc visible, le mot dit change de couleur), `mot_par_mot` (les mots apparaissent un à un), `surlignage` (boîte colorée derrière le mot dit → `couleur_surlignage` = couleur de la boîte), `aucune` (apparition sèche). Hésitation : la majorité des vidéos tranche |
| `sous_titres.emojis` | confirme `emojis_par_minute` (0 si aucun) ; ajoute `emojis_style` (au-dessus du texte, sur les émotions / objets…) |
| `zooms.style` | `cut` (le cadre saute) ou `doux` (glisse) — doit coller à `zooms.types` |
| `hook` | `type` (question, chiffre, choc / « arrête de… », résultat d'abord, histoire), `texte_a_l_ecran` (titre 2-3 s ?), `formule` (structure de la 1re phrase) |
| `broll.usage` | `usage` : quoi (objets dits, captures d'écran, stock, plans tournés), cadrage (plein écran / incrustation), entrée (coupe sèche, whoosh) |
| `titres` | `titres` : {`actif`, `style` (bandeau, gros texte centré…), `quand` (hook, chiffres, chapitres)} |
| `transitions` | `transitions` : coupe sèche (défaut pro), flash, glissé, zoom de transition |
| `style_verbal` | étape 4 |

Remplis avec Edit (pas de script ad hoc), garde les champs mesurés, retire chaque entrée traitée de
`a_completer_par_claude`. Tu peux ajouter des clés ; ne renomme ni ne supprime celles du contrat. Ne touche
pas à `verrous` (réglages imposés par l'utilisateur).

## 4. Style parlé (Read `.monteur/takes_references.md`)
Relève, mot pour mot quand c'est utile : les 1res phrases (formules de hook), la longueur des phrases,
tu/vous, tics gardés au montage, la structure (problème → solution, liste « 3 erreurs », histoire, avant/après),
la durée typique, la fin (CTA « abonne-toi », « commente X », « lien en bio », chute sèche). Ajoute :
`"style_verbal": {"duree_typique_s": .., "formules_hook": [..], "structure": "..", "cta": [..], "fin": "cta|chute|question", "registre": "tu|vous"}`.

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
## Coups de signature 2-3 gestes qui le rendent reconnaissable, décrits pour être refaits
                      (« crash zoom + impact sur le chiffre clé », « hook = résultat chiffré en 1 phrase, coupe sèche »)
## Style parlé        formules de hook, structure, CTA
## Règles apprises    (ajoutées par les corrections, datées — ne jamais effacer)
```

## 7. Valider avec lui
Résume « ta DA » en 6 à 8 puces en mots de tous les jours (« Rythme très rapide : une coupe toutes les
2 secondes », « Un zoom sur une coupe sur deux, plus fort sur les chiffres », « Sous-titres blancs en capitales,
le mot important en jaune », « Un petit bruit de whoosh à chaque image d'illustration », « Tu ouvres par un
chiffre choc »), plus tes 2-3 coups de signature et l'éventuelle vidéo atypique.
**Bruitages** : le code ne les entend pas de façon fiable sous la voix (estimation seulement). Pose une seule
question : « Tu mets des bruitages (whoosh, pop…) ? Jamais, parfois (sur les images d'illustration) ou
souvent (presque à chaque coupe) ? » → `audio.sfx_par_minute` = 0 / ~3 / ~12 (garde l'estimation si elle
tombe dans la bonne fourchette), `sfx_types` si il les nomme, et ajoute `audio.sfx_par_minute` à `verrous`.
Sans réponse : 0 (pas de bruitage, jamais un bruitage qu'il n'a pas voulu). Termine par : « C'est bien
ça ? Dis-moi ce qui ne va pas, sinon dépose tes rushs dans rushs/ et dis-moi ce que tu veux. » S'il corrige :
applique (profile.json + STYLE.md) sans relancer l'analyse, et ajoute le champ corrigé à `verrous` pour qu'une
future analyse ne l'écrase pas.
