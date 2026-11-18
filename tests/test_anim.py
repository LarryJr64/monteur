import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import anim  # noqa: E402


def test_mot_fort_et_nombre():
    assert anim.mot_fort("Quel a été le plus grand *accomplissement* de ta vie ?") == \
        ("Quel a été le plus grand", "accomplissement", "de ta vie ?")
    assert anim.mot_fort("Nul n'est prophète")[1] == "prophète"  # sans étoiles : le mot le plus long
    assert anim.nombre("15 ans") == (15, "", " ans")
    assert anim.nombre("1 000 clients") == (1000, "", " clients")
    assert anim.nombre("+300 %") == (300, "+", " %")
    assert anim.nombre("zéro") is None


def test_rendu_insert(tmp_path):
    """Un insert animé de 0,5 s sort en MP4 1080×1920 (sauté sans Playwright ni navigateur)."""
    pytest.importorskip("playwright")
    out = tmp_path / "q.mp4"
    r = subprocess.run([sys.executable, str(Path(anim.__file__)), "chiffre", "15 ans", "--sous", "test",
                        "--duree", "0.5", "--sortie", str(out)], capture_output=True, text=True)
    if r.returncode and "navigateur" in r.stderr:
        pytest.skip("aucun navigateur")
    assert r.returncode == 0, r.stderr
    from common import probe
    info = probe(out)
    assert (info["width"], info["height"]) == (1080, 1920) and abs(info["duration"] - 0.5) < 0.1


def test_ponctuation_francaise_jamais_seule():
    assert anim.colle_ponctuation("de ta vie ?") == ["de", "ta", "vie ?"]
    assert "vie ?" in anim.mots("de ta vie ?", 0, 0.1)


def test_mosaique_carte_dans_le_sens_des_clips(monkeypatch):
    tailles = {"h.mp4": (1920, 1080), "v.mp4": (1080, 1920)}
    monkeypatch.setattr(anim, "probe", lambda c: {"width": tailles[c][0], "height": tailles[c][1], "duration": 5})
    f, _, _ = anim.mosaique_filtre(["h.mp4", "h.mp4", "v.mp4"], 3.0, 2)
    assert "crop=960:600" in f[0]
    f, _, _ = anim.mosaique_filtre(["v.mp4"], 3.0, 2)
    assert "crop=860:1180" in f[0]


def test_mosaique_rayon_et_ombre_du_profil(monkeypatch):
    monkeypatch.setattr(anim, "probe", lambda c: {"width": 1920, "height": 1080, "duration": 5})
    f, _, _ = anim.mosaique_filtre(["h.mp4"], 2.0, 2)
    assert "[ombre]" not in "".join(f) and ",40)'" in "".join(f)  # défaut : rendu d'avant
    f, _, _ = anim.mosaique_filtre(["h.mp4"], 2.0, 2, rayon=30, ombre=0.8, fenetres=[(0.0, 1.5)])
    g = ";".join(f)
    assert ",30)'" in g and "aa=0.80" in g and "[ombre]" in g
    assert "trim=0:1.500" in g and "tpad=stop_mode=clone:stop_duration=0.500" in g  # fin floue retirée, image tenue


def test_passage_net_ecarte_la_fin_floue(tmp_path):
    """1 s de mire nette puis 0,5 s d'écran rose uni : le passage s'arrête à la fin de la mire."""
    from common import ffmpeg
    clip = tmp_path / "c.mp4"
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=30:d=1", "-f", "lavfi",
                    "-i", "color=c=pink:s=320x180:r=30:d=0.5", "-filter_complex", "[0][1]concat=n=2:v=1", str(clip)],
                   check=True)
    a, b = anim.passage_net(clip, 1.5)
    assert a == 0 and 0.9 <= b <= 1.05
    a, b = anim.passage_net(clip, 0.6)  # clip plus long que l'extrait : le passage net, sans le rose
    assert b <= 1.05 and b - a == pytest.approx(0.6, abs=0.01)


def test_question_mot_fort_des_la_premiere_image():
    from types import SimpleNamespace
    corps, _ = anim.gabarit("question", SimpleNamespace(textes=["Quel a été le plus grand *accomplissement* de ta vie ?"]),
                            dict(anim.DEFAUT))
    delais = [float(x) for x in re.findall(r'class="m" style="animation-delay:([\d.]+)s"', corps)]
    fort = re.search(r'animation-delay:(-?[\d.]+)s">ACCOMPLISSEMENT', corps)
    assert float(fort.group(1)) <= 0  # déjà à l'écran à l'image 0
    assert len(delais) == 9 and max(delais) < 0.5  # les 9 autres mots partent tous avant 0,5 s


