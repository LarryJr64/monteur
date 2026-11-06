import json
import re
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import captions  # noqa: E402
import common  # noqa: E402
import pacing  # noqa: E402

FPS = 100  # grille fine : les attentes restent exactes à 10 ms près
W = lambda t, a, b, **k: {"w": t, "start": a, "end": b, "p": 0.9, **k}  # noqa: E731

# « Salut tout le monde. » [1.0 s] « Aujourd'hui on » [0.6 s] « parle du café. » [0.9 s] « Le café, c'est la vie. »
WORDS = [W("Salut", 0.5, 0.8), W("tout", 0.8, 1.0), W("le", 1.0, 1.1), W("monde.", 1.1, 1.5),
         W("Aujourd'hui", 2.5, 3.0), W("on", 3.0, 3.1),
         W("parle", 3.7, 4.0), W("du", 4.0, 4.1), W("café.", 4.1, 4.5),
         W("Le", 5.4, 5.5), W("café,", 5.5, 5.9), W("c'est", 5.9, 6.1), W("la", 6.1, 6.2), W("vie.", 6.2, 6.6)]


def setup(tmp_path, words=WORDS, name="a"):
    t = tmp_path / ".monteur" / "transcripts"
    t.mkdir(parents=True, exist_ok=True)
    (t / f"{name}.json").write_text(json.dumps({"source": f"rushs/{name}.mp4", "duration": 10.0, "language": "fr",
                                               "model": "test", "words": words, "silences": []}), encoding="utf-8")
    return tmp_path


def edl(segments, **extra):
    return {"version": 2, "format": {"width": 1080, "height": 1920, "fps": FPS}, "segments": segments, **extra}


def seg(a, b, src="a", **k):
    return {"source": f"rushs/{src}.mp4", "start": a, "end": b, **k}


def out_words(res, root):
    """Mots gardés en temps final, comme les verra render (captions.timeline_words sur les segments recalés)."""
    return captions.timeline_words(captions.snapped_segments(res, root), root)


def test_compression_exact(tmp_path):
    root = setup(tmp_path)
    res = pacing.normalize_edl(edl([seg(0.0, 7.0)]), root, {})
    got = [(round(s["start"], 2), round(s["end"], 2)) for s in res["segments"]]
    # fin de phrase : 0.30 de traîne + 0.08 de pré-roll = 0.38 ; dans la phrase : 0.10 + 0.08 = 0.18
    assert got == [(0.42, 1.8), (2.42, 3.2), (3.62, 4.8), (5.32, 6.9)]
    tw = out_words(res, root)
    gaps = [round(b["start"] - a["end"], 2) for a, b in zip(tw, tw[1:]) if b["start"] - a["end"] > 0.01]
    assert gaps == [0.38, 0.18, 0.38]
    assert res["pacing"]["duree_avant_s"] == 7.0 and res["pacing"]["duree_apres_s"] == pytest.approx(4.92)
    assert [s["piece"] for s in res["segments"]] == [0, 1, 2, 3] and {s["seg"] for s in res["segments"]} == {0}
    assert json.loads((root / ".monteur" / "edl.rendu.json").read_text(encoding="utf-8"))["segments"] == res["segments"]


def test_profile_breathing_and_small_gaps_untouched(tmp_path):
    root = setup(tmp_path)
    prof = {"rythme": {"respiration_intra_s": 0.55, "respiration_phrases_s": 0.5, "coupe_avant_mot_s": 0.05}}
    res = pacing.normalize_edl(edl([seg(0.0, 7.0)]), root, prof)
    # 0.6 s dans la phrase < 0.55 + MIN_CUT : pas de raccord pour gagner 0.1 s
    assert len(res["segments"]) == 3
    tw = out_words(res, root)
    assert [round(b["start"] - a["end"], 2) for a, b in zip(tw, tw[1:]) if b["start"] - a["end"] > 0.01] == [0.5, 0.6, 0.5]


