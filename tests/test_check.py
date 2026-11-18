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


def test_debit_lent_et_broll_part_max():
    import check as chk
    profile = {"rythme": {"debit_mots_s": 3.74}, "broll": {"part_pct": 0.12}}
    m = {"duree_moyenne_plan_s": 2, "duree": 40, "plans": [[0, 40]], "silence_max_s": 0, "silences": [], "lufs": None,
         "format": {}, "zooms_par_minute": 0, "sous_titres_presence": 0, "debit_mots_s": 3.08, "broll_part": 0.04}
    ecarts = {e["critere"]: e for e in chk.compare(m, profile, e={"broll_part": 0.11})}
    assert "parole trop lente" in ecarts["debit_mots_s"]["conseil"]
    assert "broll_part" not in ecarts  # visages dans les clips de foule : l'edl rendu fait foi (11 %)
    m["debit_mots_s"] = 3.5
    assert "debit_mots_s" not in {e["critere"] for e in chk.compare(m, profile)}


def test_raccord_interne_de_pacing_pas_un_mot_coupe(setup):
    import check as chk
    words = _rushs_transcript(setup)
    w = words[4]  # « bosses » : pacing a retiré une vraie pause rangée dans ce mot
    cut = w["start"] + 0.2  # comme pacing : le milieu du mot reste dans un morceau
    rendu = {"segments": [{"source": "rushs/a.mp4", "start": 0.0, "end": cut, "seg": 0, "piece": 0},
                          {"source": "rushs/a.mp4", "start": cut + 0.1, "end": 6.0, "seg": 0, "piece": 1}]}
    ws, coupes, _ = chk.output_words(rendu, setup)
    assert coupes == [] and [x["w"] for x in ws].count(w["w"]) == 1  # compté une fois, au morceau de son milieu
    rendu["segments"][1]["seg"] = 1  # vrai raccord de Claude entre deux segments : signalé
    rendu["segments"][1]["piece"] = 0
    assert [c["mot"] for c in chk.output_words(rendu, setup)[1]] == [w["w"]]


def test_debut_au_creux_avant_la_fin_transcrite_du_mot_d_avant(setup):
    import check as chk
    words = _rushs_transcript(setup)
    # pacing commence « Tu » 0,06 s avant la fin transcrite de « problème. » (creux d'énergie)
    seg = {"source": "rushs/a.mp4", "start": words[2]["end"] - 0.06, "end": 6.0, "seg": 0, "piece": 0}
    assert chk.output_words({"segments": [seg]}, setup)[1] == []
    seg["start"] = words[2]["end"] - 0.2  # vraiment dans le mot : signalé
    assert [c["mot"] for c in chk.output_words({"segments": [seg]}, setup)[1]] == [words[2]["w"]]


def test_plan_hors_etalonnage_et_bruitages_du_carton(tmp_path):
    import check as chk
    from common import ffmpeg
    v = tmp_path / "sortie.mp4"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xC09070:s=180x320:r=25:d=3",
                    "-f", "lavfi", "-i", "color=c=0x101040:s=180x320:r=25:d=1", "-f", "lavfi",
                    "-i", "color=c=0xC09070:s=180x320:r=25:d=1", "-filter_complex", "[0][1][2]concat=n=3", "-pix_fmt",
                    "yuv420p", str(v)], check=True)
    edl = {"segments": [{"source": "rush.mov", "start": 0, "end": 5}],
           "broll": [{"source": "club.mp4", "at": 3.0, "duration": 1.0},
                     {"source": "anim.mp4", "at": 3.0, "duration": 1.0, "texte": True}],  # insert graphique : exclu
           "sfx": [{"type": "impact", "at": 0, "carton": True}, {"type": "whoosh", "at": 3.0}]}
    hors = chk.etalonnage(v, edl)
    assert [x["source"] for x in hors] == ["club.mp4"] and hors[0]["luma"] < 0.12
    edl["broll"][0]["at"] = 0.5  # plan à la même lumière que le rush : rien
    assert chk.etalonnage(v, edl) == []
    e = chk.edl_measures(edl, tmp_path, {})
    assert e["sfx_par_minute"] == 12.0  # le bruitage du carton n'est pas compté (1 en 5 s)
    m = {"duree_moyenne_plan_s": 2, "duree": 5, "plans": [[0, 5]], "silence_max_s": 0, "silences": [], "lufs": -14,
         "format": {}, "zooms_par_minute": 0, "sous_titres_presence": 1}
    ecarts = {x["critere"]: x for x in chk.compare(m, {}, e={"etalonnage": hors})}
    assert "club.mp4" in ecarts["plan_hors_etalonnage"]["mesure"][0]


