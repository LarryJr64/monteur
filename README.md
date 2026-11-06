# Monteur — ton monteur vidéo, dans ton style

Monteur est un module pour **Claude Code**. Tu lui montres **5 à 10 vidéos** que tu as déjà montées : il
mesure ta « DA » (ton rythme, ta façon de respirer entre les phrases, tes zooms, tes sous-titres et leurs mots
en couleur, tes emojis, tes plans de coupe, tes bruitages, ta musique, la couleur de ton image, ton accroche).
Ensuite tu poses tes rushs bruts dans un dossier, tu dis en une phrase ce que tu veux, et tu récupères une
vidéo montée **comme tu l'aurais montée** — prête à poster, plus un fichier pour la retoucher dans Premiere,
Final Cut ou DaVinci.

Pensé pour les reels / TikTok / Shorts en vertical (9:16) : **face caméra**, ou **voix off posée sur tes rushs**
(il choisit pour chaque phrase le plan qui l'illustre, au rythme de tes vidéos). Français et autres langues.
Fonctionne avec un abonnement Claude Pro (20 $/mois) : le gros du travail tourne sur ton ordinateur.

## Ce qu'il fait pour toi
- garde ta meilleure prise de chaque phrase, enlève les ratés, les « euh » et les blancs — en gardant **ta**
  respiration entre les mots et les phrases ;
- choisit l'accroche la plus forte (il note les phrases candidates et peut ouvrir sur ta meilleure phrase) ;
- ajoute zooms, sous-titres animés avec mots-clés en couleur, emojis, plans de coupe (b-roll), bruitages,
  musique, nettoyage de la voix et correction de couleur, **à la dose de ta DA** ;
- te montre le découpage **avant** de monter, pour que tu valides ;
- **vérifie** la vidéo finie avec les mêmes mesures que tes vidéos (rythme, respiration, zooms, b-roll,
  bruitages, couleur, volume, mots coupés…) et corrige avant de te la donner ;
- retient tes corrections : dis « trop de zooms » une fois, il s'en souvient pour les prochaines.

## Ce qui est reproduit exactement, ce qui est approché
**Mesuré sur tes vidéos et vérifié sur chaque montage** : rythme des coupes, longueur des plans (leur
répartition courts / longs, et le rythme plus rapide de ton accroche), cadrage de ton visage, plus long
silence, pauses dans et entre les phrases, nombre et type de zooms (poussée lente, punch-in, crash zoom) et leur
vitesse, taille et position des sous-titres, couleur du texte et du mot-clé, emojis par minute, part et durée
des b-roll, bruitages par minute, volume de la voix et de la musique, luminosité / contraste / saturation /
température de l'image, durée de l'accroche.

**Approché** (Claude regarde des images de tes vidéos et choisit au plus proche) : la police (parmi des
polices libres proches : Montserrat, Anton, Bebas Neue, Poppins…), le style d'animation des sous-titres, les
titres à l'écran, le style des b-roll. Les bruitages et emojis viennent d'une bibliothèque libre fournie : le
même *type* de son (whoosh, pop…) et d'emoji, pas forcément le fichier exact que tu utilises.

## Installation (une seule fois)
1. **Claude Code** (abonnement Claude Pro ou Max). Le plus simple : l'application **Claude** pour ordinateur,
   onglet **Code**. Sinon en ligne de commande : https://claude.com/claude-code
2. **Python 3.10 ou plus** : Windows → https://www.python.org/downloads/ (coche « Add python.exe to PATH ») ou
   `winget install Python.Python.3.12` ; Mac → `brew install python`.
3. **Le module**, dans Claude Code :
   ```
   /plugin marketplace add LarryJr64/monteur
   /plugin install monteur@monteur
   ```
   (« user » si on te demande « user » ou « project »).
4. Dis simplement **« Installe le monteur »** (ou commence directement par apprendre ta DA). Claude te demande
   ton accord, puis une seule commande installe tout le reste : ffmpeg (le moteur vidéo), les outils Python, le
   modèle de reconnaissance vocale (~700 Mo) et le modèle de détection de visage. Compte 5 à 15 minutes la
   première fois. Ensuite, plus rien à installer.

## Utilisation en 3 étapes
Crée un dossier pour tes vidéos (ex. `Documents/Montage`) et ouvre-le dans Claude Code (application : choisis
ce dossier ; terminal : `cd` dedans puis `claude`). Les sous-dossiers `mes-videos`, `rushs`, `broll`, `musique`,
`sorties` sont créés tout seuls.

**1. Apprendre ta DA** — mets 5 à 10 de tes vidéos montées dans `mes-videos/` (tes meilleures, toutes dans le
style que tu veux reproduire), puis dis :
> Apprends mon style à partir de mes vidéos.

Il te résume ta DA en quelques lignes, avec tes 2-3 « coups de signature », et te signale une vidéo qui ne
ressemble pas aux autres. Corrige-le si besoin (« non, mes sous-titres sont plutôt jaunes »). À refaire
seulement si ton style change.

**2. Monter** — mets tes rushs dans `rushs/` (et si tu veux des plans de coupe dans `broll/`, une musique dans
`musique/`), puis dis par exemple :
> Monte ma vidéo sur les 3 erreurs en salle de sport, 45 secondes, ton motivant.

**Voix off** : mets ta voix (fichier son) dans `voix/` et tes plans filmés dans `rushs/`, puis dis
> Monte ma voix off sur mes rushs.

Il regarde tous tes plans, découpe ta voix au rythme de ta DA et pose sur chaque passage le plan qui colle à ce
que tu dis (le café quand tu parles du café), sans jamais réutiliser le même plan ni laisser d'écran noir.

Tu peux aussi donner ta trame (« d'abord le problème, puis mes 3 conseils, finis sur abonne-toi »). Il te
montre le plan ; tu valides ou tu dis « enlève la 2e phrase ». Pressé ? Ajoute « vas-y direct ». La vidéo
arrive dans `sorties/`.

**3. Corriger** — dis simplement ce qui ne va pas :
> C'est trop lent. · Trop de zooms. · Le son n'est pas propre. · Moins de bruitages. · Garde ce passage. ·
> À chaque fois, finis par « abonne-toi ».

Avec « à chaque fois » / « toujours », ça devient une règle de ta DA (dans `ma-da/STYLE.md`).

## Pour de bons résultats
- **Filme en vertical (9:16)**, visage vers le haut de l'image, bon son (micro-cravate si possible).
- **Garde tes ratés** dans le même fichier : refais simplement la phrase, il choisit la meilleure prise.
- **Nomme tes b-roll** avec ce qu'on y voit : `cafe.mp4`, `ordinateur-portable.mov`. Il les pose au mot près
  quand tu prononces ces mots.
- Musiques **libres de droits** uniquement, nommées par ambiance (`lofi-calme.mp3`, `energique.mp3`).
- Pour la DA, 5 à 10 vidéos **du même style** : plus il y en a, plus la copie est fidèle.

## Vie privée et coût
Tout se passe **sur ton ordinateur** : transcription, analyse, montage. Ta voix et tes vidéos ne partent
nulle part (seul Claude lit le texte de tes rushs et quelques images, comme dans toute conversation). Aucune
clé, aucun service en plus : rien de plus que ton abonnement Claude. Tous les outils utilisés sont libres et
gratuits.

## Limites
- Fait pour les vidéos courtes (moins de ~3 min), face caméra ou voix off. Pas de vlog multi-caméra, pas d'effets 3D ni
  de motion design (sous-titres : pop, rebond, glissé, fondu, karaoké, mot par mot, surlignage), pas de
  transitions élaborées.
- Les bruitages (whoosh…) ne s'entendent pas de façon fiable sous la voix : il te demande si tu en mets.
- Il ne trouve pas de b-roll ni de musique sur Internet : il utilise ce que tu mets dans `broll/` et `musique/`.
- Sans carte graphique, compte environ 1 minute de calcul par minute de vidéo pour la transcription (au
  premier apprentissage de ta DA aussi). « Fais vite » : modèle plus rapide (~3x), un peu plus de fautes.
- Certaines mesures sont des estimations : b-roll = plans où on ne voit pas ton visage ; un emoji qui
  apparaît pile avec un nouveau sous-titre peut compter comme une coupe ; un bruitage sous la voix n'est pas
  détecté.

## Questions fréquentes
**Ça marche sur Mac ?** Oui (Intel ou Apple Silicon).

**Je peux retoucher le montage dans mon logiciel ?** Oui : à côté de chaque vidéo il y a un `.fcpxml` (Final
Cut, DaVinci) et un `.xml` (Premiere). Garde les rushs au même endroit pour qu'il les retrouve.

**Il a mal compris un nom propre.** Ajoute le mot (une ligne par mot) dans `ma-da/lexique.txt`.

**Il dit que ffmpeg est introuvable.** Dis « installe le monteur » ; si ça persiste, ferme complètement Claude
et rouvre-le.

**Où sont mes réglages ?** Dans `ma-da/` : `STYLE.md` se lit comme un texte normal, tu peux le modifier.
Le dossier `.monteur/` contient les fichiers de travail, tu peux le supprimer sans risque.

**Comment mettre à jour le module ?** Dans Claude Code : `/plugin`, puis mets à jour « monteur ».