def test_no_word_clipped(tmp_path):
    root = setup(tmp_path)
    # coupes de Claude au ras (voire dans) les mots : jamais de fin de mot coupée
    res = pacing.normalize_edl(edl([seg(0.55, 1.45), seg(2.6, 4.48), seg(5.45, 6.58)]), root, {})
    for s in res["segments"]:
        for w in WORDS:
            mid = (w["start"] + w["end"]) / 2
            if s["start"] <= mid < s["end"]:
                assert s["start"] <= w["start"] - 0.03 and s["end"] >= w["end"] + pacing.TAIL_MIN - 1e-9, (s, w)
    # raccord entre segments = vraie respiration (pas zéro)
    tw = out_words(res, root)
    assert all(b["start"] - a["end"] >= 0.17 for a, b in zip(tw, tw[1:]) if a["w"].endswith(".") or b["w"] == "parle")


def test_touching_neighbour_not_included(tmp_path):
    root = setup(tmp_path)
    res = pacing.normalize_edl(edl([seg(0.8, 1.45)]), root, {})  # « tout le monde. » sans « Salut » (collé)
    s = res["segments"][0]
    assert s["start"] == pytest.approx(0.8) and s["end"] >= 1.5 + pacing.TAIL_MIN


def test_fillers_cut_kept_or_note(tmp_path):
    words = [W("Alors", 0.5, 0.9), W("[euh]", 1.2, 1.6, filler=True, p=0.0), W("voilà.", 1.9, 2.3),
             W("Bon", 3.0, 3.2), W("euh", 3.2, 3.4), W("ok.", 3.45, 3.7)]
    root = setup(tmp_path, words)
    res = pacing.normalize_edl(edl([seg(0.0, 4.0)]), root, {})
    tw = [w["w"] for w in out_words(res, root)]
    assert "[euh]" not in tw and "euh" in tw  # « euh » collé à ses voisins : pas de coupe propre possible → gardé
    assert res["pacing"]["hesitations_coupees"] == 1
    first = res["segments"][0]
    assert first["end"] <= 1.2 and res["segments"][1]["start"] >= 1.6  # aucune image du « euh »
    kept = pacing.normalize_edl(edl([seg(0.0, 4.0, note="garder les euh")]), root, {})
    assert "[euh]" in [w["w"] for w in out_words(kept, root)]


def test_filler_short_gap_still_cut(tmp_path):
    # fin de phrase + « euh » court : l'écart total (0.45 s) ne justifie pas une coupe de silence, mais le euh part
    words = [W("Voilà.", 0.5, 1.0), W("euh", 1.12, 1.33), W("Ensuite", 1.45, 1.9)]
    root = setup(tmp_path, words)
    res = pacing.normalize_edl(edl([seg(0.0, 2.5)]), root, {})
    assert [w["w"] for w in out_words(res, root)] == ["Voilà.", "Ensuite"]
    a, b = res["segments"]
    assert a["end"] <= 1.12 + 1e-9 and b["start"] >= 1.33 - 1e-9


def test_zoom_first_piece_only_and_fields_kept(tmp_path):
    root = setup(tmp_path)
    res = pacing.normalize_edl(edl([seg(0.0, 7.0, zoom=1.15, zoom_type="punch", mots_cles=["café", "monde"], emoji="☕",
                                        note="x")]), root, {})
    z = [(s["zoom"], s["zoom_type"]) for s in res["segments"]]
    assert z[0] == (1.15, "punch") and all(x == (1.0, "aucun") for x in z[1:])
    # mots-clés seulement sur le morceau qui les dit, emoji une seule fois, autres champs recopiés
    assert [s.get("mots_cles") for s in res["segments"]] == [["monde"], None, ["café"], ["café"]]
    assert [s.get("emoji") for s in res["segments"]] == ["☕", None, None, None]
    assert all(s["note"] == "x" and s["seg"] == 0 for s in res["segments"])
    doux = pacing.normalize_edl(edl([seg(0.0, 7.0, zoom=1.1, zoom_type="doux")]), root, {})
    ps = doux["segments"]
    assert ps[0]["zoom_from"] == 1.0 and ps[-1]["zoom_to"] == pytest.approx(1.1)
    assert all(a["zoom_to"] == b["zoom_from"] for a, b in zip(ps, ps[1:]))  # poussée continue à travers les raccords
    assert all(s["zoom_type"] == "doux" and s["zoom"] == 1.1 for s in ps)
    # la pente est la même partout : échelle proportionnelle à la durée gardée
    slopes = [(s["zoom_to"] - s["zoom_from"]) / (s["end"] - s["start"]) for s in ps]
    assert max(slopes) - min(slopes) < 1e-3


