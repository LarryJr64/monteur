"""Image : suivi du visage (recadrage 9:16, centre des zooms), courbes de zoom, mesure et correction couleur.

- `face_track(video, t0, t1)` : centres de visage lissés image par image (OpenCV YuNet, modèle MIT d'opencv_zoo,
  téléchargé une fois dans ~/.cache/monteur). Détections brutes en cache dans `.monteur/visages/`.
- `zoom_curve(...)` : échelle par image pour aucun | doux | punch | crash | statique (v1).
- `crop_box(...)` : zone source à recadrer pour une échelle et un centre (visage : yeux vers le 1/3 haut).
- `measure_colour(video)` / `colour_filter(mesure, cible)` : MÊME code pour les rushs (render) et les
  références (analyze_style), sinon la comparaison n'a pas de sens.
"""
import hashlib
import json
import math
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import ffmpeg, probe, tonemap  # noqa: E402

MODEL_NAME = "face_detection_yunet_2023mar.onnx"
# opencv_zoo, licence MIT (models/face_detection_yunet/LICENSE, Shiqi Yu) — vérifiée le 05/11/2026
MODEL_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/" + MODEL_NAME
CACHE = Path.home() / ".cache" / "monteur"
RATE = 8            # détections par seconde (5-10 suffisent pour une tête qui parle)
DET_W = 320         # largeur d'analyse : un visage qui parle y fait encore 30-100 px, YuNet le voit
ALPHA = 0.08        # lissage exponentiel par image à 30 i/s (0,05-0,1 : mouvement de caméra posé)
DEAD = 0.12         # zone morte : le cadre ne bouge pas tant que le visage reste dans ±12 % de sa largeur
CADRAGE_MAX = 2.0   # zoom de base maximal pour imiter un cadrage serré
SUIVI_MAX = 0.08    # le cadre ne traîne jamais à plus de 8 % de la largeur du visage derrière lui (yeux gardés)
VISAGE_MAX = 1.2    # visage ≤ 1,2× la largeur du cadre (zooms compris) : au-delà les yeux sortent du cadre
FACE_Y = 0.38       # centre du visage à 38 % du haut du cadre → yeux vers le tiers supérieur
FALLBACK = (0.5, 0.4, 0.0)
_warned = set()
_LOCK = threading.Lock()


def _say(msg):
    if msg not in _warned:
        _warned.add(msg)
        print(msg, flush=True)


def model_path():
    """Chemin du modèle YuNet (téléchargé la première fois, ~230 ko) ; None si impossible."""
    f = CACHE / MODEL_NAME
    if f.is_file() and f.stat().st_size > 100_000:
        return f
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        data = urllib.request.urlopen(MODEL_URL, timeout=30).read()
        if len(data) < 100_000:  # page d'erreur ou pointeur LFS
            raise OSError("fichier trop petit")
        f.write_bytes(data)
        return f
    except OSError as e:
        _say(f"Modèle de visage indisponible ({e}) : recadrage au centre.")
        return None


def _detector(w, h):
    try:
        import cv2
    except ImportError:
        _say("opencv-python absent (pip install opencv-python) : recadrage au centre, sans suivi du visage.")
        return None
    m = model_path()
    return cv2.FaceDetectorYN.create(str(m), "", (w, h), 0.6, 0.3, 50) if m else None


def _cache_file(video, cache_dir):
    video = Path(video).resolve()
    if cache_dir is None:  # dossier de travail = premier parent qui contient .monteur/
        cache_dir = next((p / ".monteur" for p in video.parents if (p / ".monteur").is_dir()), CACHE)
    key = hashlib.md5(str(video).encode("utf-8")).hexdigest()[:8]
    return Path(cache_dir) / "visages" / f"{video.stem}_{key}.json"


