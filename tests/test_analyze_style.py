import copy
import json
import subprocess
import sys
from pathlib import Path

from test_measure import FPS, make_video

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_style.py"
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_style  # noqa: E402

# un plan toutes les 1,5 s : jump cuts + un punch-in ×1,15, sous-titres, un silence de 0,3 s
DA = [(0, 1.5, 1, 1), (2.0, 1.5, 1.15, 1.15), (4.0, 1.5, 1, 1), (6.0, 1.5, 1, 1)]


def run(cwd, *args):
    r = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


def _transcript(root, source):
    """Transcript de référence : pauses de 0,2 s dans les phrases, 0,45 s entre phrases, hook = 1re phrase."""
    words, t = [], 0.1
    for phrase in ["Tu perds deux heures.", "Voilà le vrai problème ici.", "Et la solution est simple.",
                   "Teste dès demain matin.", "Abonne-toi pour la suite."]:
        mots = phrase.split()
        for k, m in enumerate(mots):
            words.append({"w": m, "start": round(t, 2), "end": round(t + 0.25, 2), "p": 0.9})
            t += 0.25 + (0.2 if k < len(mots) - 1 else 0.45)
    d = root / ".monteur" / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{Path(source).stem}.json").write_text(json.dumps({"source": source, "words": words}), encoding="utf-8")


def test_profil_images_et_relance(tmp_path):
    refs = tmp_path / "mes-videos"
    refs.mkdir()
    for name in ("v1", "v2"):
        make_video(refs / f"{name}.mp4", DA, captions=True, gaps=[(2.5, 2.8)])
    _transcript(tmp_path, "mes-videos/v1.mp4")
    out = run(tmp_path, "mes-videos", "--sans-transcription")
    assert "Rythme : un plan toutes les 1,5 s" in out and "5 à 10" in out
    assert "Sans transcript" in out and "v2.mp4" in out

    p = json.loads((tmp_path / "ma-da/profile.json").read_text(encoding="utf-8"))
    assert p["version"] == 2
    assert p["format"] == {"width": 360, "height": 640, "fps": FPS}
    assert abs(p["rythme"]["duree_moyenne_plan_s"] - 1.5) < 0.05
    # v1 : plus grand trou entre mots du transcript = 0,45 s ; v2 sans transcript : silence audio 0,3 s → médiane 0,375
    assert abs(p["rythme"]["silence_max_s"] - 0.375) < 0.06
    assert p["rythme"]["respiration_intra_s"] == 0.2 and p["rythme"]["respiration_phrases_s"] == 0.45
    assert p["hook"]["type"] == "chiffre" and abs(p["hook"]["duree_s"] - 1.7) < 0.01
    assert p["zooms"]["par_minute"] > 0 and abs(p["zooms"]["intensite"] - 1.15) <= 0.03
    assert p["zooms"]["types"] == {"cut": 1.0} and p["zooms"]["une_coupe_sur"] == 4
    assert p["sous_titres"]["actif"] is True and p["audio"]["lufs_cible"] < -5
    assert p["sous_titres"]["couleur"].startswith("#F") and p["sous_titres"]["mots_cles_par_bloc"] == 0
    assert set(p["couleur"]) == {"luma", "contraste", "saturation", "temperature"}
    assert set(p["mesures_brutes"]) == {"mes-videos/v1.mp4", "mes-videos/v2.mp4"}
    assert "sous_titres.police" in p["a_completer_par_claude"] and p["verrous"] == []
    frames = tmp_path / "ma-da/frames"
    for suffix in ("sheet", "debut", "fin"):
        assert (frames / f"v1_{suffix}.jpg").exists()
    assert 4 <= len([f for f in frames.glob("*.jpg") if f.stem.split("_")[-1][0].isdigit()]) <= 40

    # Claude complète, l'utilisateur verrouille un réglage ; une relance ne doit rien écraser de tout ça
    p["hook"]["type"] = "question"
    p["zooms"]["style"] = "doux"
    p["rythme"]["duree_moyenne_plan_s"] = 1.2
    p["verrous"] = ["rythme.duree_moyenne_plan_s"]
    p["style_verbal"] = {"registre": "tu"}
    p["a_completer_par_claude"] = []
    (tmp_path / "ma-da/profile.json").write_text(json.dumps(p), encoding="utf-8")
    (tmp_path / "ma-da/STYLE.md").write_text("mes règles", encoding="utf-8")
    run(tmp_path, "mes-videos", "--sans-transcription")
    p2 = json.loads((tmp_path / "ma-da/profile.json").read_text(encoding="utf-8"))
    assert p2["hook"]["type"] == "question" and p2["zooms"]["style"] == "doux"
    assert p2["rythme"]["duree_moyenne_plan_s"] == 1.2 and p2["style_verbal"] == {"registre": "tu"}
    assert p2["a_completer_par_claude"] == []
    assert abs(p2["rythme"]["silence_max_s"] - 0.375) < 0.06  # mesuré, non verrouillé : remesuré
    assert (tmp_path / "ma-da/STYLE.md").read_text(encoding="utf-8") == "mes règles"