def test_broll_on_word_bounded_to_phrase(tmp_path):
    root = setup(tmp_path)
    # Claude place le b-roll « café » un peu tard et trop long (3.9 s → déborderait sur la phrase suivante)
    res = pacing.normalize_edl(edl([seg(0.0, 7.0)], broll=[{"source": "broll/x.mp4", "at": 4.3, "duration": 3.9,
                                                             "source_start": 0.0, "mot": "café"}]), root, {})
    b = res["broll"][0]
    tw = out_words(res, root)
    cafe = next(w for w in tw if w["w"] == "café.")
    nxt = next(w for w in tw if w["w"] == "Le")
    assert cafe["start"] - 2 / FPS - 1e-6 <= b["at"] <= cafe["start"]
    assert b["at"] + b["duration"] <= nxt["start"] + 1e-6  # jamais dans la phrase suivante
    # fin = phrase + 150 ms, allongée vers 1.2 s minimum mais coupée net au début de la phrase suivante
    assert b["at"] + b["duration"] == pytest.approx(min(max(cafe["end"] + pacing.BROLL_TAIL, b["at"] + 1.2), nxt["start"]),
                                                    abs=1e-6)
    assert b["source_start"] == 0.0 and b["mot"] == "café"


def test_broll_clamped_and_hook(tmp_path):
    root = setup(tmp_path)
    long_phrase = [W(f"m{k}", 0.5 + 0.3 * k, 0.75 + 0.3 * k) for k in range(30)] + [W("fin.", 9.5, 9.8)]
    setup(tmp_path, long_phrase, "b")
    res = pacing.normalize_edl(edl([seg(0.0, 10.0, "b")], broll=[{"source": "x.mp4", "at": 1.0, "duration": 9.0}]),
                               root, {"broll": {"duree_moy_s": 2.0}})
    assert res["broll"][0]["duration"] == pytest.approx(3.0)  # 2.0 + 50 %
    hook = pacing.normalize_edl(edl([seg(0.0, 1.6, note="hook"), seg(2.3, 7.0)],
                                    broll=[{"source": "x.mp4", "at": 0.6, "duration": 1.5, "mot": "tout"},
                                           {"source": "y.mp4", "at": 3.0, "duration": 1.5, "mot": "parle"}]), root, {})
    assert [b["source"] for b in hook["broll"]] == ["y.mp4"]
    allowed = pacing.normalize_edl(edl([seg(0.0, 1.6, note="hook")], broll=[{"source": "x.mp4", "at": 0.6,
                                                                             "duration": 1.5}]),
                                   root, {"broll": {"jamais_sur_hook": False}})
    assert len(allowed["broll"]) == 1


def test_titles_sfx_follow_their_words(tmp_path):
    root = setup(tmp_path)
    # dans l'ancien plan, « parle » commence à 3.7 ; un bruitage visé dessus doit rester dessus
    res = pacing.normalize_edl(edl([seg(0.0, 7.0)], sfx=[{"type": "whoosh", "at": 3.7}],
                                   titles=[{"text": "Le café", "at": 5.4, "duration": 2.0}]), root, {})
    tw = {w["w"]: w for w in out_words(res, root)}
    assert res["sfx"][0]["at"] == pytest.approx(tw["parle"]["start"], abs=0.011)
    assert res["titles"][0]["at"] == pytest.approx(tw["Le"]["start"], abs=0.011)
    total = res["pacing"]["duree_apres_s"]
    assert res["titles"][0]["at"] + res["titles"][0]["duration"] <= total + 1e-6


def test_two_segments_remap(tmp_path):
    root = setup(tmp_path)
    plan = edl([seg(2.4, 4.6), seg(0.4, 1.6)], sfx=[{"type": "pop", "at": 2.2 + 0.1}])  # ancien t = 2.3 → « Salut »
    res = pacing.normalize_edl(plan, root, {})
    tw = out_words(res, root)
    assert [w["w"] for w in tw][-4:] == ["Salut", "tout", "le", "monde."]
    assert res["sfx"][0]["at"] == pytest.approx(next(w for w in tw if w["w"] == "Salut")["start"], abs=0.011)


