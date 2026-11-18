import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_style  # noqa: E402
import anim  # noqa: E402
import captions  # noqa: E402
import style_depart  # noqa: E402
from test_analyze_style import _fake  # noqa: E402

NOMS = ["interview-posee", "podcast-extrait", "pub-produit", "tuto-face-cam", "vlog-dynamique"]
GABARITS = {"question", "citation", "lieu", "logo", "chiffre", "liste", "mosaique", "html"}


def test_liste_des_styles():
    s = style_depart.styles()
    assert list(s) == NOMS and all(len(ligne) > 20 for ligne in s.values())


@pytest.mark.parametrize("nom", NOMS)
def test_profil_valide_pour_les_scripts(nom):
    path = ROOT / "styles" / nom / "profile.json"
    p = json.loads(path.read_text(encoding="utf-8"))
    assert p["origine"] == "style de depart" and "verrous" not in p and p["version"] == 2
    for cle, val in analyze_style.DEFAUTS.items():  # mêmes clés que les profils appris
        if isinstance(val, dict):
            assert set(val) <= set(p[cle]), (cle, set(val) - set(p[cle]))
    r = p["rythme"]
    q = r["plans_quantiles"]
    assert q == sorted(q) and q[0] < r["duree_moyenne_plan_s"] < q[-1]
    assert abs(sum(p["zooms"]["types"].values()) - 1) < 0.01 and set(p["zooms"]["types"]) <= {"doux", "punch", "cut", "crash"}
    assert set(p["audio"]["sfx_types"]) <= {"whoosh", "pop", "impact", "ding", "click", "riser"}
    assert 0 <= p["broll"]["part_pct"] <= 0.5 and set(p["a_completer_par_claude"]) == set(analyze_style.A_COMPLETER)
    # sous-titres : police embarquée, animation connue, valeurs utilisables par le rendu
    st = captions.clean_style(p["sous_titres"])
    assert captions.font_name(st)[0] in captions.FONT_FILES
    assert st["animation"] in captions.ANIMATIONS and 1 <= st["mots_par_bloc"] <= 6
    assert captions.metrics(st, 1920)[2] > 20
    # graphismes : lus par anim.da, gabarit et options connus
    for g in p["graphismes"]:
        assert g["type"] in GABARITS and set(g) <= set(anim.SCHEMA.values()) | {"type", "quand", "duree_s", "detail", "sous_titres"}
        c = anim.da(path, g["type"])
        assert c["g"] == g and anim.HEX.fullmatch(c["accent"]) and anim.bundled(c["police"])


@pytest.mark.parametrize("nom", NOMS)
def test_references_ecrasent_le_style_de_depart(nom):
    depart = json.loads((ROOT / "styles" / nom / "profile.json").read_text(encoding="utf-8"))
    mes = {f"v{i}.mp4": _fake(3.0) for i in range(3)}
    new = analyze_style.build_profile(mes)
    merged = analyze_style.merge_existing(new, copy.deepcopy(depart))
    assert merged["rythme"]["duree_moyenne_plan_s"] == 3.0 and merged["zooms"]["style"] == "cut"
    assert merged["zooms"]["par_minute"] == 6.0 and merged["references"] == list(mes)


def test_copie_sans_ecraser(tmp_path, capsys):
    da = style_depart.appliquer("vlog-dynamique", tmp_path)
    assert json.loads((da / "profile.json").read_text(encoding="utf-8"))["style_depart"] == "vlog-dynamique"
    (da / "STYLE.md").write_text("ma DA à moi", encoding="utf-8")
    with pytest.raises(SystemExit):
        style_depart.appliquer("pub-produit", tmp_path)
    assert (da / "STYLE.md").read_text(encoding="utf-8") == "ma DA à moi"
    style_depart.appliquer("pub-produit", tmp_path, remplacer=True)
    gardes = list(tmp_path.glob("ma-da.avant-*"))
    assert len(gardes) == 1 and (gardes[0] / "STYLE.md").read_text(encoding="utf-8") == "ma DA à moi"
    with pytest.raises(SystemExit):
        style_depart.appliquer("inconnu", tmp_path / "x")
