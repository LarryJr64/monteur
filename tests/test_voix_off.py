"""Mode voix off : index des plans, cadence au rythme de la DA, plans bout à bout, rendu sur un fichier son."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import pacing  # noqa: E402
import plans  # noqa: E402
from common import ffmpeg, probe  # noqa: E402


def ff(*args):
    subprocess.run([ffmpeg(), "-y", "-v", "error", *args], check=True)


def words(n=40, step=0.5, phrase_every=6):
    out, t = [], 0.2
    for i in range(n):
        w = f"mot{i}" + ("." if i % phrase_every == phrase_every - 1 else "")
        out.append({"w": w, "start": round(t, 3), "end": round(t + 0.35, 3)})
        t += step + (0.3 if i % phrase_every == phrase_every - 1 else 0)
    return out


PROFIL = {"rythme": {"plans_quantiles": [1.0, 1.5, 2.0, 3.0, 4.0], "hook_plan_s": 1.2}}


def test_cadence_suit_la_distribution_de_la_da():
    ws = words()
    slots = plans.cadence(ws, PROFIL)
    assert slots[0][0] == 0 and abs(slots[-1][1] - ws[-1]["end"]) < 1e-6
    for (a, b, _), (c, _, _) in zip(slots, slots[1:]):
        assert b == c  # bout à bout
    starts = {w["start"] for w in ws}
    assert all(a in starts for a, _, _ in slots[1:])  # chaque coupe sur un début de mot
    lens = [b - a for a, b, _ in slots]
    hook = [b - a for a, b, _ in slots if a < 3.0]
    assert max(hook) <= 1.2 * 1.4 + 0.5  # accroche plus rapide
    med = sorted(lens)[len(lens) // 2]
    assert 1.2 <= med <= 3.0, lens  # médiane proche de celle de la DA (2,0)
    assert max(lens) - min(lens) > 1.0  # alterne court / long, pas un métronome


def test_cadence_sans_profil_et_preference_fin_de_phrase():
    ws = words()
    slots = plans.cadence(ws, {})
    assert len(slots) >= 5
    fins = sum(1 for a, _, w in slots[1:] if ws[ws.index(w) - 1]["w"].endswith("."))
    assert fins >= len(slots[1:]) // 3  # coupe de préférence entre deux phrases


def _workdir(d):
    for sub in ("rushs", "voix", ".monteur/transcripts", "ma-da"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    ff("-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=8", str(d / "voix" / "vo.wav"))
    # un rush de 2 plans (rouge 0-3 s puis bleu 3-6 s) et un rush vert de 4 s
    ff("-f", "lavfi", "-i", "color=c=red:s=640x360:r=25:d=3", "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=25:d=3",
       "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
       "-pix_fmt", "yuv420p", str(d / "rushs" / "a.mp4"))
    ff("-f", "lavfi", "-i", "color=c=green:s=640x360:r=25:d=4", "-c:v", "libx264", "-preset", "ultrafast",
       "-pix_fmt", "yuv420p", str(d / "rushs" / "b.mp4"))
    ws = [{"w": w, "start": round(0.3 + 0.6 * i, 3), "end": round(0.3 + 0.6 * i + 0.4, 3)}
          for i, w in enumerate("le matin un café puis la ville et enfin la forêt calme".split())]
    tr = {"source": "voix/vo.wav", "duration": 8.0, "language": "fr", "words": ws, "silences": []}
    (d / ".monteur/transcripts/vo.json").write_text(json.dumps(tr), encoding="utf-8")
    return ws


def test_index_puis_plans_bout_a_bout(tmp_path, monkeypatch):
    ws = _workdir(tmp_path)
    monkeypatch.chdir(tmp_path)
    plans.main(["rushs"])
    idx = json.loads((tmp_path / ".monteur/plans.json").read_text(encoding="utf-8"))["plans"]
    assert len(idx) == 3  # rouge, bleu, vert
    assert {(v["source"], round(v["start"])) for v in idx.values()} == {("rushs/a.mp4", 0), ("rushs/a.mp4", 3),
                                                                       ("rushs/b.mp4", 0)}
    assert (tmp_path / ".monteur/plans_1.jpg").exists() and "P1\trushs/a.mp4" in \
        (tmp_path / ".monteur/plans.md").read_text(encoding="utf-8")
    # description ajoutée par Claude : conservée à la relance
    j = json.loads((tmp_path / ".monteur/plans.json").read_text(encoding="utf-8"))
    j["plans"]["P2"]["description"] = "fond bleu"
    (tmp_path / ".monteur/plans.json").write_text(json.dumps(j), encoding="utf-8")
    plans.main(["rushs"])
    md = (tmp_path / ".monteur/plans.md").read_text(encoding="utf-8")
    assert "fond bleu" in md
    # Claude décrit dans plans.md (Edit) : gardé aussi, et repris dans plans.json
    (tmp_path / ".monteur/plans.md").write_text(md.replace("	—", "	plan rouge", 1), encoding="utf-8")
    plans.main(["rushs"])
    j = json.loads((tmp_path / ".monteur/plans.json").read_text(encoding="utf-8"))["plans"]
    assert {v["description"] for v in j.values()} >= {"plan rouge", "fond bleu"}

    bleu = next(k for k, v in idx.items() if v["source"] == "rushs/a.mp4" and v["start"] > 1)
    edl = {"version": 3, "mode": "voix_off", "output": "sorties/vo.mp4",
           "format": {"width": 540, "height": 960, "fps": 25},
           "segments": [{"source": "voix/vo.wav", "start": 0.0, "end": 8.0}], "captions": False,
           "broll": [{"plan": "P1", "voix_t": 0.3, "mot": "le"},
                     {"plan": bleu, "voix_t": 2.1, "mot": "café", "decalage": 2.5},  # déborderait : reculé
                     {"plan": "P3", "voix_t": 4.5, "mot": "enfin"}]}
    # respiration 0,2 s = les pauses de la voix (0,2 s) restent telles quelles : temps final = temps voix − début
    res = pacing.normalize_edl(edl, tmp_path, {"rythme": {"respiration_intra_s": 0.2, "coupe_avant_mot_s": 0.08}})
    br = res["broll"]
    assert br[0]["at"] == 0.0 and len(br) == 3
    for a, b in zip(br, br[1:]):
        assert abs(a["at"] + a["duration"] - b["at"]) < 1e-6  # aucun trou
    total = res["pacing"]["duree_apres_s"]
    assert abs(br[-1]["at"] + br[-1]["duration"] - total) < 1e-6
    cafe = next(w for w in ws if w["w"] == "café")
    debut = ws[0]["start"] - 0.08  # le montage commence 0,08 s avant le 1er mot
    assert abs(br[1]["at"] - (cafe["start"] - debut - 2 / 25)) < 0.045  # sur son mot (2 images avant)
    assert br[1]["source_start"] + br[1]["duration"] <= idx[bleu]["end"] + 1e-6  # ne sort pas du plan bleu
    assert br[1]["source_start"] >= idx[bleu]["start"] - 1e-6


def test_rendu_voix_off_sans_noir(tmp_path, monkeypatch):
    _workdir(tmp_path)
    monkeypatch.chdir(tmp_path)
    plans.main(["rushs"])
    edl = {"version": 3, "mode": "voix_off", "output": "sorties/vo.mp4",
           "format": {"width": 540, "height": 960, "fps": 25},
           "segments": [{"source": "voix/vo.wav", "start": 0.0, "end": 7.5}], "captions": True,
           "broll": [{"plan": "P1", "voix_t": 0.3, "mot": "le"}, {"plan": "P2", "voix_t": 1.5, "mot": "café"},
                     {"plan": "P3", "voix_t": 2.7, "mot": "la"}, {"plan": "P1", "voix_t": 3.9, "mot": "et",
                                                                 "decalage": 1.0},
                     {"plan": "P2", "voix_t": 5.1, "mot": "forêt"}, {"plan": "P3", "voix_t": 6.3, "mot": "calme"}],
           "audio": {"nettoyage_voix": "aucun"}}
    (tmp_path / ".monteur/edl.json").write_text(json.dumps(edl), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPTS / "render.py"), ".monteur/edl.json"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "sorties/vo.mp4"
    info = probe(out)
    assert info["has_audio"] and abs(info["duration"] - json.loads(
        (tmp_path / ".monteur/edl.rendu.json").read_text(encoding="utf-8"))["pacing"]["duree_apres_s"]) < 0.1
    raw = subprocess.run([ffmpeg(), "-v", "error", "-i", str(out), "-vf", "scale=8:8", "-f", "rawvideo",  # chaque image
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    import numpy as np
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 8, 8, 3).astype(int)
    lum = fr.mean(axis=(1, 2, 3))
    assert lum.min() > 25, list(lum.round())  # aucune image noire, même une seule à un raccord
    first, last = fr[0].mean(axis=(0, 1)), fr[-1].mean(axis=(0, 1))
    assert first[0] > first[1] + 40  # commence sur le rouge
    assert last[1] > last[0] + 40  # finit sur le vert (P3)


def test_export_xml_voix_off(tmp_path, monkeypatch):
    """Voix off : les plans forment la piste vidéo principale, la voix une piste son ; XML lisibles."""
    import xml.etree.ElementTree as ET
    _workdir(tmp_path)
    monkeypatch.chdir(tmp_path)
    plans.main(["rushs"])
    edl = {"version": 3, "mode": "voix_off", "output": "sorties/vo.mp4",
           "format": {"width": 540, "height": 960, "fps": 25},
           "segments": [{"source": "voix/vo.wav", "start": 0.0, "end": 7.5}], "captions": True,
           "broll": [{"plan": "P1", "voix_t": 0.3, "mot": "le"}, {"plan": "P3", "voix_t": 3.9, "mot": "et"}]}
    (tmp_path / ".monteur/edl.json").write_text(json.dumps(edl), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPTS / "export_xml.py"), ".monteur/edl.json"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr
    fc = ET.parse(tmp_path / "sorties/vo.fcpxml").getroot()
    spine = fc.find(".//spine")
    assert [c.get("name") for c in spine.findall("asset-clip")] == ["a", "b"]  # les plans, bout à bout
    assert spine.find("asset-clip/asset-clip[@lane='-1']").get("name") == "vo"  # la voix sous le 1er plan
    xm = ET.parse(tmp_path / "sorties/vo.xml").getroot()
    vtracks = xm.findall(".//video/track")
    assert [c.findtext("name") for c in vtracks[0].findall("clipitem")] == ["a", "b"]
    assert [c.findtext("name") for c in xm.findall(".//audio/track")[0].findall("clipitem")] == ["vo"]


def test_descriptions_txt_et_scenes(tmp_path, monkeypatch):
    """Claude décrit les plans en un seul fichier (descriptions.txt) ; un plan long découpé garde sa scène."""
    _workdir(tmp_path)
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=18", "-c:v", "libx264", "-preset", "ultrafast",
       "-pix_fmt", "yuv420p", str(tmp_path / "rushs" / "long.mp4"))
    monkeypatch.chdir(tmp_path)
    plans.main(["rushs"])
    idx = json.loads((tmp_path / ".monteur/plans.json").read_text(encoding="utf-8"))["plans"]
    longs = [v for v in idx.values() if v["source"] == "rushs/long.mp4"]
    assert len(longs) == 2 and longs[0]["scene"] == longs[1]["scene"]  # 18 s → 2 morceaux, même scène
    assert len({v["scene"] for v in idx.values()}) == 4
    (tmp_path / ".monteur/descriptions.txt").write_text("P1: écran rouge\np2 : écran bleu\nP99: inconnu\n",
                                                       encoding="utf-8")
    plans.main(["--voix", "voix/vo.wav"])
    idx = json.loads((tmp_path / ".monteur/plans.json").read_text(encoding="utf-8"))["plans"]
    assert idx["P1"]["description"] == "écran rouge" and idx["P2"]["description"] == "écran bleu"
    md = (tmp_path / ".monteur/plans.md").read_text(encoding="utf-8")
    assert "écran rouge" in md and "scène S" in md and (tmp_path / ".monteur/cadence.md").exists()