def test_habillage_appris_du_profil(tmp_path):
    """graphismes[] du même type donne les options ; la ligne de commande l'emporte ; sans profil, rendu d'avant."""
    import json
    from types import SimpleNamespace
    prof = tmp_path / "profile.json"
    prof.write_text(json.dumps({"sous_titres": {"police": "Inter Tight", "graisse": 600},
                                "graphismes": [{"type": "question", "police": "Montserrat", "graisse": 600,
                                                "anneaux": "flous", "entree": "disperse", "taille_pct": 0.025,
                                                "sortie": "fuite:#F2B36B"},
                                               {"type": "logo", "entree": "rotation", "taille_pct": 0.55}]}),
                    encoding="utf-8")
    c = anim.da(prof, "question")
    assert (c["police"], c["graisse"], c["g"]["anneaux"]) == ("Inter Tight", 600, "flous")
    assert anim.da(prof, "citation")["g"] == {} and anim.da(None)["graisse"] == 800
    assert "font-weight:600" in anim.font_face("Montserrat") and "font-weight:800" in anim.font_face("Montserrat")
    q = SimpleNamespace(textes=["Quel a été le plus grand *accomplissement* de ta vie ?"], anneaux="flous",
                        entree="disperse", taille=0.025)
    corps, _ = anim.gabarit("question", q, dict(anim.DEFAUT, police="Montserrat", graisse=600))
    assert "anneaux flous" in corps and 'class="d"' in corps and "--dx:" in corps
    assert re.search(r'class="acc pose ombre"[^>]*animation-delay:-\.25s">ACCOMPLISSEMENT', corps)  # sans rotation
    assert "rotate(-60deg)" in anim.gabarit("logo", SimpleNamespace(textes=["NIKKI"], image=None, sous=None, tampon=None,
                                                                    entree="rotation", taille=None), dict(anim.DEFAUT))[0]
    cit, _ = anim.gabarit("citation", SimpleNamespace(textes=["Nul n'est prophète"], sous=None, guillemets="ronds"),
                          dict(anim.DEFAUT))
    assert "Georgia" not in cit and "font-weight:800" in cit
    # 1er mot avec les guillemets (≤ 0,05 s) : jamais de guillemets vides ; le guillemet fermant reste après les mots
    assert re.search(r'class="m" style="animation-delay:0\.000s">Nul<', cit)
    assert "left:-" in cit and re.search(r'top:-\d+px;animation-delay:0\.63s">”', cit)
    assert 'class="fuite"' in anim.sortie_html("fuite:#F2B36B", 3.0) and "--f:#F2B36B;animation-delay:2.60s" in \
        anim.sortie_html("fuite:#F2B36B", 3.0)
    assert anim.sortie_html("fuite:rouge;x", 1)[:30] and "#F2B36B" in anim.sortie_html("fuite:rouge;x", 1)  # couleur saine
    assert anim.sortie_html(None, 2) == "" and anim.sortie_html("coupe", 2) == ""
    vieux, _ = anim.gabarit("question", SimpleNamespace(textes=["Ta *réussite* ?"]), dict(anim.DEFAUT))
    assert 'class="anneaux"' in vieux and "acc pop ombre" in vieux and "font-size:64px" in vieux  # rendu d'avant


def test_fond_rush_cadre_et_etalonne_comme_le_rendu(tmp_path, monkeypatch):
    """--fond <rush 16:9> avec --profil : recadré 9:16 sur le visage et corrigé en couleur, pas le plan large brut."""
    import json
    import types
    import reframe as rf
    from common import ffmpeg, run
    src = tmp_path / "rush.mp4"
    run([ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1920x1080:r=30:d=0.5", str(src)])
    prof = tmp_path / "profile.json"
    prof.write_text(json.dumps({"cadrage": {"visage_largeur": 0.3}, "couleur": {"luma": 0.4}}), encoding="utf-8")
    monkeypatch.setattr(rf, "face_track", lambda v, t0, t1, fps=30, **k: [(t0, 0.7, 0.4, 0.15)] * 15)
    monkeypatch.setattr(rf, "measure_colour", lambda *x, **k: {"luma": 0.5})
    monkeypatch.setattr(rf, "colour_filter", lambda m, target: "eq=brightness=-0.05" if m and target else "")
    a = types.SimpleNamespace(fond=str(src), fond_debut=0.0, sombre=0.6, flou=0, profil=str(prof))
    _, ff = anim.fond_args(a, 0.5)
    crop = re.search(r"crop=(\d+):(\d+):(\d+):(\d+),scale=1080:1920", ff)
    assert crop and int(crop[1]) < 608 and int(crop[3]) > 656  # plus serré que 608×1080, centré à droite (visage)
    assert "eq=brightness=-0.05" in ff and "force_original_aspect_ratio" not in ff
    a.profil = None
    assert "force_original_aspect_ratio" in anim.fond_args(a, 0.5)[1]  # sans profil : comme avant
