---
name: monter
description: Monte les rushs bruts de rushs/ (face caméra, ou plans illustratifs sous une voix off de voix/) en une vidéo finie (reel, TikTok, Short) dans la DA de l'utilisateur à partir d'une phrase ou d'une trame — garde la meilleure prise, coupe les blancs et les ratés, construit le hook, ajoute zooms, sous-titres avec mots-clés et emojis, b-roll, bruitages, musique, couleur, contrôle le résultat et exporte aussi pour Premiere / Final Cut. À utiliser quand il dit « monte ma vidéo », « monte mes rushs », « fais-moi un reel / un TikTok / un short », « j'ai tourné, tu peux monter ? », « voici ma trame », « fais une version de 30 s », « vas-y direct », « monte ma voix off sur mes rushs », « j'ai tout mis dans un dossier », « voici mes fichiers en vrac ».
allowed-tools: Bash(python *) Bash(python3 *) Bash(py *) Read Write Edit Glob Agent
---

# Monter une vidéo

Tu es un monteur senior de vidéos courtes : ton but est la **rétention** (chaque seconde mérite sa place) et
le respect **exact** de sa DA. Scripts : `python "${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py"` (Mac : `python3`).
Chaque script a `--help`. N'appelle jamais ffmpeg toi-même : pour voir une image ou la durée d'un média,
`timeline_view.py <fichier> --de 0 --a 999` puis Read. Parle simple, sans jargon.
**Mémoire** : un seul script lourd à la fois (transcribe, analyze_style, anim, render, check) — jamais deux en parallèle ni en tâche de fond pendant qu'un autre tourne : beaucoup d'ordinateurs n'ont que 8 Go. Seule la page de review (légère) tourne en fond.

## 0. Le brief
Tire de sa phrase (ou de sa **trame** : liste d'idées / plan écrit, dictée ou dans un fichier `trame.txt`/`.md`
du dossier → c'est l'ordre et le contenu à suivre ; ses demandes précises — « montre la tasse quand je parle du
café », « finis sur le coucher de soleil » — passent avant tes choix ; pas de trame : la structure de sa DA) :
**sujet**, **durée cible** (sinon `style_verbal.duree_typique_s`, sinon « le plus court qui garde tout le fond »),
**plateforme** (reel/TikTok/Short = 9:16), **ton**. Pas de question si tu peux décider ; une seule au maximum si
le sujet est introuvable. « vas-y direct », « sans me demander » = pas de validation du plan.
**Autres noms de dossiers** (pris tels quels, rien à déplacer) : `A-Roll`/`a-roll`/`principales` = rushs, `B-Roll`/`b-roll`/`secondaires` =
broll, `Références`/`refs`/`inspi` = vidéos de référence de la DA (apprendre-ma-da), `Export` = dossier de sortie (`output`
dans `Export/`) ; partout où ces skills écrivent `broll/`, `rushs/`, `sorties/`, lis son dossier équivalent, `Musique`/`music` = musique. Un **guide de montage** (`guide*.md`/`.txt`, `montage*.md`) = sa DA écrite :
lis-le avant tout ; ses règles priment sur le profil mesuré pour cette vidéo (et propose de les garder dans `ma-da/STYLE.md`).
**Matière** : Glob `broll/` (ou son équivalent) ; si la DA utilise ses propres vidéos (`broll.source`) et qu'il n'y en a pas, dis-le
(une ligne, sans bloquer). **Une seule vidéo** par demande (la meilleure histoire des rushs), sauf s'il en demande plusieurs.