def test_zooms_aux_bornes_de_broll_et_raccords_ignores_et_sans_bruitage():
    import check as chk
    sg = [{"start": 0, "end": 2}, {"start": 2, "end": 4, "zoom": 1.2, "zoom_type": "raccord"},
          {"start": 4, "end": 6, "zoom": 1.0, "zoom_type": "aucun"}, {"start": 6, "end": 9, "zoom": 1.2}]
    edl = {"segments": [dict(x, source="absent.mp4", muet=True) for x in sg],
           "broll": [{"at": 6.5, "duration": 1.0}]}
    m = {"duree": 60, "zooms": [{"t": 2.05}, {"t": 6.0}, {"t": 6.8}, {"t": 7.45}]}
    chk.zooms_hors_bornes(m, edl)
    assert [z["t"] for z in m["zooms"]] == [6.0, 6.8] and m["zooms_par_minute"] == 2.0  # punch posé + zoom dans le plan
    assert chk.edl_measures(edl, Path("."), {})["zooms_par_minute"] == round(1 / (9 / 60), 2)  # « raccord » hors compte
    profile = {"audio": {"sfx_par_minute": 6}, "verrous": ["audio.sfx_par_minute"]}
    m = {"duree_moyenne_plan_s": 2, "duree": 60, "plans": [[0, 60]], "silence_max_s": 0, "silences": [], "lufs": None,
         "format": {}, "zooms_par_minute": 0, "sous_titres_presence": 1, "sfx_par_minute": 5.4}
    assert "sfx_par_minute" in {x["critere"] for x in chk.compare(m, profile, e={"sfx_par_minute": 0})}
    assert "sfx_par_minute" not in {x["critere"] for x in chk.compare(m, profile, e={"sfx_par_minute": 0,
                                                                                      "sans_bruitage": True})}
    assert chk.SANS_BRUITAGE.search("- Pas de bruitage (non confirmé).")


def test_plan_agrandi_signale(tmp_path):
    """Paysage très peu défini recadré plein cadre (×4) : « plan agrandi » ; en fond flou, insert ou 4K : rien."""
    import check as chk
    from common import ffmpeg
    for nom, taille in (("club.mp4", "720x480"), ("k4.mp4", "3840x2160")):
        subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s={taille}:d=0.2",
                        str(tmp_path / nom)], check=True)
    edl = {"format": {"width": 1080, "height": 1920},
           "broll": [{"source": "club.mp4", "at": 21.3, "duration": 2.7}, {"source": "k4.mp4", "at": 1, "duration": 1},
                     {"source": "club.mp4", "at": 5, "duration": 1, "texte": True}]}
    assert chk.agrandis(edl, tmp_path) == [{"source": "club.mp4", "at": 21.3, "agrandi": 4.0}]
    edl["broll"][0]["fond_flou"] = True
    assert chk.agrandis(edl, tmp_path) == []
    m = {"duree_moyenne_plan_s": 2, "duree": 5, "plans": [[0, 5]], "silence_max_s": 0, "silences": [], "lufs": -14,
         "format": {}, "zooms_par_minute": 0, "sous_titres_presence": 1}
    e = {"agrandis": [{"source": "club.mp4", "at": 21.3, "agrandi": 1.92}]}
    assert "plan agrandi ×1,92 (flou)" in {x["critere"]: x for x in chk.compare(m, {}, e=e)}["plan_agrandi"]["mesure"][0]
