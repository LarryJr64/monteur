import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from common import ffmpeg, probe  # noqa: E402

PHRASE = ("salut tout le monde euh aujourd'hui on parle de trois erreurs. "
          "la première c' est de couper trop tard ! la deuxième ? bah oublier le son").split()


def ff(*args):
    subprocess.run([ffmpeg(), "-y", "-v", "error", *args], check=True)


def fake_words(duration):
    """Mots de 0,3 s espacés de 0,1 s, avec une pause de 0,6 s toutes les 8 paroles."""
    words, t, i = [], 0.3, 0
    while t + 0.3 < duration:
        words.append({"w": PHRASE[i % len(PHRASE)], "start": round(t, 3), "end": round(t + 0.3, 3), "p": 0.9})
        t += 0.4 + (0.6 if i % 8 == 7 else 0)
        i += 1
    return words


def make_workdir(d, with_media=True):
    d = Path(d)
    for sub in ("rushs", "broll", "musique", ".monteur/transcripts", "ma-da"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    rushs = {"paysage": (1920, 1080, 12, 440), "vertical": (1080, 1920, 8, 660), "carre": (720, 720, 6, 0)}
    for stem, (W, H, dur, hz) in rushs.items():
        if with_media:
            audio = ["-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate=44100:duration={dur}"] if hz else []
            ff("-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=25:duration={dur}", *audio,
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-shortest", str(d / "rushs" / f"{stem}.mp4"))
        tr = {"source": f"rushs/{stem}.mp4", "duration": dur, "language": "fr", "model": "fake",
              "words": fake_words(dur), "silences": []}
        (d / ".monteur/transcripts" / f"{stem}.json").write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    if with_media:
        ff("-f", "lavfi", "-i", "testsrc=size=640x480:rate=30:duration=3", "-c:v", "libx264",
           "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(d / "broll/plan.mp4"))
        ff("-f", "lavfi", "-i", "sine=frequency=220:duration=2", "-c:a", "libmp3lame", str(d / "musique/m.mp3"))
    edl = {"version": 1, "output": "sorties/test.mp4", "format": {"width": 1080, "height": 1920, "fps": 30},
           "segments": [
               {"source": "rushs/paysage.mp4", "start": 0.3, "end": 2.05, "zoom": 1.0, "zoom_center": [0.5, 0.4], "note": "hook"},
               {"source": "rushs/vertical.mp4", "start": 1.0, "end": 2.6, "zoom": 1.15, "zoom_center": [0.5, 0.4]},
               {"source": "rushs/paysage.mp4", "start": 5.0, "end": 6.4, "zoom": 1.3, "zoom_center": [0.7, 0.5]},
               {"source": "rushs/carre.mp4", "start": 0.5, "end": 1.9, "zoom": 1.0, "zoom_center": [0.5, 0.5]},
               {"source": "rushs/vertical.mp4", "start": 4.0, "end": 5.5, "zoom": 1.1, "zoom_center": [0.3, 0.3]},
               {"source": "rushs/paysage.mp4", "start": 9.0, "end": 10.3, "zoom": 1.0, "zoom_center": [0.5, 0.5]}],
           "captions": True,
           "broll": [{"source": "broll/plan.mp4", "at": 2.5, "duration": 1.2, "source_start": 0.5}],
           "music": {"source": "musique/m.mp3", "volume_db": -18},
           "titles": [{"text": "3 erreurs à éviter", "at": 0.0, "duration": 1.5}]}
    (d / ".monteur/edl.json").write_text(json.dumps(edl, ensure_ascii=False), encoding="utf-8")
    prof = {"version": 1, "format": edl["format"], "zooms": {"style": "doux"}, "audio": {"lufs_cible": -14},
            "sous_titres": {"animation": "karaoke", "mots_par_bloc": 3}}
    (d / "ma-da/profile.json").write_text(json.dumps(prof), encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    d = make_workdir(tmp_path_factory.mktemp("work"))
    r = subprocess.run([sys.executable, str(SCRIPTS / "render.py"), ".monteur/edl.json"], cwd=d,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    print(r.stdout)
    return d, d / "sorties/test.mp4", r.stdout


def lufs(path):
    out = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8").stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", out)[-1])


def test_render_format_duree_audio(rendered):
    d, out, stdout = rendered
    import captions
    edl = json.loads((d / ".monteur/edl.json").read_text(encoding="utf-8"))
    expected = sum(s["end"] - s["start"] for s in edl["segments"])
    info = probe(out)
    assert info["width"] == 1080 and info["height"] == 1920 and info["fps"] == 30
    assert info["has_audio"]
    # tolérance : recalage sur les mots (≤ demi-mot) + 1 image par segment ; avec pacing.py, edl.rendu.json fait foi
    rendu = json.loads((d / ".monteur/edl.rendu.json").read_text(encoding="utf-8"))
    snapped = sum(s["frames"] for s in captions.snapped_segments(rendu, d)) / 30
    assert abs(info["duration"] - snapped) <= len(edl["segments"]) / 30
    assert abs(snapped - expected) < 0.5
    assert abs(lufs(out) - (-14)) <= 1
    assert "temps réel" in stdout


def test_frame_avec_sous_titres(rendered):
    d, out, _ = rendered
    png = d / "sorties/frame.png"
    ff("-ss", "1.0", "-i", str(out), "-frames:v", "1", str(png))
    assert png.stat().st_size > 10000


def test_brouillon(rendered):
    d, _, _ = rendered
    r = subprocess.run([sys.executable, str(SCRIPTS / "render.py"), ".monteur/edl.json", "--brouillon"], cwd=d,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    info = probe(d / "sorties/test_brouillon.mp4")
    assert (info["width"], info["height"]) == (540, 960)


def test_reframe_paysage_cover():
    import render
    vf = render.reframe(1920, 1080, 1080, 1920)
    assert vf.startswith("crop=608:1080:656:0,scale=1080:1920")  # cover 9:16 centré, crop puis scale
    assert render.reframe(1080, 1920, 1080, 1920, zoom=1.2).startswith("crop=900:1600:90:160")


def test_snap_ne_coupe_pas_un_mot():
    import captions
    words = [{"w": "bonjour", "start": 1.0, "end": 1.6}]
    assert captions.snap(1.2, words) == 1.0
    assert captions.snap(1.5, words) == 1.6
    assert captions.snap(1.05, words) == 1.05  # tolérance 80 ms
    assert captions.snap(0.8, words) == 0.8
    collés = [{"w": "et", "start": 76.62, "end": 76.92}, {"w": "dis-moi", "start": 76.92, "end": 77.3}]
    assert captions.snap(76.87, collés) == 76.92  # pas de bout de « et » avant « dis-moi »


# ---------- v2 : visage, zooms doux/punch/crash, mots-clés, emojis, b-roll, couleur auto ----------

def make_workdir_v2(d):
    """6 segments avec toutes les options v2, sur un vrai visage si dispo (sinon la mire paysage)."""
    from test_reframe import real_face_clip
    d = make_workdir(d)
    clip = real_face_clip()
    src = "rushs/visage.mp4" if clip else "rushs/paysage.mp4"
    if clip:
        import shutil
        shutil.copy(clip, d / src)
        tr = {"source": src, "duration": 48, "language": "fr", "model": "fake", "words": fake_words(48), "silences": []}
        (d / ".monteur/transcripts/visage.json").write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    words = json.loads((d / f".monteur/transcripts/{Path(src).stem}.json").read_text(encoding="utf-8"))["words"]
    far = (43.0, 45.0) if clip else (10.0, 11.6)  # visage à droite dans le vrai clip
    spans = [((1.0, 3.0), "aucun", 1.0, None), ((4.0, 6.0), "doux", 1.1, "🔥"), ((8.0, 9.6), "punch", 1.15, None),
             ((12.0, 13.4) if clip else (6.6, 8.0), "crash", 1.3, None), (far, "punch", 1.12, "💡"),
             ((16.0, 18.0) if clip else (2.0, 4.0), "doux", 1.08, None)]
    segs = []
    for (a, b), kind, z, emo in spans:
        inside = [w["w"] for w in words if a + 0.2 < w["start"] and w["end"] < b - 0.2 and w["w"] not in ("euh", "bah")]
        seg = {"source": src, "start": a, "end": b, "zoom": z, "zoom_type": kind, "zoom_center": "visage",
               "mots_cles": inside[1:2]}
        if emo:
            seg["emoji"] = emo
        segs.append(seg)
    segs[0]["note"] = "hook"
    edl = {"version": 2, "output": "sorties/v2.mp4", "format": {"width": 1080, "height": 1920, "fps": 30},
           "segments": segs, "captions": True,
           "broll": [{"source": "broll/plan.mp4", "at": 6.3, "duration": 1.0, "source_start": 0.5}],
           "sfx": [{"type": "impact", "at": 5.2, "volume_db": -14}],
           "music": {"source": "musique/m.mp3", "volume_db": -20},
           "audio": {"nettoyage_voix": "auto"}, "couleur": "auto"}
    (d / ".monteur/edl_v2.json").write_text(json.dumps(edl, ensure_ascii=False), encoding="utf-8")
    prof = {"version": 2, "format": edl["format"], "zooms": {"rampe_s": 0.5}, "audio": {"lufs_cible": -14},
            "sous_titres": {"animation": "pop", "mots_par_bloc": 3, "zone_sure": True, "couleur_mot_cle": "#39FF14",
                            "position_y_pct": 0.70},
            "couleur": {"luma": 0.30, "contraste": 0.19, "saturation": 0.17, "temperature": 0.07},
            "broll": {"transition": "fondu"}}
    (d / "ma-da/profile_v2.json").write_text(json.dumps(prof), encoding="utf-8")
    return d, bool(clip)


@pytest.fixture(scope="module")
def rendered_v2(tmp_path_factory):
    d, real = make_workdir_v2(tmp_path_factory.mktemp("v2"))
    r = subprocess.run([sys.executable, str(SCRIPTS / "render.py"), ".monteur/edl_v2.json", "--profil",
                        "ma-da/profile_v2.json"], cwd=d, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    print(r.stdout)
    return d, d / "sorties/v2.mp4", r.stdout, real


def frame_rgb(path, t, w=1080, h=1920):
    import numpy as np
    raw = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)


def _face_box(img):
    """(haut, bas) du visage détecté par YuNet dans une image de sortie, ou None."""
    import cv2
    import reframe as rf
    det = cv2.FaceDetectorYN.create(str(rf.model_path()), "", (540, 960), 0.6)
    _, faces = det.detect(cv2.resize(img[..., ::-1].copy(), (540, 960)))
    if faces is None or not len(faces):
        return None
    f = max(faces, key=lambda f: f[2] * f[3])
    return f[1] * 2, (f[1] + f[3]) * 2


def test_v2_toutes_options(rendered_v2):
    import numpy as np
    import captions
    d, out, stdout, real = rendered_v2
    info = probe(out)
    assert (info["width"], info["height"], info["fps"]) == (1080, 1920, 30) and info["has_audio"]
    rendu = json.loads((d / ".monteur/edl.rendu.json").read_text(encoding="utf-8"))
    segs = captions.snapped_segments(rendu, d)
    assert abs(info["duration"] - sum(s["frames"] for s in segs) / 30) <= 2 / 30
    assert abs(lufs(out) - (-14)) <= 1
    for étape in ("analyse", "segments", "finition", "son", "mux"):
        assert étape in stdout
    assert "emojis)" in stdout and " 0 emojis" not in stdout
    words = captions.timeline_words(segs, d)
    kw = [w for w in words if w.get("cle")]
    assert kw
    seen = 0
    for i, w in enumerate(kw):  # images de contrôle (aussi regardées à l'œil pendant le développement)
        t = w["start"] + 0.3
        if any(b["at"] <= t <= b["at"] + b["duration"] for b in rendu["broll"]):
            continue
        img = frame_rgb(out, t)
        ff("-ss", f"{t:.3f}", "-i", str(out), "-frames:v", "1", str(d / f"sorties/kw{i}.png"))
        r, g, b = (img[..., k].astype(int) for k in range(3))
        rows = np.nonzero((((r < 130) & (g > 210) & (b < 130)).sum(1)) > 5)[0]  # vert #39FF14 du mot-clé
        if len(rows):
            seen += 1
            assert rows.min() > 0.45 * 1920 and rows.max() < 0.80 * 1920  # zone sûre
            face = _face_box(img) if real else None
            if face:  # jamais sur le visage
                assert rows.min() > face[1] or rows.max() < face[0], (t, face, rows.min(), rows.max())
    assert seen >= 2
    b = rendu["broll"][0]
    ff("-ss", f"{b['at'] + b['duration'] / 2:.3f}", "-i", str(out), "-frames:v", "1", str(d / "sorties/broll.png"))


def test_zoom_sans_saccade(tmp_path):
    """Poussée lente sur une image fixe : la position d'un point suit une courbe lisse (zoompan arrondissait au pixel)."""
    import numpy as np
    from PIL import Image
    import render
    import reframe as rf
    img = np.zeros((1080, 1920, 3), np.uint8)
    img[290:310, 1090:1110] = 255
    Image.fromarray(img).save(tmp_path / "point.png")
    seg = {"start": 0, "frames": 60}
    crops = [rf.crop_box(1920, 1080, 1080, 1920, z, (0.5, 0.5)) for z in rf.zoom_curve("doux", 1.3, 60)]
    render.render_segment(seg, tmp_path / "point.png", tmp_path / "z.mkv", 1080, 1920, 30, crops, "", False,
                          with_audio=False, image=True)
    raw = subprocess.run([ffmpeg(), "-v", "error", "-i", str(tmp_path / "z.mkv"), "-pix_fmt", "gray", "-f", "rawvideo",
                          "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 1920, 1080).astype(float)
    assert len(frames) == 60
    xs = []
    for f in frames:
        f = np.where(f > 60, f, 0)
        xs.append((f.sum(0) * np.arange(1080)).sum() / f.sum())
    dx = np.diff(xs)
    assert (dx > -0.05).all()  # s'éloigne du centre sans jamais revenir en arrière
    assert np.abs(np.diff(dx)).max() < 0.5  # lisse (bruit x264 ≈ 0,3 px) : un arrondi au pixel donnerait des marches de 1 px


def test_doux_continu_a_travers_les_morceaux(tmp_path):
    """Segment « doux » découpé par pacing (silence interne retiré) : une seule poussée continue, sans repartir
    de 1.0 à chaque morceau ; render lit zoom_from/zoom_to et regroupe par `seg`."""
    import pacing
    import render
    tdir = tmp_path / ".monteur" / "transcripts"
    tdir.mkdir(parents=True)
    words = [{"w": "un", "start": 0.0, "end": 0.4}, {"w": "deux", "start": 0.5, "end": 0.9},
             {"w": "trois", "start": 2.5, "end": 2.9}, {"w": "quatre.", "start": 3.0, "end": 3.4}]
    (tdir / "a.json").write_text(json.dumps({"source": "rushs/a.mp4", "words": words}), encoding="utf-8")
    edl = {"version": 2, "format": {"width": 1080, "height": 1920, "fps": 30},
           "segments": [{"source": "rushs/a.mp4", "start": 0.0, "end": 3.5, "zoom": 1.2, "zoom_type": "doux"}]}
    res = pacing.normalize_edl(edl, tmp_path, {})
    segs = [dict(s, frames=round((s["end"] - s["start"]) * 30)) for s in res["segments"]]
    assert len(segs) == 2 and "zoom_from" in segs[1]
    info = {"width": 1920, "height": 1080}
    for style in ("cut", "doux"):  # zoom_from/zoom_to (pacing) ou, sans eux, regroupement par seg
        ss = [dict(s) for s in segs] if style == "cut" else \
            [{k: v for k, v in s.items() if k not in ("zoom_from", "zoom_to", "zoom_type")} for s in segs]
        render.rf.doux_u(ss, style)
        scales = []
        for s in ss:
            crops, _, _ = render.plan(s, info, 1080, 1920, 30, None, "doux", 1.0, 0.5, False)
            scales += [1080 / c[3] for c in crops]  # échelle = hauteur source / hauteur recadrée
        steps = [b - a for a, b in zip(scales, scales[1:])]
        assert min(steps) >= -1e-9, "la poussée ne revient jamais en arrière"
        assert max(steps) < 0.01, "pas de saut au raccord entre morceaux"
        assert abs(scales[0] - 1.0) < 1e-6 and abs(scales[-1] - 1.2) < 0.01


def test_bandes_noires_jamais_dans_le_cadre(tmp_path):
    """Une source avec une bande noire en haut (clip téléchargé, letterbox) : détectée, et aucun cadre
    (zoom 1.0 compris, le plus grand) ne la touche."""
    import reframe as rf
    import render
    src = tmp_path / "bande.mp4"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1042:r=30:d=2",
                    "-vf", "pad=1920:1080:0:38:black", "-c:v", "libx264", "-preset", "ultrafast", str(src)], check=True)
    info = probe(src)
    info["bandes"] = rf.active_area(src, info)
    assert info["bandes"][1] in range(36, 41) and info["bandes"][3] == 0, info["bandes"]
    for z in (1.0, 1.15):
        seg = {"start": 0, "frames": 3, "zoom": z}
        crops, _, _ = render.plan(seg, info, 1080, 1920, 30, [(0, 0.5, 0.1, 0.2)] * 3, None, 1.0, 0.5, True)
        assert all(y >= info["bandes"][1] and y + ch <= 1080 + 0.5 for _, y, _, ch in crops), (z, crops[0])


def test_transition_en_sortie_et_segment_muet(tmp_path):
    import render
    brolls = [{"at": 2.0, "frames": 45, "entree": "noir"}, {"at": 5.0, "frames": 30, "sortie": "flash",
                                                            "sortie_couleur": "#F4A7B9"}]
    trs = render.transitions_of({"transitions": [{"at": 1.0, "type": "flash"}]}, brolls, 30)
    assert trs == [{"at": 1.0, "type": "flash"}, {"at": 2.0, "type": "noir"},
                   {"at": 6.0, "type": "flash", "couleur": "#F4A7B9"}]  # sortie = at + durée à l'écran
    # « muet » : même durée, piste son continue mais silencieuse
    src = tmp_path / "r.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src))
    out = tmp_path / "s.mkv"
    render.render_segment({"start": 0.2, "frames": 30, "muet": True}, src, out, 180, 320, 30, [(0, 0, 135, 240)] * 30,
                          "", True)
    info = probe(out)
    assert info["has_audio"] and abs(info["duration"] - 1.0) < 0.05
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8").stderr
    assert float(re.search(r"max_volume: (-?[\d.inf]+)", r).group(1)) < -60


def test_transition_fuite_degrade_qui_monte():
    """« fuite » : connue, posable en sortie de b-roll, dégradé d'opacité vertical (pas un aplat qui clignote)."""
    import render
    assert "fuite" in render.TRANSITIONS
    trs = render.transitions_of({}, [{"at": 1.0, "frames": 30, "sortie": "fuite"}], 30)
    assert trs == [{"at": 2.0, "type": "fuite"}]
    f = render.transition_alpha("fuite", 0.3)
    assert f.startswith("geq=") and "Y-H*" in f and "fade" not in f
    assert "fade=t=in" in render.transition_alpha("flash", 0.24)


def test_sous_titres_sur_inserts_et_broll_selon_le_profil():
    import render
    b = [{"at": 0, "frames": 30, "texte": True}, {"at": 2, "frames": 30, "texte": True, "sous_titres": True},
         {"at": 4, "frames": 30}]
    assert render.sans_sous_titres(b, {}, 30) == [(0, 1.0)]  # insert à texte masque, mosaïque et plan filmé gardent
    assert render.sans_sous_titres(b, {"sur_broll": False}, 30) == [(0, 1.0), (4, 5.0)]


def test_agrandissement_borne_et_fond_flou():
    """Paysage 1080×1000 recadré en 9:16 = ×1,92 (net, accentué) : le zoom ajouté est borné à ×2 (sauf cadrage voulu) ; fond flou = plan
    entier jamais agrandi au-delà d'AGRANDI_MAX."""
    import reframe as rf
    import render
    assert rf.agrandissement(1080, 1000, 1080, 1920) == pytest.approx(1.92)
    assert rf.agrandissement(3840, 2160, 1080, 1920, 1.1) < rf.AGRANDI_MAX  # 4K : de la marge pour zoomer
    assert rf.zoom_borne(1080, 1000, 1080, 1920, 1.35) == pytest.approx(2.0 / 1.92)
    assert rf.zoom_borne(1080, 1000, 1080, 1920, 1.35, voulu=True) == 1.35
    assert rf.zoom_borne(3840, 2160, 1080, 1920, 1.5) == 1.5
    assert rf.agrandissement(1080, 1000, 1080, 1920, fond_flou=True) == 1.0
    assert rf.agrandissement(320, 180, 1080, 1920, fond_flou=True) == rf.AGRANDI_MAX
    f = render.fond_flou_filtre((0, 0, 1080, 1000), 1080, 1920, "")
    assert "crop=1080:1000:0:0,split" in f and "[ff]scale=1080:1000" in f and "gblur" in f and "overlay=(W-w)/2" in f


def test_masque_delogo_dans_le_cadre():
    import render
    assert render.masque_filtre(None, 1080, 980) == ""
    assert render.masque_filtre([0.5, 0.1, 0.2, 0.05], 1080, 980) == "delogo=x=540:y=98:w=216:h=49,"
    # zone qui touche les bords (filigrane en bas à droite) : ramenée à 1 px, delogo la refuserait sinon
    assert render.masque_filtre([0.8, 0.9, 0.2, 0.1], 1080, 980) == "delogo=x=864:y=882:w=215:h=97,"
    ff("-f", "lavfi", "-i", "color=c=red:s=100x100:d=0.1", "-vf", render.masque_filtre([0, 0.5, 1, 0.5], 100, 100)
       + "null", "-f", "null", "-")


def test_luma_derriere_les_sous_titres(tmp_path):
    import render
    v = tmp_path / "v.mkv"
    ff("-f", "lavfi", "-i", "color=c=black:s=108x192:d=1", "-vf", "drawbox=y=ih/2:w=iw:h=ih/2:c=white:t=fill",
       "-pix_fmt", "yuv420p", str(v))
    assert render.luma_zone(v, 0.5, 0.0, 0.4) < 0.1 and render.luma_zone(v, 0.5, 0.6, 0.9) > 0.9
    assert render.luma_zone(tmp_path / "absent.mkv", 0, 0, 1) is None
