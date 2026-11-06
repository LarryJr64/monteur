# Licences — bruitages et modèle de débruitage

## Bruitages (assets/sfx/*.wav)

Tous **synthétisés par le code** du plugin (`python scripts/audio.py generer-sfx`, fonction `synth_sfx`) :
bruit filtré, sinus, enveloppes. Aucun enregistrement tiers, aucune licence à respecter, aucune attribution due.
Le plugin les place dans le domaine public (CC0 1.0, https://creativecommons.org/publicdomain/zero/1.0/).
48 kHz, 16 bits, crête -3 dBFS.

| Fichier | Son | Source |
|---|---|---|
| whoosh.wav | souffle 0,7 s, bande 250 → 2200 → 500 Hz, panoramique G→D | synthèse (audio.py) |
| whoosh_2.wav | souffle 0,5 s plus brillant, D→G | synthèse (audio.py) |
| whoosh_3.wav | souffle 0,9 s grave et lent | synthèse (audio.py) |
| swoosh.wav | souffle court 0,25 s, aigu | synthèse (audio.py) |
| pop.wav | « pop » 0,12 s (sinus 900 → 280 Hz) | synthèse (audio.py) |
| pop_2.wav | « pop » aigu 0,09 s | synthèse (audio.py) |
| click.wav | clic 30 ms | synthèse (audio.py) |
| impact.wav | coup sourd 1 s (sinus 135 → 45 Hz saturé + bruit) | synthèse (audio.py) |
| ding.wav | cloche 1,4 s (partiels inharmoniques à 1320 Hz) | synthèse (audio.py) |
| riser.wav | montée 2 s (bruit 200 → 8000 Hz + sinus 150 → 1200 Hz) | synthèse (audio.py) |

Pourquoi pas de sons téléchargés : Freesound (licences variables par son, API), Pixabay (licence propre, pas CC0)
et Sonniss (redistribution seule interdite) sont exclus. L'utilisateur peut déposer ses propres sons dans
assets/sfx/ (nom de fichier = `type` dans l'edl), sous sa responsabilité.

## Modèle RNNoise (assets/rnnoise/std.rnnn)

Modèle d'origine de RNNoise (Xiph.Org / Jean-Marc Valin), fichier `std.rnnn` de
https://github.com/richardpl/arnndn-models (« std.rnnn is one originally bundled with Xiph RNNoise implementation »).
Licence : BSD-3-Clause, celle de https://github.com/xiph/rnnoise (Copyright Xiph.Org Foundation, Jean-Marc Valin) :
redistribution permise en conservant la mention de copyright. Utilisé par le filtre ffmpeg `arnndn`.
Choisi au banc d'essai face aux modèles de GregorR (sh, cb, bd, mp, lq) : meilleur SI-SDR et moins de bruit résiduel.
Texte complet de la licence : assets/rnnoise/COPYING-rnnoise.txt.
