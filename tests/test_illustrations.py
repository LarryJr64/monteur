"""Illustrations générées (carte.py) et raccords internes masqués par un changement d'échelle (pacing)."""
import sys

import numpy as np
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import carte  # noqa: E402
import pacing  # noqa: E402


def test_cartes(tmp_path):
    for kind, textes, vig in (("citation", ["Nul n'est prophète en son pays"], False), ("chiffre", ["15 ans"], True),
                              ("liste", ["Réseau : 0", "Budget : petit"], False), ("lieu", ["Nikki Beach"], True),
                              ("trajet", ["Maurice", "Dubaï"], False)):
        img = carte.carte(kind, textes, sous="x", titre="t", tampon="SIGNÉ", vignette=vig)
        assert img.size == ((1080, 1080) if vig else (1080, 1920))
        assert len(np.unique(np.asarray(img.convert("L")))) > 20  # pas une image vide


def test_raccords_internes_alternent_l_echelle(tmp_path, monkeypatch):
    # trois phrases séparées de longues pauses → deux raccords internes dans un seul segment
    ws = []
    for k, t0 in enumerate((0.0, 3.0, 6.0)):
        ws += [{"w": f"mot{k}{i}" + ("." if i == 3 else ""), "start": t0 + 0.4 * i, "end": t0 + 0.4 * i + 0.3}
               for i in range(4)]
    monkeypatch.setattr(pacing, "find_transcript", lambda root, src: {"words": ws})
    edl = {"masquer_raccords": True, "format": {"fps": 30},
           "segments": [{"source": "a.mp4", "start": 0.0, "end": 7.6, "zoom": 1.0, "zoom_type": "aucun"}]}
    out = pacing.normalize_edl(edl, tmp_path, {})["segments"]
    assert len(out) == 3
    assert [round(s["zoom"], 2) for s in out] == [1.0, pacing.RACCORD_ZOOM, 1.0]


def test_carte_fond_photo(tmp_path):
    from PIL import Image
    Image.new("RGB", (800, 600), (200, 30, 30)).save(tmp_path / "p.jpg")
    img = np.asarray(carte.carte("citation", ["Test"], photo=tmp_path / "p.jpg"))
    assert img[100, 100, 0] > img[100, 100, 2] + 20  # le rouge de la photo transparaît


def test_vignette_photo_arrondie_avec_etiquette(tmp_path):
    """Photo en vignette : coins arrondis (le fond se voit dans le coin), photo au centre, étiquette de lieu."""
    import json
    import subprocess
    from PIL import Image
    from test_render import make_workdir, frame_rgb
    import render
    d = make_workdir(tmp_path)
    Image.new("RGB", (900, 900), (220, 20, 20)).save(d / "broll/photo.jpg")
    edl = json.loads((d / ".monteur/edl.json").read_text(encoding="utf-8"))
    edl.update(captions=False, titles=[], broll=[{"source": "broll/photo.jpg", "at": 2.5, "duration": 1.5,
                                                   "vignette": True, "etiquette": "Dubaï"}])
    (d / ".monteur/edl.json").write_text(json.dumps(edl, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPTS / "render.py"), ".monteur/edl.json"], cwd=d,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    img = frame_rgb(d / "sorties/test.mp4", 3.6).astype(int)
    cote = int(render.VIGNETTE * 1080) // 2 * 2
    x0, y0 = (1080 - cote) // 2, round(render.VIGNETTE_Y * 1920 - cote / 2)
    rouge = lambda p: p[0] > 170 and p[1] < 80 and p[2] < 80  # noqa: E731
    assert rouge(img[y0 + cote // 2, x0 + cote // 2])  # la photo
    assert not rouge(img[y0 + 3, x0 + 3])  # coin arrondi : on voit l'image dessous
    lx, ly = x0 + round(0.04 * cote), y0 + cote - round(0.04 * cote)
    zone = img[ly - 10:ly + 10, lx + 80:lx + 200]
    assert (zone.min(axis=2) > 200).mean() > 0.3  # pilule blanche de l'étiquette