def _fake(plan, zpm=6.0, lufs=-14.0, fmt=(1080, 1920, 30)):
    return {"format": dict(zip(("width", "height", "fps"), fmt)), "duree_moyenne_plan_s": plan, "mediane_plan_s": plan,
            "coupes_par_minute": 60 / plan, "silence_max_s": 0.2, "zooms_par_minute": zpm, "zoom_intensite": 1.12,
            "zoom_style": "cut", "zooms": [{"type": "punch"}, {"type": "doux"}], "lufs": lufs, "duree": 40.0,
            "sous_titres_presence": 0.9, "fond_sonore_db": -180}


def test_reference_atypique_et_profil_v1():
    mes = {f"v{i}.mp4": _fake(2.0 + 0.1 * i) for i in range(4)}
    mes["bizarre.mp4"] = _fake(6.0, zpm=0.5, lufs=-25)
    p = analyze_style.build_profile(mes)
    assert [a["reference"] for a in p["references_atypiques"]] == ["bizarre.mp4"]
    assert p["dispersion"]["duree_moyenne_plan_s"]["max"] == 6.0
    assert abs(p["rythme"]["duree_moyenne_plan_s"] - 2.2) < 1e-6  # la médiane résiste à la vidéo atypique
    assert p["zooms"]["types"] == {"punch": 0.5, "doux": 0.5}
    assert "ne ressemble pas aux autres" in analyze_style.summary(p)

    # profil v1 complété par Claude : relance v2 = nouveaux champs + nouvelles choses à compléter
    v1 = {"version": 1, "rythme": {"duree_moyenne_plan_s": 3.0}, "sous_titres": {"police": "Anton", "couleur": "#FFFF00"},
          "zooms": {"style": "doux"}, "a_completer_par_claude": []}
    merged = analyze_style.merge_existing(p, copy.deepcopy(v1))
    assert merged["version"] == 2 and merged["sous_titres"]["police"] == "Anton"
    assert merged["zooms"]["style"] == "doux" and merged["rythme"]["duree_moyenne_plan_s"] == p["rythme"]["duree_moyenne_plan_s"]
    assert "sous_titres.emojis" in merged["a_completer_par_claude"] and "respiration_intra_s" in merged["rythme"]


def test_da_neutre_sans_reference(tmp_path):
    import analyze_style as an
    import check
    an.main(["--neutre", "--out", str(tmp_path / "ma-da")])
    prof = json.loads((tmp_path / "ma-da/profile.json").read_text(encoding="utf-8"))
    assert prof["neutre"] and prof["format"]["height"] == 1920
    m = {"duree_moyenne_plan_s": 2.4, "duree": 40, "plans": [[0, 40]], "silence_max_s": 0.4, "silences": [],
         "lufs": -14, "format": {}, "zooms_par_minute": 8, "sous_titres_presence": 1}
    check.compare(m, prof)  # le contrôle tourne sur la DA neutre


def test_debit_et_voix_sous_carton_dans_le_profil():
    mes = {f"v{i}.mp4": dict(_fake(2.0), debit_mots_s=3.6 + 0.1 * i, voix_sous_carton=i == 0) for i in range(3)}
    p = analyze_style.build_profile(mes)
    assert p["rythme"]["debit_mots_s"] == 3.7 and p["hook"]["voix_sous_carton"] is False  # 2 cartons muets sur 3