def _detect_range(video, info, i0, i1):
    """Détections brutes aux instants k/RATE, k ∈ [i0, i1] : {k: [cx, cy, taille] ou 0}."""
    W, H = info["width"], info["height"]
    dw = DET_W if W > DET_W else W - W % 2
    dh = round(H * dw / W / 2) * 2
    det = _detector(dw, dh)
    if det is None:
        return None
    cmd = [ffmpeg(), "-v", "error", "-ss", f"{i0 / RATE:.4f}", "-i", str(video), "-t", f"{(i1 - i0 + 1) / RATE:.4f}",
           "-vf", f"fps={RATE},scale={dw}:{dh}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size, res, k, prev = dw * dh * 3, {}, i0, None
    while k <= i1:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        _, faces = det.detect(np.frombuffer(buf, np.uint8).reshape(dh, dw, 3))
        if faces is not None and len(faces):
            # l'orateur = le plus grand visage, en restant sur celui d'avant à taille comparable (pas de va-et-vient)
            f = max(faces, key=lambda f: f[2] * f[3] * f[14] / (1 + 5 * (math.dist(
                ((f[0] + f[2] / 2) / dw, (f[1] + f[3] / 2) / dh), prev[:2]) if prev else 0)))
            prev = [round(float((f[0] + f[2] / 2) / dw), 4), round(float((f[1] + f[3] / 2) / dh), 4),
                    round(float(f[3] / dh), 4)]
            res[k] = prev
        else:
            res[k] = 0
        k += 1
    p.stdout.close()
    p.wait()
    for j in range(k, i1 + 1):  # fin de fichier : rien à voir
        res[j] = 0
    return res


def _read_cache(cf, sig):
    try:
        c = json.loads(cf.read_text(encoding="utf-8"))
        return {int(k): v for k, v in c["det"].items()} if c.get("sig") == sig else {}
    except (OSError, ValueError, KeyError):
        return {}


def raw_detections(video, t0, t1, cache_dir=None):
    """Détections brutes (cache json par source, complété au besoin) entre t0 et t1 : ({k: [cx, cy, taille] | 0}, info)
    avec k = t × RATE ; None si pas de détecteur. Sûr en parallèle (render analyse plusieurs segments à la fois)."""
    video = Path(video)
    info = probe(video)
    if "width" not in info:
        return None
    cf = _cache_file(video, cache_dir)
    st = video.stat()
    sig = {"taille": st.st_size, "mtime": round(st.st_mtime), "rate": RATE}
    with _LOCK:
        cache = _read_cache(cf, sig)
    i0, i1 = max(0, math.floor(t0 * RATE)), math.ceil(t1 * RATE)
    missing = [k for k in range(i0, i1 + 1) if k not in cache]
    if missing:
        new = _detect_range(video, info, missing[0], missing[-1])  # hors verrou : les détections tournent en parallèle
        if new is None:
            return None
        with _LOCK:  # relu puis fusionné : un autre fil a pu compléter le cache entre-temps
            cache = {**_read_cache(cf, sig), **new}
            cf.parent.mkdir(parents=True, exist_ok=True)
            tmp = cf.with_suffix(".tmp")
            tmp.write_text(json.dumps({"source": str(video), "sig": sig,
                                       "det": {str(k): cache[k] for k in sorted(cache)}}), encoding="utf-8")
            tmp.replace(cf)
    return {k: cache[k] for k in range(i0, i1 + 1)}, info


def face_track(video, t0, t1, fps=30, crop_w=None, cache_dir=None):
    """Centres de visage lissés, un par image de [t0, t1[ : [(t, cx, cy, taille)] en relatif (taille = hauteur
    du visage / hauteur image). Zone morte + lissage exponentiel ; visage perdu → dernière position gardée ;
    jamais vu → (0.5, 0.4, 0)."""
    n = max(1, round((t1 - t0) * fps))
    times = [t0 + i / fps for i in range(n)]
    got = raw_detections(video, t0, t1, cache_dir)
    if not got or not any(got[0].values()):
        return [(t, *FALLBACK) for t in times]
    det, info = got
    W, H = info["width"], info["height"]
    if crop_w is None:  # largeur du cadre 9:16 dans la source (relative)
        crop_w = min(1.0, H * 9 / 16 / W)
    crop_h = min(1.0, W * 16 / 9 / H)
    # trous : on garde la dernière position (avant la 1re détection : la 1re)
    ks = sorted(det)
    first = next(det[k] for k in ks if det[k])
    filled, last = {}, first
    for k in ks:
        last = det[k] or last
        filled[k] = last
    target = cur = None
    out = []
    for t in times:
        u = t * RATE
        k0 = min(max(ks[0], math.floor(u)), ks[-1])
        k1 = min(k0 + 1, ks[-1])
        a, b, f = filled[k0], filled[k1], u - k0
        raw = [a[j] + (b[j] - a[j]) * min(max(f, 0), 1) for j in range(3)]
        if target is None:
            target, cur = list(raw), list(raw)
        dead = (DEAD * crop_w, DEAD * crop_h * 0.6, 0.15 * max(raw[2], 1e-3))
        for j in range(3):
            if abs(raw[j] - target[j]) > dead[j]:
                target[j] = raw[j]
            cur[j] += ALPHA * 30 / fps * (target[j] - cur[j])
        # tête qui tourne vite : la zone morte + le lissage laisseraient sortir un œil du cadre zoomé
        lim = SUIVI_MAX * raw[2] * H / W
        bord = min(max(cur[0], raw[0] - lim), raw[0] + lim)
        cur[0] += 0.6 * (bord - cur[0])  # rattrapage rapide mais sans à-coup (dépassement ≤ ~1/3 de pas d'image)
        out.append((t, cur[0], cur[1], cur[2]))
    return out


# ---------- géométrie et zooms ----------

def active_area(path, info, samples=5, seuil=24):
    """(gauche, haut, droite, bas) en px des bandes noires du média (letterbox, bande de capture), 0 si aucune.
    Une rangée/colonne compte comme bande si elle reste noire sur `samples` images réparties sur toute la vidéo
    (une scène sombre ne la déclenche pas). Bande ≥ 4 px et ≤ 25 % de la dimension, sinon ignorée."""
    W, H, dur = info.get("width"), info.get("height"), info.get("duration") or 0
    if not W or not H:
        return (0, 0, 0, 0)
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-vf",
                        f"fps={samples / max(dur, 0.2):.4f},scale={W}:{H},format=gray", "-frames:v", str(samples),
                        "-f", "rawvideo", "-"], capture_output=True)
    n = len(r.stdout) // (W * H)
    if not n:
        return (0, 0, 0, 0)
    a = np.frombuffer(r.stdout[:n * W * H], np.uint8).reshape(n, H, W).max(0)
    rows, cols = a.max(1) > seuil, a.max(0) > seuil

    def edge(mask, lim):
        k = int(np.argmax(mask)) if mask.any() else 0
        return k if 4 <= k <= lim else 0
    return (edge(cols, W // 4), edge(rows, H // 4), edge(cols[::-1], W // 4), edge(rows[::-1], H // 4))


def crop_box(W, H, w, h, z, center, face=False, face_y=None):
    """(x, y, cw, ch) en pixels source : zone au ratio w:h, réduite de `z`, autour de `center` (relatif).
    face=True : le centre (visage) est placé à FACE_Y du haut au lieu du milieu."""
    cw, ch = min(W, H * w / h) / z, min(H, W * h / w) / z
    x = min(max(center[0] * W - cw / 2, 0), W - cw)
    y = min(max(center[1] * H - ((face_y or FACE_Y) if face else 0.5) * ch, 0), H - ch)
    return x, y, cw, ch


def _ease_cubic(u):
    return 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def _ease_sine(u):
    return -(math.cos(math.pi * u) - 1) / 2


def zoom_target(kind, zoom):
    """Échelle visée selon le type (valeurs par défaut du teardown : punch 1.12, doux 1.08, crash 1.25-1.35)."""
    zoom = zoom or 1.0
    if kind == "crash":
        return min(max(zoom if zoom > 1 else 1.3, 1.25), 1.35)
    if kind == "punch":
        return zoom if zoom > 1 else 1.12
    if kind == "doux":
        return zoom if zoom > 1 else 1.08
    return 1.0 if kind == "aucun" else max(zoom, 1.0)


def zoom_curve(kind, zoom, frames, prev=1.0, fps=30, rampe_s=0.5):
    """Échelle par image. doux : 1 → zoom sur tout le segment ; punch : rampe aisée de 12-18 images depuis
    l'échelle précédente puis tenue ; crash : rampe de 5 images vers 1.25-1.35 ; aucun : 1 ; None : statique (v1)."""
    z1 = zoom_target(kind, zoom)
    if kind == "doux" and frames > 1:
        return [1 + (z1 - 1) * _ease_sine(i / (frames - 1)) for i in range(frames)]
    if kind in ("punch", "crash"):
        n30 = 5 if kind == "crash" else min(max(round(rampe_s * 30), 12), 18)  # en images à 30 i/s
        n = min(round(n30 * fps / 30), frames - 1)
        z0 = prev if prev < z1 else 1.0  # on part de l'échelle précédente, jamais d'un « dézoom »
        return [z1 if n <= 0 else z0 + (z1 - z0) * _ease_cubic(min(1.0, i / n)) for i in range(frames)]
    return [z1] * frames


def doux_u(segs, style):
    """Poussée lente (doux) sur un segment que pacing a découpé en morceaux (même `seg`) : position [u0, u1] de
    chaque morceau dans la poussée du segment entier. Lue dans zoom_from/zoom_to (pacing) quand ils sont là,
    sinon recalculée en regroupant les morceaux par `seg` (images cumulées)."""
    groups = {}
    for s in segs:
        doux = s.get("zoom_type") == "doux" or (not s.get("zoom_type") and style == "doux")
        if doux and s.get("zoom", 1.0) > 1.0 and "seg" in s:
            groups.setdefault(s["seg"], []).append(s)
    for g in groups.values():
        if len(g) < 2:
            continue
        total, done = sum(s["frames"] for s in g), 0
        for s in g:
            if "zoom_from" in s and "zoom_to" in s:
                # pacing exprime la poussée en échelle linéaire du segment d'origine : on la ramène à [0, 1]
                z0 = s["zoom"] - 1
                s["doux_u"] = ((s["zoom_from"] - 1) / z0, (s["zoom_to"] - 1) / z0)
            else:
                s["doux_u"] = (done / total, (done + s["frames"]) / total)
            done += s["frames"]


def base_zoom(track, W, H, w, h, cadrage):
    """Zoom de base d'un plan face caméra pour retrouver le cadrage de la DA (`cadrage.visage_largeur` = largeur
    du visage / largeur de l'image 9:16). 1.0 si inconnu ou si le rush est déjà plus serré (on ne dézoome pas).
    Borné à CADRAGE_MAX : au-delà, l'image d'un rush 1080p devient trop molle."""
    cible = (cadrage or {}).get("visage_largeur")
    tailles = sorted(t[3] for t in track or [] if t and t[3] > 0)
    if not cible or not tailles:
        return 1.0
    face_px = tailles[len(tailles) // 2] * H  # boîte YuNet ≈ carrée : hauteur relative × H = largeur en px
    cw = min(W, H * w / h)
    return round(min(max(cible * cw / face_px, 1.0), CADRAGE_MAX), 4)


def segment_scales(seg, kind, prev=1.0, fps=30, rampe_s=0.5):
    """Échelle par image d'un segment (render et export_xml) : courbe du type de zoom, ou, pour un morceau d'un
    segment « doux » découpé par pacing (`doux_u`, voir doux_u), sa part de la poussée continue du segment entier."""
    if kind == "doux" and "doux_u" in seg:
        u0, u1 = seg["doux_u"]
        z1, n = zoom_target("doux", seg.get("zoom", 1.0)), seg["frames"]
        return [1 + (z1 - 1) * _ease_sine(u0 + (u1 - u0) * i / n) for i in range(n)]
    return zoom_curve(kind, seg.get("zoom", 1.0), seg["frames"], prev, fps, rampe_s)


# ---------- couleur ----------

def _frames_rgb(video, t0=None, t1=None, n=8):
    """`n` images réparties sur [t0, t1] (une recherche rapide par image, en parallèle), RGB float, 160 px de large."""
    from concurrent.futures import ThreadPoolExecutor
    info = probe(video)
    if "width" not in info:  # pas de piste vidéo (son seul) : rien à mesurer
        return np.zeros((0, 1, 1, 3), np.float32)
    a, b = t0 or 0.0, t1 if t1 is not None else info["duration"]
    w = 160
    h = round(info["height"] * w / info["width"] / 2) * 2
    times = [a + (b - a) * (i + 0.5) / n for i in range(n)]

    def grab(t):
        return subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                               "-vf", f"scale={w}:{h},{tonemap(info)}null", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                              capture_output=True).stdout

    with ThreadPoolExecutor(4) as pool:
        frames = [d for d in pool.map(grab, times) if len(d) == w * h * 3]
    if not frames:
        return np.zeros((0, h, w, 3), np.float32)
    return np.frombuffer(b"".join(frames), np.uint8).reshape(len(frames), h, w, 3).astype(np.float32) / 255


def pooled_colour(stats, weights):
    """Stats couleur de plusieurs plans mis bout à bout (pondérés par leur durée) : moyennes pondérées,
    contraste = écart-type global (variance intra + inter plans)."""
    ok = [(s, w) for s, w in zip(stats, weights) if s and w > 0]
    if not ok:
        return None
    tw = sum(w for _, w in ok)
    mean = {k: sum(s[k] * w for s, w in ok) / tw for k in ("luma", "saturation", "temperature")}
    var = sum(w * (s["contraste"] ** 2 + (s["luma"] - mean["luma"]) ** 2) for s, w in ok) / tw
    return {**{k: round(v, 4) for k, v in mean.items()}, "contraste": round(var ** 0.5, 4)}


def measure_colour(video, t0=None, t1=None, box=None, n=8):
    """Statistiques couleur d'une vidéo (bande 10-60 % de la hauteur : visage/décor, hors sous-titres) :
    luma = Y moyen (Rec.709, 0-1), contraste = écart-type de Y, saturation = chroma moyenne (max-min RGB),
    temperature = R moyen - B moyen. Partagé avec analyze_style (références) pour que les mesures se comparent.
    `box` = cadre de sortie (x0, y0, x1, y1) relatifs : on mesure l'image telle qu'elle sera à l'écran
    (rush 16:9 recadré en 9:16), donc comparable aux références du créateur."""
    f = _frames_rgb(video, t0, t1, n)
    if not len(f):
        return None
    if box:
        H0, W0 = f.shape[1:3]
        x0, y0, x1, y1 = box
        f = f[:, int(H0 * y0):max(int(H0 * y1), int(H0 * y0) + 2), int(W0 * x0):max(int(W0 * x1), int(W0 * x0) + 1)]
    h = f.shape[1]
    f = f[:, int(h * 0.1):int(h * 0.6)]
    y = f[..., 0] * 0.2126 + f[..., 1] * 0.7152 + f[..., 2] * 0.0722
    return {"luma": round(float(y.mean()), 4), "contraste": round(float(y.std()), 4),
            "saturation": round(float((f.max(-1) - f.min(-1)).mean()), 4),
            "temperature": round(float(f[..., 0].mean() - f[..., 2].mean()), 4)}


def colour_filter(m, target):
    """Filtre ffmpeg qui rapproche les stats `m` de `target` (profile.couleur), bornées pour rester discret."""
    if not m or not target:
        return ""
    clamp = lambda v, a, b: min(max(v, a), b)  # noqa: E731
    parts = []
    if "temperature" in target:
        k = clamp((target["temperature"] - m["temperature"]) / max(2 * m["luma"], 0.1), -0.06, 0.06)
        if abs(k) > 0.004:
            parts.append(f"colorchannelmixer=rr={1 + k:.4f}:bb={1 - k:.4f}")
    c = clamp(target.get("contraste", m["contraste"]) / max(m["contraste"], 0.02), 0.85, 1.2)
    b = clamp(target.get("luma", m["luma"]) - ((m["luma"] - 0.5) * c + 0.5), -0.08, 0.08)
    s = clamp(target.get("saturation", m["saturation"]) / max(m["saturation"], 0.02), 0.8, 1.3)
    if abs(c - 1) > 0.01 or abs(b) > 0.005 or abs(s - 1) > 0.01:
        parts.append(f"eq=contrast={c:.3f}:brightness={b:.3f}:saturation={s:.3f}")
    return ",".join(parts)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Suivi du visage / mesure couleur d'une vidéo (diagnostic).")
    ap.add_argument("video")
    ap.add_argument("--debut", type=float, default=0.0)
    ap.add_argument("--fin", type=float, default=None)
    a = ap.parse_args()
    fin = a.fin if a.fin is not None else probe(a.video)["duration"]
    tr = face_track(a.video, a.debut, fin, fps=RATE)
    for t, x, y, s in tr[::RATE]:
        print(f"{t:7.2f}s  visage x={x:.3f} y={y:.3f} taille={s:.3f}")
    print("Couleur :", measure_colour(a.video))
