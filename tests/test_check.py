import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_measure import make_video

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_style  # noqa: E402
import measure  # noqa: E402

DA = [(0, 1.5, 1, 1), (2.0, 1.5, 1.15, 1.15), (4.0, 1.5, 1, 1), (6.0, 1.5, 1, 1)]


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    d = tmp_path_factory.mktemp("check")
    ref = make_video(d / "ref.mp4", DA, captions=True)
    profile = analyze_style.build_profile({"ref.mp4": measure.measure(ref)})
    (d / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    make_video(d / "bon.mp4", [(1, 1.5, 1, 1), (3.0, 1.5, 1, 1), (5.0, 1.5, 1.15, 1.15), (7.0, 1.5, 1, 1)],
               captions=True)
    make_video(d / "lent.mp4", [(0, 6, 1, 1)])
    return d


def check(d, video, *extra):
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check.py"), video, "--profil", "profile.json", *extra],
                       cwd=d, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads((d / ".monteur/rapports" / f"{Path(video).stem}.json").read_text(encoding="utf-8")), r.stdout


def test_montage_conforme(setup):
    rapport, out = check(setup, "bon.mp4", "--duree-cible", "6")
    assert rapport["conforme"], rapport["ecarts"]
    assert "CONFORME" in out


def test_montage_trop_lent(setup):
    rapport, out = check(setup, "lent.mp4", "--duree-cible", "12")
    criteres = {e["critere"] for e in rapport["ecarts"]}
    assert {"duree_moyenne_plan_s", "sous_titres", "duree_totale_s"} <= criteres
    assert not rapport["conforme"] and "NON CONFORME" in out
    plan = next(e for e in rapport["ecarts"] if e["critere"] == "duree_moyenne_plan_s")
    assert "ajouter ~3 coupe" in plan["conseil"]


def test_edl_sans_sous_titres(setup):
    (setup / "edl.json").write_text(json.dumps({"captions": False}), encoding="utf-8")
    rapport, _ = check(setup, "bon.mp4", "--edl", "edl.json")
    assert [e["critere"] for e in rapport["ecarts"]] == ["sous_titres"]


def test_champs_inconnus_ignores(setup):
    p = json.loads((setup / "profile.json").read_text(encoding="utf-8"))
    p["style_verbal"] = "tutoiement"
    p["hook"]["type"] = "question"
    (setup / "profile.json").write_text(json.dumps(p), encoding="utf-8")
    rapport, _ = check(setup, "bon.mp4")
    assert rapport["conforme"]


def _rushs_transcript(d):
    """Rush « a » : 3 phrases ; pauses de 0,7 s entre phrases (le créateur, lui, en laisse 0,38)."""
    words, t = [], 0.0
    for phrase in ["Le vrai problème.", "Tu bosses trop.", "Repose-toi enfin.", "Et reviens fort."]:
        for k, m in enumerate(phrase.split()):
            words.append({"w": m, "start": round(t, 2), "end": round(t + 0.3, 2)})
            t += 0.3 + (0.15 if k < len(phrase.split()) - 1 else 0.7)
    tdir = d / ".monteur" / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "a.json").write_text(json.dumps({"source": "rushs/a.mp4", "words": words}), encoding="utf-8")
    return words


def test_edl_rendu_mots_coupes_broll_sur_hook_respiration(setup):
    words = _rushs_transcript(setup)
    fin = words[-1]["end"]
    # 2e segment commence au milieu de « bosses » ; b-roll posé pendant le hook
    rendu = {"output": "sorties/bon.mp4",
             "segments": [{"source": "rushs/a.mp4", "start": 0.0, "end": 1.5, "note": "hook"},
                          {"source": "rushs/a.mp4", "start": words[4]["start"] + 0.1, "end": fin + 0.1}],
             "broll": [{"source": "broll/x.mp4", "at": 0.5, "duration": 1.0}]}
    (setup / "edl.json").write_text(json.dumps({"captions": True}), encoding="utf-8")
    (setup / "edl.rendu.json").write_text(json.dumps(rendu), encoding="utf-8")
    # réf. sans transcript : respiration = défaut, comparée seulement si l'utilisateur l'a imposée
    p = json.loads((setup / "profile.json").read_text(encoding="utf-8"))
    p["verrous"] = ["rythme.respiration_phrases_s"]
    (setup / "profile_resp.json").write_text(json.dumps(p), encoding="utf-8")
    try:
        rapport, out = check(setup, "bon.mp4", "--edl", "edl.json", "--profil", "profile_resp.json")
    finally:
        (setup / "edl.rendu.json").unlink()
    ecarts = {e["critere"]: e for e in rapport["ecarts"]}
    assert ecarts["mots_coupes"]["mesure"] == 1 and "bosses" in ecarts["mots_coupes"]["conseil"]
    assert ecarts["broll_sur_hook"]["mesure"] == ["broll/x.mp4"]
    assert ecarts["respiration_phrases_s"]["mesure"] == 0.7 and "resserrer" in ecarts["respiration_phrases_s"]["conseil"]
    assert "respiration_intra_s" not in ecarts  # défaut non mesuré, non verrouillé


def test_rendu_d_une_autre_video_ignore(setup):
    (setup / "edl.rendu.json").write_text(json.dumps({"output": "sorties/autre.mp4", "segments": [
        {"source": "rushs/a.mp4", "start": 0.0, "end": 0.5}], "broll": [{"source": "b.mp4", "at": 0, "duration": 1}]}),
        encoding="utf-8")
    try:
        rapport, out = check(setup, "bon.mp4", "--edl", "edl.json")
    finally:
        (setup / "edl.rendu.json").unlink()
    assert "ignoré" in out and "broll_sur_hook" not in {e["critere"] for e in rapport["ecarts"]}


def test_couleur_et_bruitages():
    import check as chk
    profile = {"couleur": {"luma": 0.5, "saturation": 0.3}, "audio": {"sfx_par_minute": 6, "sfx_types": ["whoosh"]},
               "zooms": {"par_minute": 6, "types": {"punch": 0.6, "doux": 0.4}, "une_coupe_sur": 2}}
    m = {"duree_moyenne_plan_s": 2, "duree": 60, "plans": [[0, 60]], "silence_max_s": 0, "silences": [], "lufs": -14,
         "format": {}, "zooms_par_minute": 1, "sous_titres_presence": 1, "couleur": {"luma": 0.35, "saturation": 0.31},
         "sfx_par_minute": None}
    # estimation non confirmée par l'utilisateur : pas contrôlée
    assert "sfx_par_minute" not in {e["critere"] for e in chk.compare(m, profile, e={"sfx_par_minute": 1})}
    profile["verrous"] = ["audio.sfx_par_minute"]
    ecarts = {e["critere"]: e for e in chk.compare(m, profile, e={"sfx_par_minute": 1})}
    assert "sombre" in ecarts["couleur_luma"]["conseil"] and "couleur_saturation" not in ecarts
    assert ecarts["sfx_par_minute"]["mesure"] == 1 and "whoosh" in ecarts["sfx_par_minute"]["conseil"]
    assert "ajouter ~5 zoom" in ecarts["zooms_par_minute"]["conseil"] and "punch 60 %" in ecarts["zooms_par_minute"]["conseil"]