def test_son_diffusion_carton_et_musique_absente():
    carton = {"impact": True, "intervalle_s": 0.267, "riser": True}
    mes = {f"v{i}.mp4": dict(_fake(2.0, lufs=-15.5), sfx_par_minute=5.0, sfx_carton=carton if i == 1 else None)
           for i in range(3)}
    p = analyze_style.build_profile(mes)
    a = p["audio"]
    assert a["lufs_cible"] == -14 and a["lufs_reference"] == -15.5  # diffusion -14, mesure du pro pour information
    assert a["sfx_carton"] == carton and a["sfx_par_minute"] == 5.0 and a["musique_db_sous_voix"] is None
    # ancien profil : -15,5 copié de la référence et -18 de musique alors que la DA n'en a pas → remis d'aplomb
    old = copy.deepcopy(p)
    old["audio"].update(lufs_cible=-15.5, musique_db_sous_voix=-18)
    merged = analyze_style.merge_existing(p, old)
    assert merged["audio"]["lufs_cible"] == -14 and merged["audio"]["musique_db_sous_voix"] is None
    old["verrous"] = ["audio.lufs_cible"]
    assert analyze_style.merge_existing(p, old)["audio"]["lufs_cible"] == -15.5  # réglage de l'utilisateur gardé


def test_ponderation_sources_et_ajout(tmp_path, monkeypatch):
    import datetime
    today = datetime.date(2026, 11, 18)
    ancien = {"video": "inspi/a.mp4", "type": "inspi", "date": "2025-01-01"}
    assert analyze_style.poids(ancien, today) == 1
    assert analyze_style.poids({"type": "inspi", "date": "2026-11-01"}, today) == 2
    assert analyze_style.poids({"type": "mien", "date": "2026-11-01"}, today) == 4  # son reel validé, récent
    assert analyze_style.poids({"type": "mien", "date": None}, today) == 2
    assert analyze_style.type_source("Références/x.mp4") == "inspi" and analyze_style.type_source("mes-videos/x.mp4") == "mien"

    # 2 inspis anciennes à 1,0 s, son reel récent à 2,0 s : médiane simple 1,0 ; pondérée (1, 1, 4) → 2,0
    mes = {"inspi/a.mp4": _fake(1.0), "inspi/b.mp4": _fake(1.0), "sorties/mon-reel.mp4": _fake(2.0, zpm=12.0)}
    sources = analyze_style.sources_de(mes, [dict(ancien), {"video": "inspi/b.mp4", "type": "inspi", "date": None},
                                             {"video": "sorties/mon-reel.mp4", "type": "mien", "date": "2026-11-18"}], today)
    assert [s["poids"] for s in sources] == [1, 1, 4]
    assert analyze_style.build_profile(mes)["rythme"]["duree_moyenne_plan_s"] == 1.0
    p = analyze_style.build_profile(mes, sources)
    assert p["rythme"]["duree_moyenne_plan_s"] == 2.0 and p["zooms"]["par_minute"] == 12.0
    assert p["references"] == list(mes) and p["sources"] == sources
    assert p["dispersion"]["duree_moyenne_plan_s"]["mediane"] == 1.0  # plage de ses vidéos : sans pondération

    # --ajoute : une vidéo de plus, verrous jamais touchés, sources gardées avec leur date
    old = analyze_style.build_profile({k: mes[k] for k in ("inspi/a.mp4", "inspi/b.mp4")})
    old["zooms"]["par_minute"] = 3.0
    old["verrous"] = ["zooms.par_minute"]
    (tmp_path / "ma-da").mkdir()
    (tmp_path / "ma-da/profile.json").write_text(json.dumps(old), encoding="utf-8")
    (tmp_path / "sorties").mkdir()
    (tmp_path / "sorties/mon-reel.mp4").write_bytes(b"x")
    monkeypatch.setattr(analyze_style.measure, "measure", lambda v, w=None: _fake(2.0, zpm=12.0))
    monkeypatch.chdir(tmp_path)
    analyze_style.main(["--ajoute", "sorties/mon-reel.mp4", "--sans-transcription"])
    p2 = json.loads((tmp_path / "ma-da/profile.json").read_text(encoding="utf-8"))
    assert set(p2["mesures_brutes"]) == set(mes) and p2["verrous"] == ["zooms.par_minute"]
    assert p2["zooms"]["par_minute"] == 3.0  # verrouillé par elle : la nouvelle vidéo ne l'écrase pas
    new = next(s for s in p2["sources"] if s["video"] == "sorties/mon-reel.mp4")
    assert new["type"] == "mien" and new["ajoute"] and new["poids"] == 4
    assert all(s["date"] is None for s in p2["sources"] if s["video"] != "sorties/mon-reel.mp4")  # profil d'avant : anciennes
    assert p2["rythme"]["duree_moyenne_plan_s"] == 2.0  # son reel pèse 4 contre 1 + 1
