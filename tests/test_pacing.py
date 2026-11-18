import json
import math
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


def test_coupe_elargie_sur_l_energie_jamais_une_attaque_mangee(tmp_path):
    """La transcription date « tout » 80 ms trop tard et « monde » 80 ms trop tôt (grille Parakeet) : l'énergie du son
    (10 ms) élargit les coupes pour garder l'attaque et la fin réelles ; sans son, comportement inchangé."""
    sr = 16000
    x = np.zeros(int(sr * 4), np.float32)
    rng = np.random.default_rng(0)
    x += rng.normal(0, 0.002, len(x)).astype(np.float32)  # bruit de pièce
    for a, b in ((1.00, 1.60), (2.40, 3.10)):  # voix réelle : 1,00-1,60 puis 2,40-3,10
        t = np.arange(int((b - a) * sr)) / sr
        x[int(a * sr):int(b * sr)] += 0.3 * np.sin(2 * np.pi * 220 * t)
    (tmp_path / "rushs").mkdir(parents=True)
    with wave.open(str(tmp_path / "rushs" / "e.mp4"), "wb") as w:  # wav sous un nom de rush : ffmpeg lit le contenu
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)  # noqa: E702
        w.writeframes((x * 32767).astype(np.int16).tobytes())
    words = [W("tout", 1.08, 1.60), W("monde.", 2.40, 3.02)]  # début de « tout » tardif, fin de « monde » précoce
    setup(tmp_path, words, "e")
    prof = {"rythme": {"respiration_intra_s": 0.2, "respiration_phrases_s": 0.3, "coupe_avant_mot_s": 0.0}}
    res = pacing.normalize_edl(edl([seg(1.08, 3.02, "e")]), tmp_path, prof)
    pieces = res["segments"]
    assert pieces[0]["start"] <= 1.00 + 1e-6  # l'attaque réelle (1,00) est gardée
    assert pieces[-1]["end"] >= 3.10 - 1e-6  # la fin réelle (3,10) aussi
    assert pieces[0]["start"] >= 0.75  # mais on ne remonte pas dans le silence


def test_broll_plan_et_carton(tmp_path):
    """Face caméra : `plan` (plans.json) donne source + départ et borne la durée au plan ; `carton` couvre le hook."""
    root = setup(tmp_path)
    (root / ".monteur" / "plans.json").write_text(json.dumps(
        {"plans": {"P2": {"source": "broll/club.mp4", "start": 10.0, "end": 10.9}}}), encoding="utf-8")
    res = pacing.normalize_edl(edl([seg(0.0, 1.6, note="hook"), seg(2.3, 7.0)],
                                   broll=[{"plan": "p2", "at": 3.0, "duration": 2.0, "mot": "parle"},
                                          {"source": "broll/anim_q.mp4", "at": 0.0, "duration": 1.5, "carton": True},
                                          {"plan": "P9", "at": 4.0, "duration": 1.0}]), root, {})
    srcs = {b["source"]: b for b in res["broll"]}
    assert set(srcs) == {"broll/club.mp4", "broll/anim_q.mp4"}  # P9 inconnu : ignoré
    assert srcs["broll/club.mp4"]["source_start"] == 10.0 and srcs["broll/club.mp4"]["duration"] <= 0.9 + 1e-6
    assert srcs["broll/anim_q.mp4"]["at"] == 0.0


def test_insert_a_lire_juste_avant_le_cta_garde_1_2_s(tmp_path):
    """Logo sur la dernière phrase avant le CTA : gardé 1,2 s (mord le début du CTA) au lieu d'être retiré."""
    root = setup(tmp_path)
    res = pacing.normalize_edl(edl([seg(0.0, 4.6), seg(5.3, 7.0, note="cta")],
                                   broll=[{"source": "broll/anim_logo.mp4", "at": 3.9, "duration": 1.5, "mot": "café",
                                           "tenir": True, "texte": True}]), root, {})
    assert len(res["broll"]) == 1 and res["broll"][0]["duration"] == pytest.approx(pacing.TENIR_MIN)


def test_raccord_entre_segments_garde_la_respiration_du_createur(tmp_path):
    # « parle du café. » puis « Salut tout le monde. » : au raccord, silence = respiration entre phrases, pas plus
    root = setup(tmp_path)
    profil = {"rythme": {"respiration_phrases_s": 0.12, "coupe_avant_mot_s": 0.1}}
    res = pacing.normalize_edl(edl([seg(3.6, 4.6), seg(0.4, 1.6)]), root, profil)
    tw = out_words(res, root)
    cafe, salut = next(w for w in tw if w["w"] == "café."), next(w for w in tw if w["w"] == "Salut")
    assert salut["start"] - cafe["end"] == pytest.approx(0.12, abs=0.015)  # respiration 0,12 partagée traîne + élan