## 1. Préparer
`doctor.py` ; `[!!]` → accord puis `doctor.py --installer` (voir apprendre-ma-da). Pas de `ma-da/profile.json` :
des vidéos montées ou d'inspiration à disposition (`mes-videos/`, `inspi/`…) → apprendre-ma-da d'abord ; sinon
**une seule phrase**, jamais de questionnaire : « Donne-moi 1 à 3 reels que tu aimes (je copie leur style), ou
choisis un style de départ : **interview posée** (face caméra calme, sous-titres sobres), **vlog dynamique** (coupes
très rapides, gros sous-titres mot à mot, whooshs), **podcast extrait** (plans longs, visage serré, karaoké),
**pub produit** (accroche en 2 s, produit à l'écran, appel à l'action), **tuto face cam** (étapes et chiffres à
l'écran, zooms sur le détail). » Des reels → apprendre-ma-da (ils priment toujours sur un style de départ). Un
style → `style_depart.py <interview-posee|vlog-dynamique|podcast-extrait|pub-produit|tuto-face-cam>` (copie dans
`ma-da/`, n'écrase jamais). Sa phrase dit déjà le genre (« ma pub », « mon tuto ») ou « vas-y direct » → choisis
le style le plus proche sans demander et dis-le en une ligne. Rien ne colle → `analyze_style.py --neutre` (reel
sobre). Puis ajuste `ma-da/profile.json` à son message (« pas de sous-titres », « musique douce »…).

**Fichiers en vrac** (tout dans un même dossier, pas dans `rushs/`/`voix/`/`broll/`/`musique/`) : ne trie pas à la
main, ne déplace rien. `transcribe.py <dossier>` puis `trier.py <dossier>` → `.monteur/tri.md` : chaque fichier
(`parle` = quelqu'un parle à l'image, `plan` = image sans parole, `voix off`, `musique`, `trame`) et le mode. Un
`?` → un `timeline_view.py` de ce fichier tranche. Ensuite, partout plus bas : rushs = fichiers `parle`, broll =
`plan`, voix = `voix off`, musique = `musique` (chemins `<dossier>/<fichier>`, pas de transcription en double).
Mode « aucune voix » : pas géré, dis-le et demande une voix off. Rien du tout à monter : dis-lui où déposer, stop.

**Commandes** : une commande par appel, chemin complet du script, sans `cd`, sans variable, sans `&&` ni script
temporaire (sinon des demandes d'autorisation bloquent le montage). **Économe** (beaucoup ont un abonnement à 20 $) : ne lis jamais un transcript `.json` en entier (Grep le
mot voulu), lis `takes.md` / `cadence.md` / `plans.md`, une planche d'images une seule fois, pas de vue en double.

**Quel montage ?** Fichiers en vrac : le mode de `tri.md`. Sinon, un fichier son dans `voix/` (ou une voix off citée par lui) **et** des rushs sans personne qui
parle face caméra = **voix off** → section 3 bis. Sinon (il parle dans ses rushs) = face caméra → sections 2-3.
Doute : un coup d'œil (`timeline_view.py` sur un rush) tranche.

## 2. Transcrire et lire
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/transcribe.py" rushs
```
~1 min par minute de rush (délai long, 10 min) ; `--rapide` s'il est pressé ou > 10 min de rushs (dis-le).
Lis `.monteur/takes.md`, `ma-da/STYLE.md` (**« Règles apprises » priment sur tout**, puis Règles et Coups de
signature) et `ma-da/profile.json`. Glob `broll/`, `musique/` (les noms de fichiers sont tes indices) et
`${CLAUDE_PLUGIN_ROOT}/assets/emoji/` (emojis disponibles, nommés par code Unicode).

## 3. Décider (le cœur)
**Prises.** Regroupe les reformulations / reprises (`(reprise?)`). Garde la **dernière prise complète** ; une
autre seulement si la dernière bégaie, a un `mot?`, est coupée ou plus molle. Élimine faux départs, « euh/bon/
alors » en tête de phrase, « attends je recommence », hors-caméra, hors-sujet.

**Hook (0-3 s) — note chaque candidat comme Opus.** Prends 3-5 phrases candidates (la 1re, les chiffres, les
résultats, les contradictions, la conclusion) et note-les /10 sur : **Hook** (accroche et colle au sujet),
**Flow** (s'enchaîne avec la suite sans contexte manquant), **Value** (promesse, émotion, utilité), **Trend**
(dans l'air du temps pour son public). Règles dures : la 1re phrase est une affirmation **autonome** — jamais
« donc / et / alors / mais / du coup » en tête, jamais un pronom sans référent (« il », « ça », « ce truc »),
jamais « Salut, aujourd'hui on va parler de ». Si la DA ouvre à froid (`hook.type` choc/chiffre/résultat), mets
la meilleure phrase **en premier même si elle a été dite à la fin** (cold open), puis l'ordre logique. Calque
la `formule` de ses hooks. `hook.texte_a_l_ecran` → un `titles` de 2-3 s à 0 (≤ 6 mots). DA à carton (`hook.fond`, ou un `graphismes` de type `question`) : carton **animé**, jamais un aplat fixe ; son
texte = la question **telle qu'on l'entend**, raccourcie à ≤ 10 mots si besoin (pas celle de la référence), durée
≈ celle du carton de la référence (garde de la question entendue juste ce qu'il faut) —
`anim.py question "<la question, mot fort entre *étoiles*>" --duree <durée du segment d'accroche> --profil
ma-da/profile.json --sortie broll/anim_question.mp4`, posé en b-roll à 0 avec `"carton": true, "texte": true,
"tenir": true` ; interview → la question posée en 1er segment (`note: "hook garde"`) sous le carton, puis la réponse.
Sous le carton : la voix de l'intervieweur seulement si `hook.voix_sous_carton` ; sinon le segment de la question
passe `"muet": true` (rendu sans son, silence à la place) et la réponse démarre à la fin du carton (≤ 0,05 s). La
1re image doit montrer le mot fort (le gabarit `question` le pose dès l'image 0).
**La première réponse commence au début de sa phrase** (« dans quinze ans, j'ai… », pas « quinze ans, j'ai… ») :
borne le segment sur le 1er mot de la phrase, et écoute/regarde le 1er mot gardé au rendu.
Pas de `titles` avec `fond` (repli seulement si anim.py échoue). Un `titles` sans fond par-dessus l'orateur reste
possible (≤ 6 mots). Note `"note": "hook"`
sur le(s) segment(s) du hook.

**Structure et durée.** Suis `style_verbal.structure` (ou la trame). Somme des segments ≈ cible (±10 %). Trop
long : d'abord le rythme (le rendu resserre ; compare `debit_mots_s` du rapport de check.py à `rythme.debit_mots_s`),
puis les hésitations, et seulement ensuite le contenu : les répétitions d'idée, puis les exemples secondaires, jamais
hook ni fin. Ne coupe jamais une phrase dont la suivante commence par un connecteur qui s'y rapporte (parce que,
donc, du coup, c'est pour ça) sans couper ou réécrire aussi la suivante. **Trop long : une idée dite
deux fois ne passe qu'une fois** (garde la plus forte, souvent celle de la fin) ; à la bonne durée, une
reformulation qui prépare la chute peut rester (regarde si la référence le fait). Trop court : ne remplis
pas, préviens. **Fin** : une phrase qui ferme la boucle ou le CTA de sa DA (`style_verbal.cta`), `"note": "cta"`,
coupe sèche après. Reproduis le geste de la référence sur la dernière phrase (elle finit large → dernier segment
plus large que l'avant-dernier, pas de crash-in).

**Coupes.** `start` = début du 1er mot − 0,05 s, `end` = fin du dernier mot + 0,10 s : prends les bornes des
phrases dans `takes.md` ; pour couper au milieu d'une phrase, **Grep** le mot dans `.monteur/transcripts/<nom>.json`
(jamais Read du fichier entier : des dizaines de milliers de tokens). Le rendu recale ensuite tout sur sa respiration (`rythme.respiration_*`) :
ne compresse pas les pauses à la main, coupe juste ce qui doit partir.

**Cadrage.** Le rendu cadre seul le visage comme dans ses vidéos (`cadrage` du profil : taille du visage, hauteur
des yeux) : n'ajoute pas de zoom pour « serrer » le plan, garde les zooms pour le rythme.

**Rythme = coupes sèches, pas zooms.** Le rendu découpe seul les phrases trop longues en jump cuts (même cadre,
silence retiré) au rythme de `rythme.duree_moyenne_plan_s` (au-delà de `rythme.plan_max_s`, défaut 1,6×). Ne
découpe jamais un segment en deux segments contigus pour créer un plan. Si check.py dit « plans trop longs », ne
rajoute pas de zoom : resserre (respiration, hésitations) et relance.
**Zooms (identiques à sa DA).** Budget = `zooms.par_minute` × durée/60 (arrondi), pas un de plus, chacun sur un
début de phrase ou le mot fort, intensité `zooms.intensite` ±0,05. Mélange selon `zooms.types` : `punch` (rampe `rampe_s`, ×`intensite`, phrases
qui comptent), `doux` (poussée lente sur toute une phrase calme), `crash` (×1,25-1,35, 1 à 3 par vidéo, sur LE
chiffre / la chute), `cut` = zoom sec à la coupe (mets `zoom` sans `zoom_type`). Toujours sur un **début de phrase**
ou une **phrase d'emphase**, **jamais au milieu d'une proposition** ; alterne serré/large, jamais deux identiques
d'affilée. `zoom_center: "visage"` (défaut) sauf cadrage particulier vu sur une image.

**Mots-clés (sous-titres).** Par bloc de `mots_par_bloc` mots : **au plus 1** mot-clé (`mots_cles_par_bloc`),
sur **exactement** `mots_cles_part_blocs` des blocs (0,33 = 1 bloc sur 3 : compte les blocs, n'en colore pas
plus ; check.py le mesure à l'image, ±40 %) : chiffres, argent, %, négations (jamais, rien, pas), superlatifs (pire,
meilleur, seul), sinon le nom ou verbe porteur. Liste-les dans `mots_cles` du segment (mots tels que dits).

**Emojis.** Seulement si `emojis_par_minute` > 0 : ~cette fréquence, sur une émotion ou un objet concret, un par
segment au plus, uniquement parmi ceux d'`assets/emoji/` (`"emoji": "☕"`). Jamais sur le hook si la DA n'en met pas.

**B-roll.** Un fichier de `broll/` dont le nom correspond à un mot concret dit → pose-le sur ce mot avec
`"mot": "<le mot>"` (le rendu l'aligne au mot près et le borne à sa phrase) ; `duration` ≈ `broll.duree_moy_s` ;
total ≈ `broll.part_pct`. Jamais sur le hook ni le CTA, jamais sur une phrase qui a un punch-in. DA sans
b-roll (`part_pct` < 0,03) : **aucun**, même si `broll/` contient des fichiers (ils changeraient le rythme et le
style) — sauf s'il le demande ; dis-le en une ligne à la livraison. N'invente jamais un fichier.
Noms sans indice (`IMG_1234.mov`, fichiers `plan` du vrac) : `plans.py <ces fichiers>` puis décris les planches
comme en 3 bis (étapes 1-2) ; un b-roll = `source` et `source_start` du plan choisi dans `plans.md`.
**Bilan de matière** (avant le plan) : besoin = `broll.part_pct` × durée cible ; disponible = somme des plans
**uniques** de `plans.md` qui montrent une phrase gardée, plus chaque insert animé de la DA prévu (carton, citation,
mosaïque, logo… : un insert = un plan couvert). Manque > 30 % du besoin, ou une phrase concrète (lieu,
objet, action) sans aucun plan → **illustrations libres** (section plus bas), sauf si `broll.banques` vaut `false`
ou s'il a dit « que mes images ». Ses vidéos passent toujours d'abord ; une illustration libre ne remplace jamais un
de ses plans qui montre la même chose ; jamais sur le hook ni le CTA, ni sur une marque qu'il cite ; au plus la
moitié des b-roll en illustrations libres si sa DA utilise ses propres vidéos (`broll.source`). Annonce-le dans le
plan (« 3 illustrations libres : Dubaï, foule, contrat ») ; « vas-y direct » → fais-le et redis-le à la livraison.
**Ne compense jamais un manque de plans par des zooms** ni par des plans étirés.
**Habillage : comme le monteur de la référence, pas comme un diaporama.** Lis `graphismes` et `broll.source` du
profil et les « Coups de signature » de STYLE.md : chaque recette notée là doit apparaître dans le montage quand
le contenu s'y prête (une citation → la recette citation, un lieu → lieu, une marque signée → logo…). Ordre :
1. **Ses vidéos à lui** (`broll/` : soirées, lieux, coulisses, produits) : c'est ce qui fait « vrai ». D'abord
   `plans.py broll` (ses clips découpés en plans) puis décris les planches **une fois** dans
   `.monteur/descriptions.txt` (comme en 3 bis, étapes 1-2 : « P4: foule vue du ciel, festival de nuit »). Choisis
   le plan qui montre **ce qui est dit** (« très établis à Maurice » → le festival vu du ciel ; « marché très
   concurrentiel » → la boîte de nuit bondée), pas un clip au hasard d'après son nom. Entrée :
   `{"plan": "P4", "at": .., "duration": .., "mot": "Maurice"}` (source et départ viennent du plan, jamais au-delà
   de sa fin). **Plans courts** : la durée d'un plan = celle des plans de la référence (médiane de
   `rythme.plans_quantiles`) ; `broll.couvre: "phrase"` → la phrase est couverte par une **suite de plans
   enchaînés** (chacun au plus sa durée dans plans.md, `at` = fin du précédent, **`mot` sur chaque plan** = le mot
   dit quand il entre ; un plan sans `mot` collé au précédent le suit tel quel). Pas assez de plans → la fin de phrase seulement, ou illustrations libres ; jamais un
   plan étiré au-delà de sa fin, un même plan ne revient pas, jamais deux plans de la même scène de suite.
   **Jamais deux fois le même plan dans un enchaînement ; varie les plans d'une même source** : regarde la
   planche du clip (`planche.py broll/<clip> --ips 10`) — un clip monté qui repasse les mêmes images (boucle) n'en
   donne qu'un passage (`"source"`, `"source_start"`, `"duration"` à la main, sans `plan`). **Aucun logo ni
   filigrane visible** dans un plan d'illustration (marque d'une banque d'images, sigle d'un vidéaste) : petit et
   sur fond uni/texturé → `"masque": [x, y, w, h]` (fractions de l'image source, repérées sur une image extraite du
   clip ; effacé par delogo avant recadrage, le plan reste large) ; grand, sur des détails ou contre un bord →
   recadre (`"zoom": 1.35, "zoom_center": [x, y]` sur le b-roll, part de l'image) ou change de plan.
   **Netteté** : un plan filmé n'est jamais agrandi au-delà de 2× sa définition (recadrage 9:16 × zoom ; le
   rendu accentue seul la netteté dès 1,4×, retire le zoom ajouté au-delà de 2×, sauf `zoom_center` explicite, et
   check.py signale « plan agrandi ×N (flou) »). Un paysage 1080p en plein cadre (×1,8) reste net : plein cadre comme
   la référence. `"fond_flou": true` (plan entier sur le même plan flouté) seulement si la référence le fait, ou pour
   un plan trop peu défini qu'on ne peut pas remplacer. **Un plan ne déborde pas sur la phrase suivante** (plans enchaînés compris : le
   rendu coupe à la fin de la phrase du `mot`, et retire ce qui n'a plus 0,4 s) ; seulement si la phrase suivante
   parle de la même chose et qu'il l'a voulu plus long : `"deborde": true`.
2. **Motion design animé** — `anim.py` (`--help`), toujours `--profil ma-da/profile.json` (couleurs, police) :
   `question` (carton), `citation` (dicton/phrase forte : `--fond <le rush> --fond-debut <seconde de la phrase dans
   le rush>` = l'orateur assombri derrière les guillemets), `lieu` (`--fond` = un de ses clips du lieu, sinon une
   photo ; `--tampon SIGNÉ` pour un contrat), `logo` (`--image broll/<logo>.png` s'il y en a un), `chiffre` (le
   nombre compte), `liste` (« zéro réseau, zéro contact, petit budget »), `mosaique --clips …` (best-of de ses
   images dans une carte, après un carton). Sortie `broll/anim_<nom>.mp4`, entrée b-roll = celle qu'anim.py
   affiche (`"texte": true, "tenir": true, "sous_titres": …` d'après `graphismes[].sous_titres`), durée 1,8-3 s. ~1 insert toutes les 6-8 s, jamais deux d'affilée.
   La référence a un graphisme qu'aucun gabarit ne fait (bandeau, compteur stylé, carte, écran de fin…) :
   écris-le en HTML/CSS animé dans `broll/anim_<nom>.html` (lis `anim.py --help` : police `F`, couleurs
   `var(--a)`, classes prêtes, toile 1080×1920) puis `anim.py html broll/anim_<nom>.html --duree .. --sortie
   broll/anim_<nom>.mp4`. Regarde-le (`planche.py broll/anim_<nom>.mp4 --ips 6`) avant de le poser.
   **La recette anim.py ne passe aucune option que le profil fournit déjà** (police, graisse, taille, entrée,
   anneaux, sortie, guillemets, teinte… viennent de `graphismes[]`) : seulement le texte, `--fond`, `--clips`,
   `--image`, `--duree`. Vérifie **chaque** insert face au même passage de la référence avant de le poser :
   `planche.py broll/anim_<nom>.mp4 --ips 6 --ref <référence> --ref-debut <seconde de l'élément dans la référence>`
   (seconde = `graphismes[].quand`) ; visiblement loin (taille, anneaux, entrée, sortie) → corrige le profil
   (c'est lui qui est faux), pas la recette.
3. `carte.py` (images fixes) seulement si anim.py est impossible (doctor `[!!] animations`).
**Tout ce qu'il a déposé ou cité sert** : chaque fichier de `broll/` qu'il mentionne (« le logo du Nikki Beach »)
apparaît dans le montage (le logo → `anim.py logo --image` sur la phrase qui le nomme). Avant le rendu, vérifie
dans ton edl qu'aucun n'a été oublié.
**Un seul style graphique** : mêmes couleurs, même police, même famille d'entrées ; pas de vignettes flottantes
posées sur l'orateur si la référence n'en a pas. Si la DA montre ses propres images (`broll.source:
"videos_du_createur"`) et que `broll/` n'en a pas, dis-le en une ligne au brief (« dépose 5-10 clips de tes
événements dans broll/ ») ; le bilan de matière décide des illustrations libres.
**Illustrations libres** (vidéos gratuites, licences et crédits gérés par `illustrer.py`) :
1. Une idée par phrase sans plan, en **un seul Write** de `.monteur/idees.json` :
   `[{"id": "foule", "mot": "<1er mot de la phrase>", "phrase": "<la phrase>", "duree_min": <s du créneau>,
   "type": "video", "requetes": ["crowded nightclub dance floor", "…", "…"]}]` — 3 requêtes anglaises
   **filmables** (sujet + action + lieu/lumière), jamais le mot abstrait (« marché très concurrentiel » → « crowded
   nightclub dance floor », pas « competitive market »).
2. `illustrer.py chercher .monteur/idees.json` → lis la planche `.monteur/candidats_N.jpg` **une fois**
   (`dubai-2 ▶ 8,4 s ▯ pexels · CC BY` : ▯ vertical, ▭ paysage).
3. `illustrer.py choisir <repères>` (`--broll <son dossier>` si besoin) → `<b-roll>/libres/`, crédits, et il
   affiche l'entrée b-roll de chaque fichier et la commande `plans.py …` à lancer ; lance-la (les descriptions
   sont déjà écrites), puis une entrée `broll` par plan (`"plan": "P..", "libre": true`). Paysage :
   `"zoom_center": [0.25|0.5|0.75, 0.5]` selon où est le sujet.
Aucun candidat correct → pas d'illustration (motion design de la DA à la place), **jamais hors sujet**. Pexels,
Pixabay : jamais de marque ou de logo reconnaissable, jamais une personne identifiable sur une phrase négative.
Jamais de BY-SA ni de NC (le script les écarte). Téléchargement refusé (erreur 429 de Wikimedia) → un autre candidat.
Sans clé Pexels/Pixabay : propose-le **une seule fois**, sans bloquer : « Pour plus de vidéos verticales, crée une
clé gratuite sur pexels.com/api (1 min) et colle-la ici » → `illustrer.py cle pexels <clé>`.
Aucun sous-titre ne passe sur le texte d'un insert (`"texte": true` le garantit) ; un whoosh à l'entrée d'un
insert seulement si la DA a des bruitages.
**Transitions** (seulement celles de la DA : `transitions` du profil, « Coups de signature ») : place le flash là
où la référence le met (« à la sortie » → `"sortie": "flash"` sur l'insert, couleur `"sortie_couleur"`, sinon
`"entree": "flash"` (blanc) ou `"noir"`, couleur de la référence avec `"entree_couleur": "#F4A7B9"`) ; ailleurs
`"transitions": [{"at": <t final>, "type": "flash", "couleur": ".."}]` dans l'edl. 1 à 3 par vidéo, sur un
changement d'idée ou de lieu, jamais sur le hook. DA en coupes sèches : aucune.
**Musique** : `"music": {"source": .., "volume_db": .., "debut_s": <s>}` — démarre là où le morceau a de
l'énergie (pas une intro molle : `timeline_view.py musique/<fichier> --de 0 --a 60` montre la forme d'onde).
Le rendu baisse la musique sous la voix, la remonte dans les pauses et cale seul l'entrée des b-roll sur les
temps forts du morceau (à 0,15 s près, jamais après le mot).
**Faux raccords** (pauses retirées, jump cuts, segments qui se suivent dans la même prise) : le profil tranche
(`zooms.coupes_echelle`, appris par apprendre-ma-da) ; n'écris `"masquer_raccords"` dans l'edl que pour le
contredire. `true` : à chaque raccord de la même prise, coupe sèche qui alterne 1 ↔ `zooms.intensite` (1,12 sans
profil), et une phrase trop longue sans pause est coupée à une frontière de mot pour changer d'échelle ; ces
changements ne sont pas des zooms pour check.py (ne les compense pas en retirant tes zooms). `false` : coupes
sèches même échelle (la tête saute un peu). `zooms.fin` : le dernier morceau repasse au plan large, seul.
**Bruitages.** Les bruitages repérés hors parole (carton, trous) sont fiables : garde-les sans demander et
annonce-le (« j'entends des whooshes sur ton carton, je les garde »). Seule l'estimation sous la voix demande
confirmation. Carton d'ouverture (`"carton": true`) et `audio.sfx_carton` : le rendu le sonorise seul comme la
référence (impact à l'entrée, un tic par temps, riser qui finit sur la coupe) — ne pose rien dessus, sauf pour
le remplacer. Ailleurs, si `audio.sfx_par_minute` > 0 : dans les trous de parole, sur les entrées de b-roll et
les crash zooms, types de `sfx_types` (fichiers d'`assets/sfx/` : whoosh, swoosh, pop, click, ding, impact,
riser), `volume_db` −14 à −18, `at` = temps de l'image visée (le rendu cale le point fort du son 1 image avant :
crête d'un whoosh, fin d'un riser, attaque d'un impact). ~`sfx_par_minute`/min.

**Musique.** Il a déposé un fichier dans `musique/` → utilise-le (même si sa DA n'en a pas : il l'a voulu),
`volume_db` : DA avec musique → `audio.musique_db_sous_voix` (valeur mesurée) ; DA sans musique mais fichier
déposé → −24 (nappe discrète). Pleine jusqu'à la fin du dernier mot, puis fondu de 0,4 s (le rendu le fait).
`debut_s` comme expliqué plus haut. Plusieurs fichiers : celui
qui colle au ton. Aucun fichier : la DA a de la musique → dis-lui d'en déposer une ; sinon `null`.
**Son et image** : `"audio": {"nettoyage_voix": "auto"}` et `"couleur": "auto"` toujours (sauf demande contraire).

**Doute** (deux prises proches, rire, geste, mot coupé) : `timeline_view.py rushs/a.mp4 --de 12 --a 16` + Read.

## 3 bis. Voix off sur ses rushs (si c'est ce mode)
1. `transcribe.py voix` (la voix seule) puis `plans.py rushs broll` (les dossiers qui existent ; en vrac : les
   fichiers `plan` de tri.md, un par argument, la voix déjà transcrite) : découpe les rushs en
   plans numérotés → `.monteur/plans.md` + planches `.monteur/plans_1.jpg`, `_2.jpg`…
2. Lis chaque planche **une fois**, puis écris en **un seul Write** `.monteur/descriptions.txt`, une ligne par plan :
   `P12: mains versant un café, gros plan` (3-8 mots concrets : ce qu'on voit, cadrage). Pas de script. Déjà décrits
   (relance) : ne relis pas les planches.
3. Garde les bonnes phrases de la voix (prises ratées, hésitations : mêmes règles qu'en 3) → `segments` sur le
   fichier de la voix (souvent un seul segment du premier au dernier mot gardé). Puis
   `plans.py --voix voix/<fichier>` → `.monteur/cadence.md` : les créneaux de plans **au rythme de sa DA** (accroche
   plus rapide, alternance court/long), chaque coupe sur un début de mot. Ne change pas ce découpage : c'est son rythme.
4. Pour chaque créneau, choisis le plan qui **illustre ce qui est dit** à ce moment : l'objet ou l'action nommés
   d'abord (« café » → le plan du café) ; une idée abstraite → l'émotion ou le geste qui va avec (« calme » → mer,
   forêt) ; l'accroche → le plan le plus fort visuellement. Jamais deux fois le même plan (s'il en manque, un autre
   moment du même plan avec `"decalage"` en s), **jamais deux plans de la même scène l'un après l'autre** (même
   `scène` dans plans.md : la coupe ne se verrait pas — alterne A, B, A, C…), jamais un plan `flou` s'il y a mieux,
   varie les cadrages (large / serré). Si la DA zoome, une poussée `"zoom_type": "doux", "zoom": <intensite>` sur
   ~1 plan sur `zooms.une_coupe_sur`. Un créneau = une entrée de `broll` : `{"plan": "P12", "voix_t": <début du créneau>, "mot": "<son mot>"}`.
5. edl : `"mode": "voix_off"`, `segments` = la voix, `broll` = tous les créneaux dans l'ordre, sous-titres / mots-clés
   / musique / couleur comme sa DA (pas de zooms de visage). Le rendu met les plans bout à bout sur toute la voix,
   chacun sur son mot, sans jamais d'image noire, et le son des rushs est coupé (seule la voix compte).

Écris `.monteur/edl.json` (Write) **exactement** dans ce format :
```json
{"version": 2, "output": "sorties/<sujet-court>.mp4",
 "format": {"width": 1080, "height": 1920, "fps": 30},
 "segments": [{"source": "rushs/a.mp4", "start": 12.34, "end": 15.80, "zoom": 1.12, "zoom_type": "punch",
               "zoom_center": "visage", "mots_cles": ["2 heures"], "emoji": "⏰", "note": "hook"}],
 "captions": true,
 "broll": [{"source": "broll/cafe.mp4", "at": 3.2, "duration": 1.8, "source_start": 0.0, "mot": "café"}],
 "sfx": [{"type": "whoosh", "at": 3.2, "volume_db": -16}],
 "music": {"source": "musique/lofi.mp3", "volume_db": -18},
 "titles": [{"text": "3 erreurs à éviter", "at": 0.0, "duration": 2.0}],
 "audio": {"nettoyage_voix": "auto"},
 "couleur": "auto"}
```
Voix off : `{"version": 3, "mode": "voix_off", ..., "segments": [{"source": "voix/vo.wav", "start": 0.4, "end": 38.2,
"mots_cles": [..]}], "broll": [{"plan": "P3", "voix_t": 0.4, "mot": "Tu"}, {"plan": "P7", "voix_t": 1.9, "mot": "café"}, ..]}`.
`segments` dans l'ordre de sortie, temps **dans le rush** ; `zoom_type` ∈ aucun|doux|punch|crash (absent = zoom
sec) ; `broll.at`, `sfx.at`, `titles.at` = temps **dans la timeline de l'edl** (somme des durées `end − start` des
segments qui précèdent, avant tout resserrage : le rendu les recale sur les mêmes mots — d'où `mot` sur chaque
b-roll, plus sûr qu'un temps) ; `format` = `profile.format` ;
`captions` = `sous_titres.actif` ; `music` peut être `null`, les listes `[]`. `note` : `hook`, `cta`, `garde`
(garde les hésitations de ce segment). `output` : ajoute `-v2`, `-v3`… si le fichier existe (jamais écraser).

## 4. Montrer le plan (sauf « vas-y direct »)
```
1. [0:00–0:03] « Tu perds 2 h par jour sans le savoir. »  (hook, crash zoom, mot-clé « 2 h »)
2. [0:03–0:07] « Le problème, c'est le café. »            (b-roll cafe.mp4 + whoosh, emoji ☕)
...
Durée estimée : 0:42 (cible 0:45) · coupé : 3 prises ratées, 11 s de blancs · zooms 7 · musique : lofi.mp3
Hook choisi : « Tu perds 2 h… » (Hook 9, Flow 8, Value 8, Trend 7) plutôt que « Alors aujourd'hui… » (4).
```
« Je lance le montage ? (tu peux me dire de garder/enlever une phrase) ». Ajuste seulement si on te le demande.

## 5. Rendre, contrôler, regarder
Vidéo > 60 s ou plan retouché plusieurs fois : d'abord `render.py .monteur/edl.json --brouillon`. Sinon :
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/render.py" .monteur/edl.json
python "${CLAUDE_PLUGIN_ROOT}/scripts/check.py" sorties/<nom>.mp4 --duree-cible <s> --edl .monteur/edl.json
```
`check.py` lit aussi `.monteur/edl.rendu.json` (ce qui a vraiment été rendu). `conforme: false` → applique le
`conseil` de chaque écart dans edl.json (plans trop longs → resserrer, jamais de zoom en plus ; parole trop
lente → hésitations ; respiration → bornes ; zooms/min →
ajoute/retire en début de phrase ; mot coupé → bornes sur les mots ; b-roll sur hook → décale ; couleur →
`"couleur": "auto"` ; loudness → c'est le rendu, relance), re-rends, re-contrôle. **2 boucles maximum**, puis passe
à l'œil du monteur en notant ce qui reste hors tolérance et pourquoi (à dire à la livraison).
Écarts du rythme (`plans_courts_s`, `plans_longs_s`, `hook_plan_s`) en voix off : relance `plans.py --voix` puis
réattribue ; en face caméra : resserre ou fusionne des segments (le rendu fait les jump cuts). `images_noires` = un trou : un plan manque.
Puis **l'œil du monteur** (obligatoire, même si check.py est conforme) :
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/planche.py" sorties/<nom>.mp4 --ref <une vidéo de référence de la DA>
```
(référence = celle de `profile.references` ; aucune → sans `--ref`). Read la planche (haut = ton montage à 2 i/s,
bas = la référence). Vérifie d'abord toi-même chaque point (✔/✘ avec la seconde fautive) et corrige les ✘ évidents :
1. on voit l'orateur ou un graphisme **animé** dès la 1re seconde (pas d'aplat fixe > 1 s) ;
2. aucun texte n'en chevauche un autre, aucun titre/sous-titre coupé par le bord ;
3. rythme : pas de plan figé > 2× `rythme.duree_moyenne_plan_s`, alternance large/serré visible ;
4. chaque coup de signature de STYLE.md présent quand le contenu s'y prête ;
5. un seul style graphique, inserts bien lisibles (texte entier à l'écran) ;
6. b-roll au bon moment et pertinents (ses vidéos, pas des images génériques) ;
7. image : visage lisible, pas de fond brûlé ni de couleurs acides ;
8. **niveau de finition comparable à la référence** (ta planche passerait-elle pour la sienne ?).
**Critique indépendante** (obligatoire) : tu as fait le montage, tu le juges mal. Lance un sous-agent (outil
Agent, sans contexte : il n'a rien vu de ton travail) avec ce message, chemins absolus remplis :
> Tu es un monteur senior de reels, exigeant, qui n'a pas fait ce montage. Compare-le à la référence d'un pro.
> Lis : la planche `<…_vs_ref.jpg>` (haut = montage à 2 i/s, bas = référence au même temps), la partition
> (`python "<plugin>/scripts/review.py" <vidéo> --root <projet> --partition` : qui est à l'écran à chaque instant,
> blancs entre sous-titres), `ma-da/STYLE.md` et le rapport `.monteur/rapports/<nom>.json`. Pour un doute, regarde
> de près : `python "<plugin>/scripts/timeline_view.py" <vidéo> --de <t-1> --a <t+1>` puis Read (n'importe quelle
> seconde). Juge ce qu'un spectateur sentirait : accroche des 2 premières secondes, rythme (blancs > 0,3 s, plan
> figé, coupe qui saute), lisibilité (texte coupé, chevauché, trop court pour être lu), pertinence des plans,
> finition graphique vs la référence, image (visage, couleurs). Réponds : une note /10 « finition vs référence »,
> puis **au plus 5 problèmes classés du plus visible au moins visible**, chacun : seconde, ce qu'on voit, correction
> concrète dans l'edl (champ et valeur). Pas de compliments, pas de problème inventé : rien de grave → dis-le.

Corrige ses problèmes dans l'ordre (edl, insert, profil vidéo), re-rends, et relance **un nouveau** critique
(jamais le même : il défendrait son avis). **3 tours maximum** (check.py compris) ; arrête plus tôt si la note
≥ 8/10 et plus aucun problème visible. Livre en disant la dernière note et ce qui reste.
**Jamais « c'est réussi » sans avoir lu le rapport de check.py.**

## 6. Exporter et livrer
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/export_xml.py" .monteur/edl.json
```
Réponse courte : chemin de la vidéo et des .fcpxml (Final Cut, DaVinci) / .xml (Premiere) ; 3 lignes de récap
(durée, ce que tu as coupé/réordonné et le hook choisi, résultat du contrôle) ; illustrations libres utilisées →
donne le chemin de `.monteur/credits.txt` à coller sous le post ; « Dis-moi ce que tu changerais
(ex. « trop de zooms », « garde la blague ») ». Si tu retouches dans CapCut/Premiere, redonne-moi ta version
finale (vidéo ou export XML) : j'apprends de tes retouches. » (→ skill corriger, `retouches.py`).
**Il apprend de chaque montage** : ses retours passent par la skill corriger (journal `ma-da/corrections.jsonl`,
`corrections.py bilan` : une demande faite sur 2 vidéos se propose comme règle). Quand il valide (« parfait »,
« je publie », « c'est bon »), une phrase : « Je l'ajoute à tes références pour affiner ton style ? » ; oui →
`analyze_style.py --ajoute <sa version finale, sinon sorties/<nom>.mp4>` (skill apprendre-ma-da, 8).
**Page de review** : lance-la en tâche de fond (`run_in_background`) à chaque livraison, sauf s'il dit non :
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/review.py" sorties/<nom>.mp4
```
Elle s'ouvre dans son navigateur (http://127.0.0.1:8765) : la vidéo image par image, la frise (rush, plans, inserts,
sous-titres) et des notes posées à l'instant précis. Dis-lui : « Note ce que tu veux changer sur la page, puis dis-moi
« corrige avec mes notes » ».
**Ce que je sais de ton style** : `mon_style.py` (page locale : son style en phrases simples, règles apprises,
progression des montages) — propose-la en une ligne.