def test_render_consistency_and_idempotent(tmp_path):
    root = setup(tmp_path)
    for fps in (30, 25, 100):
        plan = dict(edl([seg(0.0, 7.0), seg(0.3, 1.55)]), format={"width": 1080, "height": 1920, "fps": fps})
        res = pacing.normalize_edl(plan, root, {})
        for s in res["segments"]:
            frames = (s["end"] - s["start"]) * fps
            assert abs(frames - round(frames)) < 1e-4
        snapped = captions.snapped_segments(res, root)  # ce que render refera : aucun déplacement
        assert [(round(x["start"], 6), round(x["end"], 6)) for x in snapped] == \
               [(round(x["start"], 6), round(x["end"], 6)) for x in res["segments"]]
        assert sum(x["frames"] for x in snapped) / fps == pytest.approx(res["pacing"]["duree_apres_s"], abs=1e-6)
        again = pacing.normalize_edl(dict(res, broll=[]), root, {})
        assert [(x["start"], x["end"]) for x in again["segments"]] == [(x["start"], x["end"]) for x in res["segments"]]


def test_no_transcript_passthrough(tmp_path):
    res = pacing.normalize_edl(edl([seg(1.0, 2.5, "zz")], titles=[{"text": "x", "at": 0.5, "duration": 1}]), tmp_path, {})
    assert [(s["start"], s["end"]) for s in res["segments"]] == [(1.0, 2.5)]
    assert res["titles"][0]["at"] == 0.5


def test_measure_breathing():
    words = []
    t = 0.0
    for k in range(10):  # phrases de 4 mots : 2 mots collés, une pause de 0.2 s, fin de phrase + 0.4 s
        for j, (txt, g) in enumerate([("a", 0.0), ("b", 0.2), ("c", 0.0), ("d.", 0.4)]):
            words.append(W(txt, t, t + 0.2))
            t += 0.2 + g
    m = pacing.measure_breathing({"words": words}, cuts=[w["start"] - 0.07 for w in words[::4]])
    assert m == {"respiration_intra_s": 0.2, "respiration_phrases_s": 0.4, "coupe_avant_mot_s": 0.07}
    assert pacing.measure_breathing({"words": words[:3]}) == pacing.DEFAULTS


# ---------- vrai audio (voix SAPI) ----------

def rms_db(audio, t, sr=16000, win=0.03):
    a = audio[max(0, int((t - win) * sr)):int((t + win) * sr)]
    return 20 * np.log10(np.sqrt(np.mean(a ** 2)) + 1e-9) if len(a) else -120


def test_real_audio_cuts_in_silence(transcribed, tmp_path):
    """Rush SAPI → transcript → normalize_edl → coupes : chaque bord tombe dans du silence, aucun mot amputé,
    pauses de 1.2 s ramenées à la respiration, durée = somme des morceaux (comme render)."""
    root, mp4, _, t = transcribed
    plan = edl([seg(0.0, t["duration"], "rush")])
    plan["format"]["fps"] = 30
    res = pacing.normalize_edl(plan, root, {})
    wav = tmp_path / "a.wav"
    common.run([common.ffmpeg(), "-y", "-v", "error", "-i", str(mp4), "-ac", "1", "-ar", "16000", str(wav)])
    with wave.open(str(wav)) as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    speech = 20 * np.log10(np.sqrt(np.mean(audio[np.abs(audio) > 0.01] ** 2)))
    assert len(res["segments"]) >= 4  # les pauses entre phrases sont devenues des raccords
    for s in res["segments"]:
        for edge in (s["start"], s["end"]):
            if 0.05 < edge < t["duration"] - 0.05:
                assert rms_db(audio, edge, win=0.015) < speech - 25, (edge, s)
    assert res["pacing"]["duree_apres_s"] < res["pacing"]["duree_avant_s"] - 2.0
    # rendu audio réel des morceaux : la durée doit être la somme des morceaux (même grille que render)
    parts = []
    for k, s in enumerate(res["segments"]):
        p = tmp_path / f"p{k}.wav"
        common.run([common.ffmpeg(), "-y", "-v", "error", "-ss", f"{s['start']:.6f}", "-t", f"{s['end'] - s['start']:.6f}",
                    "-i", str(wav), str(p)])
        parts.append(p)
    (tmp_path / "l.txt").write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    out = tmp_path / "out.wav"
    common.run([common.ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(tmp_path / "l.txt"), str(out)])
    assert common.probe(out)["duration"] == pytest.approx(res["pacing"]["duree_apres_s"], abs=0.01 * len(parts))


from test_transcribe import media, transcribed  # noqa: E402,F401  (fixtures partagées, une seule transcription)
