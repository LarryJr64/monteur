"""Fichiers de téléphone réels (rapport du comité robustesse) : étiquettes couleur, HDR, fréquence variable, visage
qui tourne la tête sous un zoom serré."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402
from common import ffmpeg, ffprobe, probe  # noqa: E402

BT709 = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


def ff(*args):
    subprocess.run([ffmpeg(), "-y", "-v", "error", *args], check=True)


def stream_durations(path):
    out = subprocess.run([ffprobe(), "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
                          str(path)], capture_output=True, text=True).stdout
    return {s["codec_type"]: float(s["duration"]) for s in json.loads(out)["streams"]}


def test_synchro_source_etiquetee_bt709_zooms_melanges(tmp_path):
    """Tout téléphone étiquette bt709 : avant, un segment « aucun » (ffmpeg, étiqueté) suivi d'un « punch »
    (image par image, non étiqueté) faisait reconfigurer le concat → images perdues, l'image en avance sur le son."""
    d = tmp_path
    for sub in ("rushs", ".monteur/transcripts", "ma-da"):
        (d / sub).mkdir(parents=True)
    ff("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=10", "-f", "lavfi",
       "-i", "sine=frequency=440:sample_rate=48000:duration=10", "-c:v", "libx264", "-preset", "ultrafast",
       "-pix_fmt", "yuv420p", *BT709, "-c:a", "aac", "-shortest", str(d / "rushs/tel.mp4"))
    words = [{"w": "mot", "start": round(0.2 + 0.4 * i, 2), "end": round(0.5 + 0.4 * i, 2), "p": 0.9} for i in range(23)]
    (d / ".monteur/transcripts/tel.json").write_text(json.dumps(
        {"source": "rushs/tel.mp4", "duration": 10, "language": "fr", "model": "fake", "words": words,
         "silences": []}), encoding="utf-8")
    segs = [("doux", 1.1, 0.1, 3.0), ("aucun", 1.0, 3.0, 6.0), ("punch", 1.15, 6.0, 9.4)]
    edl = {"version": 2, "output": "sorties/s.mp4", "format": {"width": 1080, "height": 1920, "fps": 30},
           "segments": [{"source": "rushs/tel.mp4", "start": a, "end": b, "zoom": z, "zoom_type": k,
                         "zoom_center": [0.5, 0.5]} for k, z, a, b in segs]}
    (d / ".monteur/edl.json").write_text(json.dumps(edl), encoding="utf-8")
    (d / "ma-da/profile.json").write_text(json.dumps({"version": 2, "format": edl["format"]}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPTS / "render.py"), ".monteur/edl.json"], cwd=d,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    dur = stream_durations(d / "sorties/s.mp4")
    assert abs(dur["video"] - dur["audio"]) < 0.1, dur


def test_hdr_iphone_pas_delave(tmp_path):
    """HLG (iPhone par défaut) et PQ : le gris moyen (18 %, BT.2408) doit ressortir vers 0,42 en SDR et le blanc de
    référence (203 nits) près du blanc ; lu tel quel, le HDR sort terne."""
    import measure

    def patch(trc, signal):
        p = tmp_path / f"{trc}_{signal}.mp4"
        y = round(signal * (940 - 64) + 64)
        ff("-f", "lavfi", "-i", f"color=c=black:s=64x64:d=0.2,format=yuv420p10le,geq=lum={y}:cb=512:cr=512,"
           f"setparams=color_primaries=bt2020:color_trc={trc}:colorspace=bt2020nc:range=tv",
           "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-color_primaries", "bt2020", "-color_trc", trc, "-colorspace", "bt2020nc", str(p))
        info = probe(p)
        assert info["hdr"]
        return (measure.decode(p, 64, info=info).mean() - 16) / 219, (measure.decode(p, 64, info={**info, "hdr": False})
                                                                      .mean() - 16) / 219

    for trc, gris, blanc in (("arib-std-b67", 0.38, 0.75), ("smpte2084", 0.38, 0.58)):
        g, g_brut = patch(trc, gris)
        w, _ = patch(trc, blanc)
        assert 0.37 < g < 0.47 and abs(g - 0.42) < abs(g_brut - 0.42), (trc, g, g_brut)
        assert 0.85 < w < 1.0, (trc, w)


