import datetime
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import export_xml  # noqa: E402
import retouches  # noqa: E402

FPS = Fraction(30)


def _timelines(d):
    """Notre montage et sa retouche, écrits par export_xml (mêmes formats que ceux que l'on exporte)."""
    rush, cafe, plage = d / "IMG.mov", d / "cafe.mp4", d / "plage.mp4"
    infos = {rush: {"width": 1080, "height": 1920, "duration": 60, "has_audio": True, "fps": 30},
             cafe: {"width": 1920, "height": 1080, "duration": 10, "has_audio": False, "fps": 30},
             plage: {"width": 1920, "height": 1080, "duration": 10, "has_audio": False, "fps": 30}}
    nous = [{"src": rush, "track": 1, "at": 0, "in": 30, "len": 60, "zoom": 1.0},
            {"src": rush, "track": 1, "at": 60, "in": 150, "len": 90, "zoom": 1.2},
            {"src": rush, "track": 1, "at": 150, "in": 300, "len": 60, "zoom": 1.15},
            {"src": cafe, "track": 2, "at": 90, "in": 0, "len": 45, "zoom": 1.0}]
    sa = [nous[0],
          {"src": rush, "track": 1, "at": 60, "in": 150, "len": 75, "zoom": 1.0},    # raccourci 0,5 s, zoom retiré
          {"src": rush, "track": 1, "at": 135, "in": 300, "len": 30, "zoom": 1.0},   # coupé en deux, zoom retiré
          {"src": rush, "track": 1, "at": 165, "in": 330, "len": 30, "zoom": 1.0},
          {"src": rush, "track": 1, "at": 195, "in": 600, "len": 30, "zoom": 1.0},   # passage ajouté
          {"src": plage, "track": 2, "at": 30, "in": 0, "len": 30, "zoom": 1.0}]     # b-roll café retiré, plage ajouté
    out = {}
    for nom, items in (("nous", nous), ("sa", sa)):
        for ext, f in (("fcpxml", export_xml.fcpxml), ("xml", export_xml.xmeml)):
            path = d / f"{nom}.{ext}"
            export_xml.write_xml(f(items, infos, 1080, 1920, FPS, nom), path)
            out[nom, ext] = path
    return out


@pytest.mark.parametrize("ext", ["fcpxml", "xml"])
def test_timeline_relue_et_comparee(tmp_path, ext):
    f = _timelines(tmp_path)
    tn, ts = retouches.lire_timeline(f["nous", ext]), retouches.lire_timeline(f["sa", ext])
    assert [c["src"] for c in tn["main"]] == ["img"] * 3 and tn["broll"][0]["src"] == "cafe"
    assert tn["main"][1] == {"src": "img", "at": 2.0, "in": 5.0, "len": 3.0, "zoom": 1.2}
    assert abs(tn["broll"][0]["zoom"] - 1.0) < 0.01 and tn["broll"][0]["at"] == 3.0  # Premiere : recadrage 9:16 retiré
    assert tn["duree"] == 7.0 and ts["duree"] == 7.5
    quoi = [d["quoi"] for d in retouches.comparer_timelines(tn, ts)]
    assert sorted(quoi) == sorted(["raccourci", "zoom retiré", "coupe ajoutée", "zoom retiré", "passage ajouté",
                                   "b-roll retiré", "b-roll ajouté"])
    d = next(d for d in retouches.comparer_timelines(tn, ts) if d["quoi"] == "raccourci")
    assert "raccourci de 0,50 s" in d["texte"] and d["t"] == 2.0


def test_comparaison_inverse_coupe_retiree():
    a = {"src": "img", "at": 0.0, "in": 1.0, "len": 1.0, "zoom": 1.0}
    b = {"src": "img", "at": 1.0, "in": 2.0, "len": 1.0, "zoom": 1.0}
    nous = {"main": [a, b], "broll": [], "duree": 2.0}
    sa = {"main": [{"src": "img", "at": 0.0, "in": 1.0, "len": 2.0, "zoom": 1.0}], "broll": [], "duree": 2.0}
    assert [d["quoi"] for d in retouches.comparer_timelines(nous, sa)] == ["coupe retirée"]


