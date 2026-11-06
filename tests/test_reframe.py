import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from common import ffmpeg  # noqa: E402
import reframe as rf  # noqa: E402

# Interview de Hannah Cloke, The Royal Society, CC BY 3.0 (Wikimedia Commons). Les 8 premiers Mo suffisent :
# 0-31 s : intervieweuse cadrée au centre ; 32-41 s : plan large à deux ; 42-50 s : invitée à droite (x ≈ 0,6).
CLIP_URL = ("https://upload.wikimedia.org/wikipedia/commons/transcoded/9/99/"
            "Interview_on_extreme_weather_with_physical_geographer_Hannah_Cloke_%E2%80%93_The_Royal_Society.webm/"
            "Interview_on_extreme_weather_with_physical_geographer_Hannah_Cloke_%E2%80%93_The_Royal_Society.webm.720p.vp9.webm")
FIXTURES = Path.home() / ".cache" / "monteur" / "tests"  # hors dépôt : jamais commité


def ff(*args):
    subprocess.run([ffmpeg(), "-y", "-v", "error", *args], check=True)


def real_face_clip():
    """visage.mp4 (1280x720, 48 s, vrai visage qui parle) en cache ; None si hors ligne."""
    clip = FIXTURES / "visage.mp4"
    if clip.is_file():
        return clip
    FIXTURES.mkdir(parents=True, exist_ok=True)
    head = FIXTURES / "visage_head.webm"
    try:
        if not head.is_file():
            req = urllib.request.Request(CLIP_URL, headers={"Range": "bytes=0-8000000",
                                                            "User-Agent": "monteur-tests/1.0 (plugin Claude Code)"})
            head.write_bytes(urllib.request.urlopen(req, timeout=60).read())
        # fichier tronqué : ffmpeg décode jusqu'où il peut (≈ 50 s)
        subprocess.run([ffmpeg(), "-y", "-v", "error", "-i", str(head), "-t", "48", "-c:v", "libx264", "-preset",
                        "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-r", "25", str(clip)])
    except OSError:
        return None
    return clip if clip.is_file() and clip.stat().st_size > 100_000 else None


@pytest.fixture(scope="module")
def visage():
    clip = real_face_clip()
    if clip is None:
        pytest.skip("hors ligne : pas de vidéo de visage réelle")
    return clip


def test_suivi_visage_reel(visage, tmp_path):
    centre = rf.face_track(visage, 2.0, 12.0, 30, cache_dir=tmp_path)
    droite = rf.face_track(visage, 43.0, 47.0, 30, cache_dir=tmp_path)
    assert len(centre) == 300 and len(droite) == 120
    cx = np.array([t[1] for t in centre])
    assert 0.44 < cx.mean() < 0.56 and 0.2 < np.mean([t[3] for t in centre]) < 0.45
    assert 0.55 < np.mean([t[1] for t in droite]) < 0.68  # l'invitée est à droite : le cadre la suit
    assert np.abs(np.diff(cx)).max() < 0.004  # lissé + zone morte : aucune trépidation
    assert list((tmp_path / "visages").glob("visage_*.json"))  # détections en cache
    again = rf.face_track(visage, 2.0, 12.0, 30, cache_dir=tmp_path)
    assert again == centre


def test_suivi_sans_visage_repli(tmp_path):
    src = tmp_path / "mire.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=2", "-pix_fmt", "yuv420p", str(src))
    tr = rf.face_track(src, 0.5, 1.5, 30, cache_dir=tmp_path)
    assert len(tr) == 30 and all(t[1:] == rf.FALLBACK for t in tr)


def test_courbes_de_zoom():
    punch = rf.zoom_curve("punch", 1.12, 40, prev=1.0, fps=30, rampe_s=0.5)
    assert punch[0] == 1.0 and abs(punch[15] - 1.12) < 1e-9 and punch[-1] == 1.12
    assert all(b >= a for a, b in zip(punch, punch[1:]))
    assert abs(punch[7] + punch[8] - 2.12) < 0.002  # aisée et symétrique autour de la mi-rampe
    assert punch[1] - punch[0] < 0.002  # départ en douceur
    crash = rf.zoom_curve("crash", 1.5, 20)
    assert crash[5] == 1.35 and crash[4] < 1.35  # 5 images, plafonné à 1.35
    assert rf.zoom_curve("crash", None, 10)[-1] == 1.3
    doux = rf.zoom_curve("doux", 1.1, 61)
    assert doux[0] == 1.0 and abs(doux[-1] - 1.1) < 1e-9 and abs(doux[30] - 1.05) < 1e-6
    assert rf.zoom_curve("punch", 1.12, 30, prev=1.05)[0] == 1.05  # part de l'échelle précédente
    assert rf.zoom_curve(None, 1.2, 3) == [1.2] * 3 and rf.zoom_curve("aucun", 1.2, 2) == [1.0, 1.0]


def test_cadre_visage_tiers_superieur():
    x, y, cw, ch = rf.crop_box(1920, 1080, 1080, 1920, 1.2, (0.6, 0.45), face=True)
    assert abs((0.6 * 1920 - x) / cw - 0.5) < 1e-9
    assert abs((0.45 * 1080 - y) / ch - rf.FACE_Y) < 1e-9
    x, y, cw, ch = rf.crop_box(1920, 1080, 1080, 1920, 1.0, (0.99, 0.5))
    assert x + cw == 1920  # jamais hors de l'image


def test_couleur_mesure_et_correction(tmp_path):
    src = tmp_path / "terne.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=2", "-vf", "eq=saturation=0.6:brightness=-0.08",
       "-pix_fmt", "yuv420p", str(src))
    m = rf.measure_colour(src)
    assert set(m) == {"luma", "contraste", "saturation", "temperature"}
    target = {"luma": m["luma"] + 0.06, "contraste": m["contraste"], "saturation": m["saturation"] * 1.25,
              "temperature": m["temperature"] + 0.03}
    vf = rf.colour_filter(m, target)
    assert "eq=" in vf and "colorchannelmixer" in vf
    fixed = tmp_path / "corrige.mp4"
    ff("-i", str(src), "-vf", vf, "-pix_fmt", "yuv420p", str(fixed))
    m2 = rf.measure_colour(fixed)
    for k in ("luma", "saturation", "temperature"):
        assert abs(m2[k] - target[k]) < abs(m[k] - target[k]) * 0.5, (k, m, m2, target)
    assert rf.colour_filter(m, m) == ""  # déjà conforme : rien


def test_couleur_sans_piste_video(tmp_path):
    """Média son seul : pas de plantage, mesure None et aucun filtre (render laisse le média tel quel)."""
    src = tmp_path / "son.m4a"
    ff("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(src))
    assert rf.measure_colour(src) is None
    assert rf.colour_filter(None, {"luma": 0.5}) == ""
