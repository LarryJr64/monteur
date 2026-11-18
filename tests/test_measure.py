"""Calibrage de measure.py sur des vidéos synthétiques « face caméra ».

Fond texturé fixe + une « personne » (tête, épaules, main) qui bouge comme quand on parle ;
un jump cut = saut dans le temps source (la personne se décale, le cadre reste le même).
"""
import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import measure  # noqa: E402
from common import ffmpeg  # noqa: E402

W, H, FPS = 360, 640, 30


def _background():
    rng = np.random.default_rng(1)
    bg = Image.fromarray((rng.random((22, 12)) * 120 + 70).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    d = ImageDraw.Draw(bg)
    for _ in range(25):  # meubles / cadres : de la structure nette pour mesurer les échelles
        x, y = rng.integers(0, W - 40), rng.integers(0, H - 40)
        d.rectangle([x, y, x + rng.integers(15, 60), y + rng.integers(15, 60)], fill=int(rng.integers(75, 185)))
    return bg.convert("RGB")


BG = _background()


def _person(t, gesture=False):
    """Image source à l'instant t : la personne oscille doucement (parole), main rapide si geste."""
    img = BG.copy()
    d = ImageDraw.Draw(img)
    x = W * (0.5 + 0.05 * math.sin(2 * math.pi * 0.4 * t))
    y = H * (0.42 + 0.015 * math.sin(2 * math.pi * 0.9 * t))
    d.ellipse([x - 110, y + 90, x + 110, y + 400], fill=(60, 70, 120))   # épaules
    d.ellipse([x - 55, y - 75, x + 55, y + 75], fill=(170, 130, 110))    # tête
    d.ellipse([x - 30, y - 15, x - 15, y], fill=(40, 30, 30))
    d.ellipse([x + 15, y - 15, x + 30, y], fill=(40, 30, 30))
    mouth = 6 + 6 * abs(math.sin(2 * math.pi * 3 * t))
    d.ellipse([x - 20, y + 30, x + 20, y + 30 + mouth], fill=(90, 40, 40))
    if gesture:  # main qui traverse vite (≈8 px/image)
        hx = W * 0.15 + abs((t * 240) % (W * 1.4) - W * 0.7)  # aller-retour, sans téléportation
        d.ellipse([hx - 30, H * 0.75 - 30, hx + 30, H * 0.75 + 30], fill=(175, 135, 115))
    return img


def _write_wav(path, dur, gaps=(), amp=0.3, freq=220):
    t = np.arange(int(dur * 48000)) / 48000
    a = amp * np.sin(2 * math.pi * freq * t)
    for s, e in gaps:
        a[(t >= s) & (t < e)] = 0
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(48000)
        w.writeframes((a * 32767).astype(np.int16).tobytes())


def _broll(t):
    """Plan de coupe : autre décor, personne absente, un objet qui glisse."""
    img = BG.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    x = 60 + 40 * t
    ImageDraw.Draw(img).rectangle([x, 200, x + 120, 330], fill=(120, 110, 100))
    return img


MOTS = ("MOT", "ERREUR", "VITE", "TROIS", "SALUT", "TOUT", "ARGENT")


def make_video(path, segments, captions=False, gesture=False, noise=2.0, gaps=(), amp=0.3, freq=220, size=(W, H),
               accent=None, emojis=()):
    """segments : [(début_source_s, durée_s, zoom_début, zoom_fin[, "broll"])] mis bout à bout.
    accent : couleur du 2e mot d'un bloc de sous-titres sur deux ; emojis : [(t0, t1)] rond jaune au-dessus."""
    rng = np.random.default_rng(2)
    font = ImageFont.load_default(size=30)
    frames, k = [], 0
    for src, dur, z0, z1, *kind in segments:
        n = round(dur * FPS)
        for j in range(n):
            z = z0 + (z1 - z0) * j / max(n - 1, 1)
            img = _broll(src + j / FPS) if kind == ["broll"] else _person(src + j / FPS, gesture)
            w, h = W / z, H / z
            left, top = W / 2 - w / 2, min(max(H * 0.45 - h / 2, 0), H - h)
            img = img.resize(size, Image.BILINEAR, box=(left, top, left + w, top + h))
            d = ImageDraw.Draw(img)
            b = k // 15  # un bloc toutes les 0,5 s ; le texte change (un texte fixe = décor, ignoré)
            if captions and accent and b % 2 == 0:
                d.text((size[0] / 2, size[1] * 0.7), MOTS[b % len(MOTS)] + " ", font=font, fill="white",
                       stroke_width=3, stroke_fill="black", anchor="rm")
                d.text((size[0] / 2, size[1] * 0.7), ("PIRE", "JAMAIS", "RIEN")[b // 2 % 3], font=font, fill=accent,
                       stroke_width=3, stroke_fill="black", anchor="lm")
            elif captions:
                d.text((size[0] / 2, size[1] * 0.7), f"{MOTS[b % len(MOTS)]} {b}", font=font, fill="white",
                       stroke_width=3, stroke_fill="black", anchor="mm")
            if any(t0 <= k / FPS < t1 for t0, t1 in emojis):
                cx, cy = size[0] / 2, size[1] * 0.58
                d.ellipse([cx - 18, cy - 18, cx + 18, cy + 18], fill=(255, 200, 0))
            a = np.asarray(img, np.float32) + rng.normal(0, noise, (size[1], size[0], 1))
            frames.append(np.clip(a, 0, 255).astype(np.uint8).tobytes())
            k += 1
    total = k / FPS
    args = [ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}",
            "-r", str(FPS), "-i", "-"]
    if amp is not None:
        wav = Path(path).with_suffix(".wav")
        _write_wav(wav, total, gaps, amp, freq)
        args += ["-i", str(wav), "-c:a", "aac", "-b:a", "128k"]
    args += ["-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(args, input=b"".join(frames), check=True)
    return path


def _cut_times(path):
    frames = measure.decode(path, measure.ANALYSE_LARGEUR, fps=FPS)
    return [i / FPS for i in measure.detect_cuts(measure.frame_diffs(frames), FPS)], frames


def test_parole_continue_sans_coupe(tmp_path):
    v = make_video(tmp_path / "continu.mp4", [(0, 6, 1, 1)], gesture=True)
    cuts, _ = _cut_times(v)
    assert cuts == []


def test_jump_cuts_detectes(tmp_path):
    # sauts de 0.6 à 1.3 s dans la source : la personne se décale de quelques % du cadre
    v = make_video(tmp_path / "jump.mp4", [(0, 1.5, 1, 1), (2.1, 1.2, 1, 1), (4.0, 1.0, 1, 1), (5.3, 1.5, 1, 1)])
    cuts, _ = _cut_times(v)
    assert len(cuts) == 3
    for got, want in zip(cuts, [1.5, 2.7, 3.7]):
        assert abs(got - want) <= 1.5 / FPS


def test_punch_in_cut_et_echelle(tmp_path):
    v = make_video(tmp_path / "punch.mp4", [(0, 2, 1, 1), (2, 2, 1.2, 1.2), (4, 2, 1.1, 1.1)])
    cuts, frames = _cut_times(v)
    assert [round(c * FPS) for c in cuts] == [60, 120]
    z = measure.zooms(frames, [60, 120], FPS)
    assert len(z) == 1 and z[0]["type"] == "cut" and abs(z[0]["echelle"] - 1.2) <= 0.03
    assert not measure.zoom_fin(frames, [60])  # le dernier plan est plus serré


def test_zoom_fin_dernier_plan_plus_large(tmp_path):
    v = make_video(tmp_path / "fin.mp4", [(0, 1.5, 1.2, 1.2), (1.5, 1.5, 1, 1)])
    cuts, frames = _cut_times(v)
    assert measure.zoom_fin(frames, [round(c * FPS) for c in cuts])


def test_jump_cut_sans_zoom_pas_compte_comme_zoom(tmp_path):
    v = make_video(tmp_path / "jz.mp4", [(0, 1.5, 1, 1), (2.1, 1.5, 1, 1)])
    cuts, frames = _cut_times(v)
    assert len(cuts) == 1
    assert measure.zooms(frames, [round(c * FPS) for c in cuts], FPS) == []


def test_zoom_doux_sans_coupe(tmp_path):
    v = make_video(tmp_path / "doux.mp4", [(0, 3, 1.0, 1.25)])
    cuts, frames = _cut_times(v)
    assert cuts == []
    z = measure.zooms(frames, [], FPS)
    assert len(z) == 1 and z[0]["type"] == "doux" and abs(z[0]["echelle"] - 1.25) <= 0.05


def test_audio_lufs_et_silences(tmp_path):
    v = make_video(tmp_path / "a.mp4", [(0, 6, 1, 1)], amp=0.1, freq=1000, gaps=[(1.0, 1.4), (3.0, 3.8)])
    lufs, sil = measure.audio(v, 6.0)
    assert abs(lufs - -23.0) <= 0.7  # sinus 1 kHz, amplitude 0.1 en mono = -23 LUFS
    assert len(sil) == 2 and abs((sil[1]["end"] - sil[1]["start"]) - 0.8) <= 0.06
    assert measure.fond_sonore(v) < measure.FOND_MUSIQUE_DB  # voix coupée net : pas de fond musical
    continu = make_video(tmp_path / "m.mp4", [(0, 3, 1, 1)], amp=0.05, freq=300)
    assert measure.fond_sonore(continu) > measure.FOND_MUSIQUE_DB


def test_sous_titres(tmp_path):
    with_caps = make_video(tmp_path / "st.mp4", [(0, 4, 1, 1)], captions=True)
    without = make_video(tmp_path / "nost.mp4", [(0, 4, 1, 1)])
    info = {"duration": 4.0, "width": W, "height": H}
    assert measure.caption_score(with_caps, info) >= 0.8
    assert measure.caption_score(without, info) == 0.0
    score, (hauteur, y) = measure.caption_score(with_caps, info, geometry=True)
    assert abs(y - 0.7) <= 0.03 and 0.01 < hauteur < 0.1  # texte centré à 70 % de la hauteur


def test_measure_complet(tmp_path):
    v = make_video(tmp_path / "m.mp4", [(0, 2, 1, 1), (2.6, 2, 1.15, 1.15), (5.0, 2, 1, 1)], gaps=[(3.0, 3.3)])
    m = measure.measure(v)
    assert m["format"] == {"width": W, "height": H, "fps": 30.0}
    assert len(m["plans"]) == 3 and abs(m["duree_moyenne_plan_s"] - 2.0) < 0.05
    assert m["zooms_par_minute"] > 0 and abs(m["zoom_intensite"] - 1.15) <= 0.03
    assert abs(m["silence_max_s"] - 0.3) <= 0.06 and m["lufs"] is not None


def fake_faces(img):
    """Détecteur de test : la tête synthétique (couleur peau) ; aucun visage dans les b-roll."""
    m = (np.abs(img.astype(np.int16) - (170, 130, 110)).max(axis=-1) < 18)
    if m.mean() < 0.005:
        return None
    ys, xs = np.nonzero(m)
    h, w = m.shape
    return (xs.mean() / w, ys.mean() / h, (xs.max() - xs.min()) / w)


def test_types_de_zoom_et_rampe(tmp_path):
    # plan fixe | jump cut, 0,2 s, punch (rampe 15 images) tenu | jump cut, 0,2 s, crash (rampe 5 images) tenu
    # (une rampe qui démarre sur l'image même de la coupe masque la coupe : ses écarts sont plus forts)
    v = make_video(tmp_path / "types.mp4", [(0, 1.5, 1, 1), (2.1, 0.2, 1, 1), (2.3, 0.5, 1.0, 1.2), (2.8, 1.3, 1.2, 1.2),
                                            (4.5, 0.2, 1, 1), (4.7, 5 / FPS, 1.0, 1.3), (4.7 + 5 / FPS, 1.3, 1.3, 1.3)])
    cuts, frames = _cut_times(v)
    assert [round(c * FPS) for c in cuts] == [45, 105]
    z = measure.zooms(frames, [45, 105], FPS)
    assert [x["type"] for x in z] == ["punch", "crash"], z
    assert 0.3 <= z[0]["rampe_s"] <= 0.7 and z[1]["rampe_s"] <= measure.RAMPE_CRASH_S
    assert abs(z[0]["echelle"] - 1.2) <= 0.03 and abs(z[1]["echelle"] - 1.3) <= 0.04
    assert 1.7 <= z[0]["t"] <= 2.0


def test_broll_couleurs_sous_titres_emojis(tmp_path, monkeypatch):
    monkeypatch.setattr(measure, "face_detector", lambda: fake_faces)
    monkeypatch.setattr(measure, "raw_detections", None)  # YuNet ne voit pas une tête dessinée
    v = make_video(tmp_path / "riche.mp4", [(0, 2, 1, 1), (2.6, 2, 1, 1, "broll"), (5.0, 2, 1, 1)],
                   captions=True, accent="#FDE047", emojis=[(0.5, 1.5), (4.5, 5.5)], amp=0.05)
    words = [{"w": w, "start": s, "end": s + 0.3} for w, s in
             [("Tu", 0.1), ("perds", 0.6), ("tout.", 1.1), ("Voilà", 2.0), ("pourquoi.", 2.5), ("Fin", 3.5), ("ici.", 4.0)]]
    m = measure.measure(v, words)
    # shortcut connu : un emoji qui apparaît en même temps qu'un bloc de sous-titres peut compter comme coupe
    assert m["broll_plans"] == [[2.0, 4.0]], m["plans"]
    assert abs(m["broll_part"] - 1 / 3) < 0.05 and abs(m["broll_duree_moy_s"] - 2.0) < 0.1
    c = m["sous_titres_couleurs"]
    r, g, b = (int(c["couleur_accent"][i:i + 2], 16) for i in (1, 3, 5))
    assert r > 200 and g > 170 and b < 140, c  # jaune
    assert c["couleur"].startswith("#F") and 0.3 <= c["accent_part_blocs"] <= 0.7
    assert abs(m["emojis_par_minute"] - 2 / (m["duree"] / 60)) < 0.5
    assert 0.2 < m["couleur"]["luma"] < 0.8 and m["visage_part"] > 0.5
    assert m["hook"]["texte"] == "Tu perds tout." and m["hook"]["duree_s"] == 1.4
    assert abs(m["musique_lufs"] - -29) <= 2  # sinus continu amplitude 0,05 ≈ -29 dB sous la « voix »


def test_respiration_et_hook():
    mots = [("Arrête", 0.0, 0.4), ("de", 0.6, 0.7), ("faire", 0.72, 1.0), ("ça.", 1.0, 1.3),
            ("Voilà", 1.7, 2.0), ("le", 2.2, 2.3), ("truc.", 2.3, 2.6), ("Et", 3.0, 3.1), ("fin", 3.3, 3.5),
            ("ici.", 3.5, 3.8), ("Bye.", 4.2, 4.5)]
    words = [{"w": w, "start": a, "end": b} for w, a, b in mots]
    r = measure.respiration(words, cuts=[1.65, 2.95, 4.15])
    assert r["respiration_intra_s"] == 0.2 and r["respiration_phrases_s"] == 0.4
    assert r["coupe_avant_mot_s"] == 0.05
    h = measure.hook(words)
    assert h["type"] == "choc" and h["duree_s"] == 1.3
    assert measure.respiration(words[:5])["respiration_phrases_s"] is None  # < 3 mesures : pas un défaut déguisé
    assert measure.hook([{"w": "Combien", "start": 0, "end": 0.4}, {"w": "gagnes-tu ?", "start": 0.5, "end": 1}])["type"] == "question"


def test_distribution_cadrage_noir():
    q, hook = measure.plan_stats([[0, 1], [1, 1.5], [1.5, 2.5], [2.5, 5.5], [5.5, 9.5]])
    assert q[2] == 1.0 and q[0] < q[2] < q[4] and hook == 1.0  # accroche : plans commençant avant 3 s (1 ; 0,5 ; 1 ; 3)
    vis = [(0.5, 0.4, 0.3)] * 6 + [None] * 3 + [(0.5, 0.45, 0.6)] * 3
    cad = measure.cadrage([[0, 2], [2, 3], [3, 4]], vis, 3.0, [[2, 3]])
    assert cad["visage_largeur"] < 0.45 and 0.4 <= cad["visage_y"] <= 0.45  # base = plans larges, b-roll exclu
    noir = np.zeros((4, 8, 8, 3), np.uint8)
    noir[2:] = 200
    assert measure.images_noires(noir) == 0.5


def test_debit_et_voix_sous_carton():
    W = lambda t, a, b, **k: {"w": t, "start": a, "end": b, **k}  # noqa: E731
    ref = [W("[euh]", 0.03, 0.26, filler=True), W("Du", 2.97, 3.05), W("coup", 3.05, 3.21), W("dans", 3.23, 3.37),
           W("15", 3.39, 3.69), W("ans.", 3.69, 3.83)]
    assert measure.voix_sous_carton(ref, 3.103) is False  # carton muet : un « euh » et un mot à la coupe
    assert measure.debit(ref) == round(5 / (3.83 - 2.97), 3)  # hésitations hors compte
    assert measure.voix_sous_carton([W("Quel", 0.2, 0.5), W("est", 0.5, 0.7)], 3.1) is True
    assert measure.debit(ref[:2]) is None


def test_carton_fin():
    """Carton muet = 1er plan ≥ 1 s sans parole avant sa fin (un « euh » transcrit sur le bruitage ne compte pas)."""
    W = lambda t, a, b, **k: {"w": t, "start": a, "end": b, **k}  # noqa: E731
    ref = [W("[euh]", 0.03, 0.26, filler=True), W("Du", 2.97, 3.05), W("coup", 3.05, 3.21)]
    plans = [[0.0, 3.103], [3.103, 4.6]]
    assert measure.carton_fin(ref, plans) == 3.103
    assert measure.carton_fin([W("Quel", 0.2, 0.5)] + ref, plans) == 0.0  # on parle sous le 1er plan : pas un carton
    assert measure.carton_fin(ref, [[0.0, 0.6], [0.6, 4.6]]) == 0.0  # plan trop court
    assert measure.carton_fin(ref, [[0.0, 5.0]]) == 0.0  # un seul plan
