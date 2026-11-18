import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mon_style  # noqa: E402
import style_depart  # noqa: E402


def test_page_ma_da_minimale_et_echappement(tmp_path):
    da = tmp_path / "ma-da"
    da.mkdir()
    pire = "<script>alert(1)</script>"
    p = {"rythme": {"duree_moyenne_plan_s": 1.1, "respiration_intra_s": 0.1, "respiration_phrases_s": 0.3},
         "sous_titres": {"police": pire, "couleur": "red;}</style><script>x()</script>", "mots_par_bloc": 2,
                         "mots_cles_par_bloc": 1, "mots_cles_part_blocs": 0.3, "couleur_mot_cle": "#FDE047"},
         "zooms": {"par_minute": 0}, "broll": {"part_pct": 0.2, "usage": pire}, "verrous": ["zooms.par_minute"],
         "references": ["mes-videos/a.mp4"], "sources": [{"chemin": "inspi/" + pire + ".mp4", "date": "2026-11-18", "poids": 2}]}
    (da / "profile.json").write_text(json.dumps(p), encoding="utf-8")
    (da / "STYLE.md").write_text("# Ma DA\n## Règles apprises (corrections)\n<!-- note -->\n- Jamais de zoom " + pire
                                 + "\n## Autre\n- pas ça\n", encoding="utf-8")
    (da / "corrections.jsonl").write_text(json.dumps({"date": "18/11", "texte": "trop " + pire}) + "\nillisible\n",
                                          encoding="utf-8")
    rap = tmp_path / ".monteur" / "rapports"
    rap.mkdir(parents=True)
    for i, n in enumerate((3, 1)):
        f = rap / f"v{i}.json"
        f.write_text(json.dumps({"conforme": False, "ecarts": [{"critere": "x", "conseil": "trop de b-roll (27 % : trop) : retirer"}] * n}),
                     encoding="utf-8")
        os.utime(f, (1e9 + i, 1e9 + i))
    out = mon_style.main(["--projet", str(tmp_path), "--sans-navigateur"])
    h = re.sub(r"@font-face\{[^}]*\}", "", out.read_text(encoding="utf-8"))  # polices en base64 : diff illisible
    assert "<script>" not in h and "&lt;script&gt;" in h  # aucun texte du profil ne devient du code
    assert "background:red" not in h and "color:red" not in h  # couleur invalide écartée du CSS
    assert "Un plan toutes les 1,1 s." in h and "2 mots à la fois" in h and "Pas de zoom." in h
    assert "Jamais de zoom" in h and "pas ça" not in h and "note" not in h  # seule la section Règles apprises
    assert "zooms par minute : <b>0</b>" in h and "poids 2,00" in h and "2026-11-18" in h
    assert "<polyline" in h and "trop de b-roll (27 % : trop)" in h and "retirer" not in h
    assert "Appris sur 1 vidéo." in h


def test_page_style_de_depart(tmp_path):
    style_depart.appliquer("vlog-dynamique", tmp_path)
    h = mon_style.page(tmp_path)
    assert "Style de départ « vlog-dynamique »" in h and "@font-face" in h and "Aucun montage contrôlé" in h


def test_sans_ma_da(tmp_path):
    with pytest.raises(SystemExit):
        mon_style.page(tmp_path)