def test_cadence_variable_ramenee_a_une_cadence_standard():
    assert common._fps({"avg_frame_rate": "30/1", "r_frame_rate": "30/1"}) == 30
    assert common._fps({"avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001"}) == 29.97
    assert common._fps({"avg_frame_rate": "2340/50", "r_frame_rate": "60/1"}) == 60  # 46,8 de moyenne
    assert common._fps({"avg_frame_rate": "5960/200", "r_frame_rate": "600/1"}) == 29.97  # nominal absurde : moyenne


def test_images_doublees_ne_sont_pas_des_coupes():
    import measure
    rng = np.random.default_rng(0)
    d = 1.0 + 0.3 * rng.random(300)
    for i in range(10, 290, 13):  # image doublée (≈ 0) puis saut de deux images de mouvement
        d[i], d[i + 1] = 0.05, 4.5
    d[150] = 20.0  # la vraie coupe
    assert measure.detect_cuts(d, 30) == [150]


def test_suivi_garde_le_visage_quand_la_tete_tourne(monkeypatch):
    """Tête qui passe de 0,50 à 0,62 en une détection : la zone morte + le lissage laissaient le cadre traîner
    (œil coupé sous un zoom serré) ; le cadre reste à ≤ 8 % de la largeur du visage."""
    import reframe as rf
    det = {k: (0.5 if k < 16 else 0.62, 0.4, 0.5) for k in range(40)}
    monkeypatch.setattr(rf, "raw_detections", lambda *a, **k: (det, {"width": 1920, "height": 1080}))
    tr = rf.face_track("x.mp4", 0.0, 4.9, 30)
    lim = rf.SUIVI_MAX * 0.5 * 1080 / 1920
    late = [t for t in tr if t[0] >= 2.2]  # rattrapé en quelques images
    assert all(abs(t[1] - 0.62) <= lim + 1e-3 for t in late)


def test_zoom_plafonne_quand_le_visage_deborderait():
    """Visage déjà gros + punch : le zoom est borné pour que le visage ne dépasse pas VISAGE_MAX × le cadre."""
    import reframe as rf
    import render
    info = {"width": 1920, "height": 1080}
    track = [(0, 0.5, 0.4, 0.5)] * 5  # visage = 50 % de la hauteur = 540 px ; cadre 9:16 large de 607 px
    seg = {"start": 0, "frames": 5, "zoom": 1.3}
    crops, _, _ = render.plan(seg, info, 1080, 1920, 30, track, "punch", 1.3, 0.5, True,
                              cadrage={"visage_largeur": 1.0, "visage_y": 0.4})
    assert all(540 / cw <= rf.VISAGE_MAX + 1e-6 for _, _, cw, _ in crops), [540 / c[2] for c in crops]


BASE_M = {"duree_moyenne_plan_s": 2, "duree": 60, "plans": [[0, 60]], "silence_max_s": 0, "silences": [], "lufs": -14,
          "format": {}, "zooms_par_minute": 1, "sous_titres_presence": 1}


def test_check_signale_un_visage_coupe(tmp_path):
    import check
    prof = {"cadrage": {"visage_largeur": 1.0, "visage_y": 0.43}, "visage_bord_max": 0.0}
    m = {"cadrage": {"visage_largeur": 1.05, "visage_y": 0.43, "visage_bord": 0.3}}
    m = {**json.loads(json.dumps(BASE_M)), **m}
    assert "visage_coupe" in [e["critere"] for e in check.compare(m, prof)]
    m["cadrage"]["visage_bord"] = 0.1
    assert "visage_coupe" not in [e["critere"] for e in check.compare(m, prof)]