def fake_env(loud, total=4.0):
    """Envelope sans fichier : -20 dB sur les plages `loud`, -70 dB ailleurs (tranches de 10 ms)."""
    env = pacing.Envelope.__new__(pacing.Envelope)
    t = np.arange(int(total / pacing.Envelope.STEP)) * pacing.Envelope.STEP
    env.db = np.where(np.any([(t >= a) & (t < b) for a, b in loud], axis=0), -20.0, -70.0)
    env.np = np
    return env


def test_vrai_silence_dans_un_mot_coupe_en_deux():
    # Parakeet : « réussite » 1,0-2,0 collé à « ensuite », mais la voix se tait 1,4-1,8 (pause rangée dans le mot)
    words = [W("réussite", 1.0, 2.0), W("ensuite", 2.0, 2.4)]
    env = fake_env([(1.0, 1.4), (1.8, 2.4)])
    pieces, _ = pacing._cut_plan(seg(0.9, 2.5), words, pacing._rythme({}), True, env)
    assert len(pieces) == 2
    (a0, b0, w0, _), (a1, b1, w1, _) = pieces
    assert [w["w"] for w in w0] == ["réussite"] and [w["w"] for w in w1] == ["ensuite"]
    assert 1.4 + pacing.TAIL_MIN <= b0 < a1 <= 1.8 - pacing.PRE_MIN  # coupe dans le silence, respiration gardée
    assert a0 <= 1.0 and b1 >= 2.4  # aucune attaque ni fin mangée
    assert env.silences(1.05, 2.35) == [pytest.approx((1.4, 1.8))]


def test_jump_cut_au_vrai_silence_meme_echelle(tmp_path):
    words = [W("a", 0.0, 0.5), W("b", 0.5, 1.0), W("c", 1.0, 1.5), W("d", 1.5, 2.0)]
    env = fake_env([(0.0, 0.95), (1.05, 2.0)])  # 100 ms de vrai silence entre « b » et « c »
    r = {"plan_max_s": 1.0, "respiration_intra_s": 0.08}
    out, jumps = pacing._jump_cuts([(0.0, 2.0, words, math.inf)], r, 30, env)
    assert [(round(a, 2), round(b, 2), [w["w"] for w in pw]) for a, b, pw, _ in out] == \
        [(0.0, 0.95, ["a", "b"]), (1.05, 2.0, ["c", "d"])] and jumps == {1}
    assert out[1][0] - out[0][1] >= pacing.JUMP_FRAMES / 30 - 1e-9  # ≥ 3 images retirées : le saut se voit
    # sans son : plus grande frontière de mots ; le morceau après le jump garde l'échelle (pas de zoom ajouté)
    root = setup(tmp_path)
    res = pacing.normalize_edl(edl([seg(0.0, 7.0, zoom=1.2)]), root, {"rythme": {"plan_max_s": 1.0}})
    ps = res["segments"]
    assert all(s["end"] - s["start"] <= 1.0 + 1e-6 for s in ps if s["end"] - s["start"] > 0)
    assert ps[0]["zoom"] == 1.2 and ps[1]["zoom"] == 1.2 and ps[1]["zoom_type"] == "cut"
    assert pacing.normalize_edl(edl([seg(0.0, 7.0)]), root, {})["pacing"]["morceaux"] == 4  # sans profil : aucun jump


def test_broll_sur_le_premier_mot_apres_le_hook_garde(tmp_path):
    """Avancé de 2 images, il chevauchait le hook et était retiré : il part à la fin du hook."""
    root = setup(tmp_path)
    prof = {"rythme": {"coupe_avant_mot_s": 0.0, "respiration_phrases_s": 0.0}}
    res = pacing.normalize_edl(edl([seg(0.0, 1.6, note="hook"), seg(2.3, 7.0)],
                                   broll=[{"source": "y.mp4", "at": 1.3, "duration": 1.0, "mot": "Aujourd'hui"}]),
                               root, prof)
    hook_fin = res["segments"][0]["end"] - res["segments"][0]["start"]
    assert len(res["broll"]) == 1 and res["broll"][0]["at"] == pytest.approx(hook_fin, abs=1e-3)


def test_plans_enchaines_sans_mot(tmp_path):
    """Plan sans mot collé au précédent : le suit, garde sa durée courte, jamais plus long que le plan."""
    root = setup(tmp_path)
    (root / ".monteur" / "plans.json").write_text(json.dumps(  # P1 court : P2 tient encore dans la phrase
        {"plans": {"P1": {"source": "b/a.mp4", "start": 0.0, "end": 0.5},
                   "P2": {"source": "b/b.mp4", "start": 3.0, "end": 3.5}}}), encoding="utf-8")
    res = pacing.normalize_edl(edl([seg(0.0, 7.0)], broll=[{"plan": "P1", "at": 3.6, "duration": 0.8, "mot": "parle"},
                                                           {"plan": "P2", "at": 4.4, "duration": 0.6}]),
                               root, {"broll": {"duree_moy_s": 2.2}})
    a, b = res["broll"]
    assert b["at"] == pytest.approx(a["at"] + a["duration"], abs=1e-3)
    assert b["duration"] == pytest.approx(0.5, abs=1e-3)  # 0,6 demandé (pas forcé à 1,1), borné au plan (0,5)
    assert not any(k.startswith("_") for x in res["broll"] for k in x)