def test_compare_mesures_et_regles():
    nous = {"duree": 40.0, "duree_moyenne_plan_s": 1.0, "zooms_par_minute": 12.0, "sous_titres_presence": 0.9,
            "sous_titres_hauteur_pct": 0.03, "silence_max_s": 0.2, "couleur": {"luma": 0.5}, "emojis_par_minute": 0}
    sa = {"duree": 41.0, "duree_moyenne_plan_s": 1.2, "zooms_par_minute": 0.0, "sous_titres_presence": 0.0,
          "sous_titres_hauteur_pct": 0.05, "silence_max_s": 0.6, "couleur": {"luma": 0.57}, "emojis_par_minute": 5}
    r = {x["chemin"]: x for x in retouches.compare_mesures(nous, sa)}
    assert "style_verbal.duree_typique_s" not in r  # +2,5 % : dans la tolérance
    assert r["zooms.par_minute"]["force"] == "fort" and r["zooms.par_minute"]["sens"] == "-"
    assert r["sous_titres.actif"] == {"force": "fort", "chemin": "sous_titres.actif", "sens": "-", "proposition": False,
                                      "regle": "sous-titres : retirés dans ta version"}
    assert "sous_titres.taille_pct_hauteur" not in r and "sous_titres.emojis_par_minute" not in r  # plus de sous-titres
    assert r["rythme.duree_moyenne_plan_s"]["force"] == "faible" and r["couleur.luma"]["proposition"] == 0.57
    assert r["rythme.silence_max_s"]["force"] == "moyen"
    assert "rythme.silence_max_s" not in {x["chemin"] for x in retouches.compare_mesures(nous, sa, avec_mots=False)}

    details = [{"quoi": "zoom retiré", "t": 1, "texte": "z1"}, {"quoi": "zoom retiré", "t": 2, "texte": "z2"},
               {"quoi": "b-roll retiré", "t": 3, "texte": "b1"}, {"quoi": "passage retiré", "t": 4, "texte": "p"}]
    regles = retouches.regles(retouches.compare_mesures(nous, sa), details)
    assert [x["force"] for x in regles] == sorted((x["force"] for x in regles), key=retouches.FORCES.index)
    z = next(x for x in regles if x["chemin"] == "zooms.par_minute")
    assert z["exemples"] == ["z1", "z2"] and z["force"] == "fort"  # la mesure et ses exemples, une seule règle
    b = next(x for x in regles if x["chemin"] == "broll.part_pct")
    assert b["force"] == "faible" and b["regle"] == "moins de b-roll (1 fois)"
    assert all(x["chemin"] for x in regles)  # passage retiré = contenu de cette vidéo, pas une règle


def test_phrases_retirees_et_ajoutees():
    def mots(texte):
        return [{"w": w, "start": float(i), "end": i + 0.5} for i, w in enumerate(texte.split())]
    nous = mots("Tu perds deux heures, par jour. Euh voilà, le vrai problème c'est le café.")
    sa = mots("Tu perds deux heures par jour. Le vrai problème c'est le café, crois-moi vraiment.")
    out = retouches.phrases_changees(nous, sa)
    assert out == [{"quoi": "phrase retirée", "t": 6.0, "texte": "retiré : « Euh voilà, »"},
                   {"quoi": "phrase ajoutée", "t": 12.0, "texte": "ajouté : « crois-moi vraiment. »"}]


def test_zones_changees_fond_flou():
    rng = np.random.default_rng(0)
    nous = rng.integers(0, 255, (30, 64, 36), dtype=np.uint8)
    sa = nous.copy()
    sa[10:20] = 128                                                  # plan posé sur fond flou : bandes unies
    sa[10:20, 20:44] = rng.integers(0, 255, (10, 24, 36), dtype=np.uint8)
    sa[25] = 0                                                       # une image isolée : bruit, ignorée
    rendu = {"broll": [{"source": "B-Roll/drone.mp4", "at": 2.0, "duration": 2.0}]}
    out = retouches.zones_changees(sa, nous, 5, rendu)
    assert out == [{"quoi": "fond flou", "t": 2.0,
                    "texte": "b-roll drone (2,0-4,0 s) : posé entier sur fond flou au lieu de plein cadre"}]
    assert retouches.zones_changees(nous, sa, 5)[0]["quoi"] == "plein cadre"


def test_cli_xml_ecrit_rapport_et_journal(tmp_path):
    f = _timelines(tmp_path)
    r = subprocess.run([sys.executable, str(ROOT / "scripts/retouches.py"), str(f["sa", "fcpxml"]),
                        "--ref-plugin", str(f["nous", "fcpxml"]), "--root", str(tmp_path)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "FORT [zooms.par_minute -]" in r.stdout and "Rien n'est appliqué" in r.stdout
    rap = json.loads((tmp_path / ".monteur/retouches" / f"{datetime.date.today()}.json").read_text(encoding="utf-8"))
    assert rap["mode"] == "xml" and any(d["quoi"] == "passage ajouté" for d in rap["details"])
    journal = [json.loads(l) for l in (tmp_path / "ma-da/corrections.jsonl").read_text(encoding="utf-8").splitlines()]
    assert journal and all(e["source"] == "retouche" and e["video"] == "nous" and e["portee"] == "video" for e in journal)
    assert {"zooms.par_minute"} <= {e["categorie"] for e in journal}
    assert not (tmp_path / "ma-da/profile.json").exists()  # rien d'appliqué
