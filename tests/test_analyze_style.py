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