def test_plan_enchaine_jamais_au_dela_de_sa_phrase(tmp_path):
    """Un plan enchaîné s'arrête 0,3 s après le dernier mot de la phrase du plan qu'il suit (pas sur la phrase
    suivante) ; `deborde` le laisse filer (même sujet) ; ce qui reste sous 0,4 s est retiré."""
    root = setup(tmp_path)
    (root / ".monteur" / "plans.json").write_text(json.dumps(
        {"plans": {"P1": {"source": "b/a.mp4", "start": 0.0, "end": 5.0},
                   "P2": {"source": "b/b.mp4", "start": 0.0, "end": 5.0}}}), encoding="utf-8")

    def rendu(moy=1.0, **k):
        return pacing.normalize_edl(edl([seg(0.0, 7.0)], broll=[
            {"plan": "P1", "at": 2.4, "duration": 1.0, "mot": "Aujourd'hui"},
            {"plan": "P2", "at": 3.4, "duration": 1.5, **k}]), root, {"broll": {"duree_moy_s": moy}})
    res = rendu()
    fin = next(w for w in out_words(res, root) if w["w"] == "Le")["start"]  # début de la phrase suivante
    a, b = res["broll"]
    assert b["at"] == pytest.approx(a["at"] + a["duration"], abs=1e-3)
    assert b["at"] + b["duration"] == pytest.approx(fin, abs=1e-3)  # 1,5 s demandées, coupé à sa phrase
    assert rendu(deborde=True)["broll"][1]["duration"] == pytest.approx(1.5, abs=1e-3)
    assert len(rendu(moy=2.2)["broll"]) == 1  # le 1er couvre déjà la phrase : le suivant n'a plus la place


def test_coupe_sans_retrait_et_echelle_alternee(tmp_path):
    words = [W("a", 1.5, 2.0), W("b", 2.0, 2.5), W("c", 2.5, 3.0), W("d", 3.0, 3.5)]
    env = fake_env([(1.5, 3.5)], total=6.0)  # parole continue : aucune coupe sans retirer de la voix
    r = {"plan_max_s": 1.2, "respiration_intra_s": 0.08}
    assert len(pacing._jump_cuts([(1.5, 3.5, words, math.inf)], r, 30, env)[0]) == 1  # figé
    out, jumps = pacing._jump_cuts([(1.5, 3.5, words, math.inf)], r, 30, env, sans_retrait=True)
    assert [(a, b) for a, b, _, _ in out] == [(1.5, 2.5), (2.5, 3.5)] and jumps == {1}  # rien retiré
    # profil qui change d'échelle à chaque coupe : 1 ↔ intensité, coupe sèche (« raccord », pas un zoom animé)
    root = setup(tmp_path)
    prof = {"rythme": {"plan_max_s": 1.0}, "zooms": {"coupes_echelle": True, "intensite": 1.2, "fin": True}}
    ps = pacing.normalize_edl(edl([seg(0.0, 7.0)]), root, prof)["segments"]
    zs = [s.get("zoom", 1.0) for s in ps]
    assert all(z1 != z2 for z1, z2 in zip(zs, zs[1:])) and set(zs) == {1.0, 1.2}
    assert {s.get("zoom_type") for s in ps if s.get("zoom", 1.0) > 1} == {"raccord"}
    assert ps[-1]["zoom"] == 1.0 and ps[-2]["zoom"] == 1.2  # fin sur le plan large
    sec = pacing.normalize_edl(edl([seg(0.0, 7.0)], masquer_raccords=False), root, prof)["segments"]
    assert all(s.get("zoom_type") != "raccord" for s in sec[:-2])  # l'edl tranche : coupes sèches même échelle


def test_premier_mot_colle_au_mot_d_avant_commence_au_creux():
    # « oui, » 1,0-1,5 collé à « dans » 1,5-1,7 dans la transcription, mais la voix de « dans » part à 1,44
    words = [W("oui,", 1.0, 1.5), W("dans", 1.5, 1.7), W("quinze", 1.7, 2.0)]
    env = fake_env([(1.0, 1.40), (1.44, 2.0)])
    (a, *_), *_ = pacing._cut_plan(seg(1.5, 2.1), words, pacing._rythme({}), True, env)[0]
    assert 1.40 <= a <= 1.44  # « dans » entier, rien de « oui »
    suite = seg(0.9, 1.5)  # « oui » gardé par le segment d'avant : on ne le reprend pas
    (a, *_), *_ = pacing._cut_plan(seg(1.5, 2.1), words, pacing._rythme({}), True, env, suite)[0]
    assert a >= 1.47
