"""Mesures partagées par analyze_style.py (références) et check.py (nos sorties).

Même code des deux côtés, sinon la comparaison n'a pas de sens.
"""
import math
import re
import statistics
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from common import FF_THREADS, die, ffmpeg, probe, run, tonemap

try:  # lot B (parole) : même mesure de respiration que le rendu
    from pacing import is_filler, measure_breathing
except ImportError:
    measure_breathing = None

    def is_filler(w):
        return bool(w.get("filler"))
try:  # lot A (son) : détection des bruitages
    from audio import carton_appris, detect_sfx
except ImportError:
    detect_sfx = None
try:  # lot C (image) : mêmes visages (YuNet, cache .monteur/visages) et même couleur que le rendu
    from reframe import RATE as VISAGE_RATE, measure_colour, raw_detections
except ImportError:
    measure_colour = raw_detections = None

# --- Calibrage (voir tests/test_measure.py, fixtures synthétiques) ---------------------------
ANALYSE_LARGEUR = 128      # images d'analyse en niveaux de gris, 128 px de large (moyenne = anti-bruit)
ANALYSE_FPS_MAX = 30
COUPE_PLANCHER = 1.5       # différence moyenne minimale (0-255) : un petit jump cut fait ~2,5, une main ~1,3
COUPE_RATIO_FOND = 3.0     # le pic doit dépasser 3x la médiane locale (±0,5 s)...
COUPE_RATIO_VOISINS = 2.0  # ...et 2x ses deux voisines : un geste rapide dure plusieurs images, une coupe une seule
COUPE_ECART_MIN_S = 0.2
ZOOM_ECHELLES = np.arange(1.0, 1.4001, 0.02)
ZOOM_CENTRES = [(cx, cy) for cx in (0.4, 0.5, 0.6) for cy in (0.35, 0.45, 0.55)]
ZOOM_MIN = 1.06            # en dessous : pas un zoom
ZOOM_GAIN = 0.7            # l'image zoomée doit expliquer l'autre bien mieux que sans zoom
ZOOM_GAIN_COUPE = 0.9      # à une coupe (jump cut) la tête bouge entre les prises : gain 0,67-0,87 sur de vrais punch-ins
SILENCE_DB, SILENCE_D = -35, 0.15
SOUS_TITRES_SEUIL = 0.001  # part de pixels « blanc collé à du noir » dans la bande 20-90 % de hauteur
MUSIQUE_TONALITE = 0.01   # part tonale des passages calmes au-dessus de laquelle le fond est une musique
FOND_MUSIQUE_DB = -48      # niveau des 10 % de passages les plus calmes : au-dessus = fond musical continu
# v2
ZOOM_ECHELLES_FINES = np.arange(1.0, 1.4001, 0.01)
RAMPE_CRASH_S = 0.27       # rampe ≤ 8 images à 30 i/s : crash zoom (design : 4-6 images)
RAMPE_DOUX_S = 0.8         # rampe ≥ 0,8 s (ou ≥ 60 % du plan) : poussée lente
VISAGE_YEUX = 0.35         # demi-écart des yeux (coins extérieurs) en part de la largeur de la boîte visage
COULEUR_LARGEUR = 288      # images couleur (visages, couleur, sous-titres colorés, emojis)
COULEUR_IMAGES_MAX = 240
VISAGE_TAILLE_MIN = 0.10   # visage ≥ 10 % de la largeur : l'orateur (un visage minuscule dans un b-roll ne compte pas)
YUNET = Path.home() / ".cache" / "monteur" / "face_detection_yunet_2023mar.onnx"  # téléchargé par doctor.py
PAUSE_MIN_S = 0.06         # écart entre mots en dessous : coarticulation, pas une respiration (= pacing)
FIN_PHRASE = ".?!…"
HOOK_S = 3.0               # l'accroche : les 3 premières secondes (rythme mesuré à part)
DEFAUT_RESPIRATION = {"respiration_intra_s": 0.18, "respiration_phrases_s": 0.38, "coupe_avant_mot_s": 0.08}


def decode(path, width, fps=None, gray=True, info=None):
    """Images brutes (N, h, w[, 3]) uint8 via ffmpeg, à cadence fixe si fps donné."""
    info = info or probe(path)
    if "width" not in info:
        die(f"pas de piste vidéo dans {path}")
    h = max(2, round(width * info["height"] / info["width"] / 2) * 2)
    vf = (f"fps={fps}," if fps else "") + f"scale={width}:{h}:flags=area,{tonemap(info)}null"
    ch = 1 if gray else 3
    taille = width * h * ch
    # lu directement dans un tampon à la bonne taille : la vidéo décodée n'existe qu'une fois en mémoire
    # durée inconnue (rare) : 2 h au plus, le tampon ne grossit qu'au fil de la lecture
    n_max = math.ceil((info["duration"] or 7200) * (fps or info.get("fps") or 30)) + 8
    buf = bytearray(n_max * taille) if info["duration"] else bytearray()
    with tempfile.TemporaryFile() as err:
        p = subprocess.Popen([ffmpeg(), "-v", "error", *FF_THREADS, "-i", str(path), "-an", "-vf", vf, "-frames:v", str(n_max),
                              "-f", "rawvideo", "-pix_fmt", "gray" if gray else "rgb24", "-"], stdout=subprocess.PIPE, stderr=err)
        if buf:
            vue, lu = memoryview(buf), 0
            while lu < len(buf):
                k = p.stdout.readinto(vue[lu:])
                if not k:
                    break
                lu += k
            del vue
        else:
            buf = bytearray(p.stdout.read())
            lu = len(buf)
        p.stdout.close()
        if p.wait() != 0:
            err.seek(0)
            die(f"lecture vidéo impossible ({path}) : {err.read().decode(errors='replace')[-500:]}")
    n = lu // taille
    shape = (n, h, width) if gray else (n, h, width, 3)
    return np.frombuffer(buf, np.uint8, n * taille).reshape(shape)


def frame_diffs(frames):
    """Différence moyenne entre images voisines ; mise à 0 quand le changement tient dans ≤ 2 bandes horizontales
    sur 8 (un sous-titre ou un emoji qui change sur un plan fixe n'est pas une coupe ; une coupe, un zoom ou un
    jump cut touchent toute la hauteur du visage et au-delà)."""
    d = np.zeros(len(frames))
    for i in range(1, len(frames)):
        diff = np.abs(frames[i].astype(np.int16) - frames[i - 1])
        d[i] = diff.mean()
        stripes = diff[: diff.shape[0] // 8 * 8].reshape(8, -1).mean(axis=1)
        if (stripes > max(COUPE_PLANCHER, 0.5 * d[i])).sum() <= 2 and d[i] > 0:
            d[i] = min(d[i], float(np.median(stripes)))
    return d


def detect_cuts(d, fps):
    """Indices des premières images de chaque nouveau plan.

    Seuil adaptatif : une coupe (y compris jump cut sur face caméra ou punch-in) est un pic
    isolé d'une image ; la parole et les gestes donnent des différences étalées sur plusieurs images.
    """
    w, cuts = max(3, int(fps * 0.5)), []
    for i in range(1, len(d)):
        ctx = np.concatenate([d[max(1, i - w): max(1, i - 1)], d[i + 2: i + w + 1]])
        fond = np.median(ctx) if len(ctx) else 0.0
        voisins = max(d[i - 1] if i > 1 else 0.0, d[i + 1] if i + 1 < len(d) else 0.0)
        # fréquence variable (téléphone) ramenée à cadence fixe : image doublée (≈ 0) puis saut de 2 images de
        # mouvement ; ce saut n'est pas une coupe, il faut un pic bien plus fort
        doublon = i > 1 and d[i - 1] < 0.25 * max(fond, 0.4)
        if d[i] >= COUPE_PLANCHER and d[i] > COUPE_RATIO_FOND * fond * (2.5 if doublon else 1) and d[i] > COUPE_RATIO_VOISINS * voisins:
            if not cuts or (i - cuts[-1]) / fps >= COUPE_ECART_MIN_S:
                cuts.append(i)
    return cuts


def _zoomed(img, s, cx, cy):
    W, H = img.size
    w, h = W / s, H / s
    left = min(max(cx * W - w / 2, 0), W - w)
    top = min(max(cy * H - h / 2, 0), H - h)
    return np.asarray(img.resize((W, H), Image.BILINEAR, box=(left, top, left + w, top + h)), np.int16)


def zoom_between(a, b):
    """Facteur d'échelle de l'image a vers l'image b (>1 zoom avant, <1 arrière) et gain d'erreur.

    gain = erreur au meilleur zoom / erreur sans zoom (petit = le zoom explique bien le changement).
    """
    A, B = Image.fromarray(a), Image.fromarray(b)
    a16, b16 = a.astype(np.int16), b.astype(np.int16)
    ref = np.abs(a16 - b16).mean() + 1e-6
    best, scale, centre = ref, 1.0, (0.5, 0.5)
    for s in ZOOM_ECHELLES[1:]:
        for cx, cy in ZOOM_CENTRES:
            e_in = np.abs(_zoomed(A, s, cx, cy) - b16).mean()
            e_out = np.abs(_zoomed(B, s, cx, cy) - a16).mean()
            if e_in < best:
                best, scale, centre = e_in, s, (cx, cy)
            if e_out < best:
                best, scale, centre = e_out, 1 / s, (cx, cy)
    return float(scale), float(best / ref), centre


def _scale_at(a, b, cx, cy):
    """Échelle de a vers b à centre fixé (grille fine) : sert à suivre une rampe image par image."""
    A, B = Image.fromarray(a), Image.fromarray(b)
    a16, b16 = a.astype(np.int16), b.astype(np.int16)
    best, scale = np.abs(a16 - b16).mean(), 1.0
    for s in ZOOM_ECHELLES_FINES[1:]:
        e_in, e_out = np.abs(_zoomed(A, s, cx, cy) - b16).mean(), np.abs(_zoomed(B, s, cx, cy) - a16).mean()
        if e_in < best:
            best, scale = e_in, s
        if e_out < best:
            best, scale = e_out, 1 / s
    return scale


def _ramp(frames, a, b, total, centre):
    """(début, durée) de la rampe : temps (en images) entre 20 % et 80 % du zoom total, ramené à 0-100 %.
    Bissection (le zoom monte de façon monotone dans un plan)."""
    L = np.log(total)

    def cross(frac):
        lo, hi = a + 1, b - 2
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if np.log(_scale_at(frames[a + 1], frames[mid], *centre)) / L >= frac:
                hi = mid
            else:
                lo = mid
        return hi

    t20, t80 = cross(0.2), cross(0.8)
    return t20, max(t80 - t20, 1) / 0.6


def _is_zoom(scale, gain, seuil=ZOOM_GAIN):
    return max(scale, 1 / scale) >= ZOOM_MIN and gain < seuil


def zoom_kind(rampe_s, plan_s):
    """doux (poussée lente) | punch (rampe 12-18 images) | crash (rampe 4-6 images)."""
    if rampe_s >= RAMPE_DOUX_S or rampe_s >= 0.6 * plan_s:
        return "doux"
    return "crash" if rampe_s <= RAMPE_CRASH_S else "punch"


def zooms(frames, cuts, fps):
    """Zooms « cut » (changement d'échelle sec à une coupe) et zooms dans un plan, classés
    doux | punch | crash selon la durée de rampe. Chaque zoom : t, type, echelle, rampe_s, centre."""
    found = []
    for i in cuts:
        s, g, c = zoom_between(frames[i - 1], frames[i])
        if _is_zoom(s, g, ZOOM_GAIN_COUPE) and s > 1:  # on compte les punch-in ; le retour à 1.0 n'est pas un zoom de plus
            found.append({"t": round(i / fps, 3), "type": "cut", "echelle": round(s, 3), "rampe_s": 0.0,
                          "centre": list(c)})
    bounds = [0] + cuts + [len(frames)]
    for a, b in zip(bounds, bounds[1:]):
        if (b - a) / fps < 0.6:
            continue
        s, g, c = zoom_between(frames[a + 1], frames[b - 2])
        if _is_zoom(s, g):
            debut, rampe = _ramp(frames, a, b, s, c)
            rampe_s = rampe / fps
            found.append({"t": round(debut / fps, 3), "type": zoom_kind(rampe_s, (b - a) / fps),
                          "echelle": round(s, 3), "rampe_s": round(rampe_s, 3), "centre": list(c)})
    return sorted(found, key=lambda z: z["t"])


def zoom_fin(frames, cuts):
    """Le dernier plan est plus large que l'avant-dernier (dézoom sec à la dernière coupe) ?"""
    if not cuts:
        return False
    s, g, _ = zoom_between(frames[cuts[-1] - 1], frames[cuts[-1]])
    return _is_zoom(s, g, ZOOM_GAIN_COUPE) and s < 1


def audio(path, duration, peak=None):
    """Loudness intégrée (LUFS) et silences (-35 dB, 0,15 s). `peak` (liste) reçoit le true peak en dBTP."""
    out = run([ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
               f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_D},ebur128=framelog=verbose:peak=true",
               "-f", "null", "-"])
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    if peak is not None:
        tp = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", out)
        peak.append(float(tp[-1]) if tp and tp[-1] != "-inf" else None)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", out)]
    sil = [{"start": round(max(0.0, s), 3), "end": round(e, 3)}
           for s, e in zip(starts, ends + [duration] * (len(starts) - len(ends)))]
    return (float(lufs[-1]) if lufs else None), sil


def fond_sonore(path):
    """Niveau (dBFS) des 10 % de fenêtres de 50 ms les plus calmes : ~-180 si coupes sèches sans musique,
    -45..-30 avec une musique de fond. shortcut: un bruit de pièce très fort passerait pour de la musique."""
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                       capture_output=True)
    a = np.frombuffer(r.stdout, np.int16).astype(np.float64) / 32768
    n = len(a) // 400
    if not n:
        return None
    rms = np.sqrt((a[: n * 400].reshape(n, 400) ** 2).mean(axis=1)) + 1e-9
    return round(float(np.percentile(20 * np.log10(rms), 10)), 1)


def musique_tonalite(path):
    """Part d'énergie tonale (pics ≥ 8 dB au-dessus du spectre voisin, 80-3000 Hz) dans les 15 % de fenêtres de
    100 ms les plus calmes. Une musique (notes) donne > 0,01 ; le bruit de pièce d'un vrai tournage ~0, alors que
    son niveau (-30..-40 dBFS) le faisait passer pour une musique avec le seul seuil de niveau."""
    sr, n = 16000, 1600
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                       capture_output=True)
    a = np.frombuffer(r.stdout, np.int16).astype(np.float64) / 32768
    k = len(a) // n
    if k < 10:
        return None
    w = a[: k * n].reshape(k, n)
    rms = np.sqrt((w ** 2).mean(axis=1)) + 1e-9
    q = w[rms <= np.percentile(rms, 15)]
    spec = (np.abs(np.fft.rfft(q * np.hanning(n), axis=1)) ** 2).mean(axis=0)
    db = 10 * np.log10(spec + 1e-12)
    f = np.fft.rfftfreq(n, 1 / sr)
    sel = np.flatnonzero((f > 80) & (f < 3000))
    loc = np.array([np.median(db[max(0, i - 10):i + 11]) for i in sel])
    e = spec[sel]
    return round(float(e[db[sel] - loc > 8].sum() / max(e.sum(), 1e-30)), 3)


def _text_mask(frames):
    """Pixels « trait de sous-titre » : clairs, FINS (un mur blanc ou une chemise ne le sont pas), collés à un
    contour sombre, dans la bande 20-90 % ; les pixels présents sur ≥ 40 % des images (décor fixe : affiche,
    mur bordé d'un pull sombre) sont retirés. Mesuré sur de vrais entretiens : sans ces deux filtres, un décor
    clair derrière une personne en pull sombre passait pour des sous-titres."""
    h = frames.shape[1]
    band = frames[:, int(h * 0.2): int(h * 0.9)]
    bright, dark = band > 200, band < 60
    cv2 = _cv2()
    near = dark.copy()
    for dy in (-2, -1, 1, 2):  # dilatation du masque sombre de 2 px
        near |= np.roll(dark, dy, axis=1)
        near |= np.roll(dark, dy, axis=2)
    mask = bright & near
    if cv2 is not None:
        k = max(5, round(band.shape[2] / 50)) | 1  # plus épais qu'un trait de lettre (7 px à 360 de large)
        ker = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
        thick = np.array([cv2.morphologyEx(b.astype(np.uint8), cv2.MORPH_OPEN, ker) for b in bright], bool)
        mask &= ~thick
    if len(mask) >= 5:
        mask &= ~(mask.mean(axis=0) >= 0.4)
    return mask


def _caption_band(m):
    """(hauteur en lignes, début) de la plus longue suite de lignes contenant ≥ 4 pixels de texte."""
    on = m.sum(axis=1) >= 4
    best, start = (0, 0), None
    for y in range(len(on) + 1):
        if y < len(on) and on[y]:
            start = y if start is None else start
        elif start is not None:
            best = max(best, (y - start, start))
            start = None
    return best


def caption_frames(mask, h):
    """Images qui portent un bloc de sous-titres : texte concentré dans UNE bande horizontale (≥ 35 % des pixels
    de texte), assez haute (capitales ≥ 1,2 % de l'image) et centrée (centre entre 30 et 70 % de la largeur).
    shortcut: des sous-titres alignés à gauche ne sont pas vus ; ajouter un critère si une DA en a."""
    out = []
    for m in mask:
        n, y0 = _caption_band(m)
        tot = m.sum()
        if n < max(3, 0.012 * h) or not tot:
            out.append(False)
            continue
        xs = np.nonzero(m[y0:y0 + n])[1]
        out.append(len(xs) >= 0.35 * tot and 0.3 <= xs.mean() / m.shape[1] <= 0.7
                   and len(xs) >= SOUS_TITRES_SEUIL * m.size)
    return np.array(out, bool)


def caption_geometry(mask, h):
    """(hauteur, centre_y) relatifs du texte : plus longue suite de lignes de pixels « texte » par image,
    médiane sur les images sous-titrées. La hauteur ≈ hauteur des capitales (accents compris parfois)."""
    hs, ys = [], []
    for m in mask:
        n, y0 = _caption_band(m)
        if n >= 3:
            hs.append(n / h)
            ys.append((y0 + n / 2) / h + 0.2)
    return (round(statistics.median(hs), 4), round(statistics.median(ys), 3)) if hs else (None, None)


def caption_score(path, info, geometry=False):
    """Part de pixels « texte sous-titre » (clair bordé de sombre) dans la bande 20-90 % de hauteur.

    shortcut: heuristique contour noir + remplissage clair, rate les sous-titres sans contour ;
    passer à un OCR si ça devient un critère bloquant.
    """
    fps = min(2.0, max(0.5, 15 / max(info["duration"], 1)))
    frames = decode(path, 360, fps=fps, info=info)
    mask = _text_mask(frames)
    on = caption_frames(mask, frames.shape[1])
    score = float(on.mean()) if len(on) else 0.0
    if not geometry:
        return score
    return score, caption_geometry(mask[on], frames.shape[1])


# --- Image couleur : visages / b-roll, couleur, sous-titres colorés, emojis --------------------

def _cv2():
    try:
        import cv2
        return cv2
    except ImportError:
        return None


def face_detector():
    """Fonction image RGB → (cx, cy, largeur) relatifs du plus grand visage, ou None.
    YuNet (modèle MIT téléchargé par doctor.py) ; repli : cascade de Haar livrée avec OpenCV.
    Renvoie None si OpenCV est absent (b-roll et zooms « sur visage » non mesurés)."""
    cv2 = _cv2()
    if cv2 is None:
        return None
    if YUNET.is_file() and hasattr(cv2, "FaceDetectorYN"):
        det = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.7)

        def find(img):
            h, w = img.shape[:2]
            det.setInputSize((w, h))
            _, found = det.detect(np.ascontiguousarray(img[:, :, ::-1]))
            if found is None or not len(found):
                return None
            x, y, fw, fh = max(found, key=lambda f: f[2] * f[3])[:4]
            return (float((x + fw / 2) / w), float((y + fh / 2) / h), float(fw / w))
        return find
    haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    def find_haar(img):
        h, w = img.shape[:2]
        gray = cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_RGB2GRAY)
        mini = int(w * VISAGE_TAILLE_MIN)
        found = haar.detectMultiScale(gray, 1.15, 5, minSize=(mini, mini))
        if not len(found):
            return None
        x, y, fw, fh = max(found, key=lambda f: f[2] * f[3])
        return (float((x + fw / 2) / w), float((y + fh / 2) / h), float(fw / w))
    return find_haar


def faces(rgb, path=None, fps_c=None):
    """Un visage (cx, cy, taille = largeur relative) ou None par image de `rgb` ; None si aucun détecteur.
    Détections de reframe.raw_detections (lot C) quand dispo, sinon détecteur local sur les images."""
    if raw_detections is not None and path is not None and rgb is not None and len(rgb):
        try:
            got = raw_detections(path, 0.0, len(rgb) / fps_c)
        except Exception as e:  # lot C indisponible pour cette vidéo : repli local
            print(f"visages via reframe impossibles ({e}) : détecteur local")
            got = None
        if got:
            det, info = got
            ratio = info["height"] / info["width"]  # taille reframe = hauteur relative → largeur relative
            out = []
            for j in range(len(rgb)):
                f = det.get(round(j / fps_c * VISAGE_RATE)) or None
                f = (f[0], f[1], f[2] * ratio) if f else None
                out.append(f if f and f[2] >= VISAGE_TAILLE_MIN else None)
            return out
    find = face_detector()
    if find is None or rgb is None:
        return None
    out = []
    for img in rgb:
        f = find(img)
        out.append(f if f and f[2] >= VISAGE_TAILLE_MIN else None)
    return out


def broll_plans(plans, visages, fps_c):
    """Plans sans visage ≈ b-roll. None si aucun visage nulle part (détecteur aveugle sur cette vidéo :
    on ne conclut pas « 100 % b-roll »). shortcut: un b-roll avec une personne en gros plan compte comme visage."""
    if not visages or not any(visages):
        return None
    n, out = len(visages), []
    for a, b in plans:
        idx = [j for j in range(n) if a <= j / fps_c < b] or [min(n - 1, round((a + b) / 2 * fps_c))]
        if not any(visages[j] for j in idx):
            out.append([a, b])
    return out


def color_stats(path, rgb=None):
    """luma, contraste, saturation (chroma max-min), température (R-B) : reframe.measure_colour (lot C), le même
    code que la correction couleur du rendu. Repli (lot C absent) : même définition sur nos images."""
    if measure_colour is not None:
        try:
            return measure_colour(path)
        except Exception as e:
            print(f"couleur via reframe impossible ({e}) : calcul local")
    return _rgb_stats(rgb)


def _rgb_stats(rgb):
    if rgb is None or not len(rgb):
        return None
    h = rgb.shape[1]
    x = rgb[:, int(h * 0.1):int(h * 0.6), ::2].astype(np.float32) / 255
    y = x[..., 0] * 0.2126 + x[..., 1] * 0.7152 + x[..., 2] * 0.0722
    return {"luma": round(float(y.mean()), 4), "contraste": round(float(y.std()), 4),
            "saturation": round(float((x.max(-1) - x.min(-1)).mean()), 4),
            "temperature": round(float(x[..., 0].mean() - x[..., 2].mean()), 4)}


def _hsv(px):
    px = px.astype(np.float32)
    mx, mn = px.max(axis=-1), px.min(axis=-1)
    return mx, np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)


def _hex(rgb):
    return "#" + "".join(f"{int(round(float(v))):02X}" for v in rgb)


def _fill(px):
    """Couleur de remplissage du texte : médiane du quart le plus lumineux (les bords anticrénelés tirent vers le noir)."""
    px = px.reshape(-1, 3).astype(np.float32)
    mx = px.max(axis=1)
    return _hex(np.median(px[mx >= np.percentile(mx, 75)], axis=0))


def caption_colors(rgb, cap_y, cap_h):
    """Couleur principale du texte, couleur d'accent (mot-clé / mot actif), part des blocs avec un mot
    en couleur, surface relative du mot coloré. Pixels = clairs ou saturés, collés à un contour sombre,
    dans la bande des sous-titres. None si pas de sous-titres."""
    if rgb is None or cap_y is None or not len(rgb):
        return None
    H = rgb.shape[1]
    half = max(2 * (cap_h or 0.03), 0.04)
    y0, y1 = max(0, int((cap_y - half) * H)), min(H, int((cap_y + half) * H))
    band = rgb[:, y0:y1]
    gray = band.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    dark = gray < 60
    near = dark.copy()
    for dy in (-2, -1, 1, 2):
        near |= np.roll(dark, dy, axis=1)
        near |= np.roll(dark, dy, axis=2)
    mx, sat = _hsv(band)
    white = near & (sat < 0.2) & (mx > 200)
    # couleur d'accent = vive (jaune, rouge, vert, bleu de sous-titres : canal max > 200) ; la peau (#BA7C56) et
    # les bords d'une barbe passaient avant pour le « mot-clé » (mesuré sur de vrais visages)
    accent = near & (sat > 0.45) & (mx > 200)
    rows = white.sum(axis=2) >= 4  # lignes du texte blanc de chaque image : l'accent est sur la même ligne
    for j in range(len(rows)):
        ys = np.flatnonzero(rows[j])
        keep = np.zeros(rows.shape[1], bool)
        if len(ys):
            keep[max(0, ys.min() - 2):ys.max() + 3] = True
        accent[j] &= keep[:, None]
    seuil = max(20, 0.001 * band.shape[1] * band.shape[2])
    nw, na = white.sum(axis=(1, 2)), accent.sum(axis=(1, 2))
    texte = (nw + na) > seuil
    if texte.sum() < 2:
        return None
    avec = texte & (na >= np.maximum(20, 0.08 * (nw + na)))
    res = {"couleur": _fill(band[white]) if nw.sum() else None,
           "couleur_accent": None, "accent_part_blocs": round(float(avec.sum() / texte.sum()), 3),
           "accent_surface": None}
    if avec.any():
        px = band[avec][accent[avec]].astype(np.float32)
        mxp, mnp = px.max(axis=1), px.min(axis=1)
        d = np.maximum(mxp - mnp, 1e-6)
        r, g, b = px[:, 0], px[:, 1], px[:, 2]
        hue = np.where(mxp == r, ((g - b) / d) % 6, np.where(mxp == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
        bins = (hue // 20).astype(int)
        top = Counter(bins.tolist()).most_common(1)[0][0]
        res["couleur_accent"] = _fill(px[bins == top])
        res["accent_surface"] = round(float((na[avec] / (nw[avec] + na[avec])).mean()), 3)
    return res


def emoji_count(rgb, cap_y, cap_h, fps_c, visages=None):
    """Apparitions d'emojis : petites taches très colorées, compactes, au-dessus des sous-titres, qui restent
    ≤ 4 s. shortcut: un objet coloré fixe au-dessus des sous-titres peut passer pour un emoji s'il dure < 4 s."""
    cv2 = _cv2()
    if cv2 is None or rgb is None or cap_y is None or not len(rgb):
        return None
    W, H = rgb.shape[2], rgb.shape[1]
    top = max(0, int((cap_y - 0.25) * H))
    bottom = max(top + 1, int((cap_y - max(1.2 * (cap_h or 0.03), 0.02)) * H))
    present = []
    for j, img in enumerate(rgb[:, top:bottom]):
        mx, sat = _hsv(img)
        mask = ((sat > 0.6) & (mx > 200)).astype(np.uint8)  # emoji = couleurs vives (la peau n'en est pas)
        _, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        f = visages[j] if visages and j < len(visages) else None

        def on_face(c, f=f):  # tache sur le visage de l'orateur (joue, lèvres) : pas un emoji
            return f is not None and abs(c[0] / W - f[0]) < 0.75 * f[2] and \
                abs((c[1] + top) / H - f[1]) < 0.9 * f[2] * W / H
        present.append(any(0.04 * W <= bw <= 0.2 * W and 0.6 <= bw / max(bh, 1) <= 1.6 and area >= 0.45 * bw * bh
                           and not on_face(c) for (_, _, bw, bh, area), c in zip(stats[1:], cents[1:])))
    runs, k = 0, 0
    for on in present + [False]:
        if on:
            k += 1
        elif k:
            runs += 1 if k / fps_c <= 4.0 else 0
            k = 0
    return runs


# --- Parole : respiration, hook (depuis des mots horodatés dans le temps de la vidéo) -----------

def _gaps(words):
    """[(écart, fin_de_phrase)] entre mots consécutifs. Sans aucune ponctuation : écart ≥ 0,3 s = fin de phrase."""
    punct = any(w["w"].rstrip()[-1:] in FIN_PHRASE for w in words if w["w"].strip())
    out = []
    for a, b in zip(words, words[1:]):
        g = max(0.0, b["start"] - a["end"])
        out.append((g, a["w"].rstrip()[-1:] in FIN_PHRASE if punct else g >= 0.3))
    return out


def respiration(words, cuts=None):
    """Respiration d'un montage : pause médiane dans une phrase, entre phrases, délai coupe → mot suivant.
    Valeur = pacing.measure_breathing (lot B, le code du rendu) ; None quand < 3 mesures (pacing renverrait
    son défaut, qu'on ne doit pas confondre avec une mesure)."""
    if not words or len(words) < 3:
        return None
    g = _gaps(words)
    intra = [x for x, fin in g if not fin and PAUSE_MIN_S <= x <= 1.0]
    phrases = [x for x, fin in g if fin and 0.05 <= x <= 1.5]
    pre = []
    starts = sorted(w["start"] for w in words)
    for c in cuts or []:
        nxt = next((s for s in starts if s >= c - 0.01), None)
        if nxt is not None and nxt - c <= 0.4:
            pre.append(max(0.0, nxt - c))
    res = {k: round(statistics.median(v), 3) if len(v) >= 3 else None
           for k, v in (("respiration_intra_s", intra), ("respiration_phrases_s", phrases), ("coupe_avant_mot_s", pre))}
    if measure_breathing is not None:
        try:
            lot_b = measure_breathing({"words": words}, cuts)
            for k in res:
                if res[k] is not None and isinstance(lot_b.get(k), (int, float)):
                    res[k] = round(float(lot_b[k]), 3)
        except Exception as e:  # le lot B ne doit pas faire planter la mesure
            print(f"pacing.measure_breathing indisponible ({e}) : calcul local")
    return res


NEGATIONS = {"arrête", "arrêtez", "stop", "jamais", "personne", "rien", "pas", "aucun", "aucune", "pire", "faux"}
NOMBRES = {"un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "cent", "mille",
           "million", "millions", "%", "€", "$"}


def hook(words):
    """Première phrase du montage : durée, texte, type deviné (question | chiffre | choc | affirmation)."""
    if not words:
        return None
    first = [words[0]]
    for w, (gap, fin) in zip(words[1:], _gaps(words)):
        if fin or gap > 0.35:
            break
        first.append(w)
    texte = " ".join(w["w"].strip() for w in first)
    low = {t.strip(".,!?…«»\"'").lower() for t in texte.split()}
    if texte.rstrip().endswith("?"):
        kind = "question"
    elif any(c.isdigit() for c in texte) or NOMBRES & low:
        kind = "chiffre"
    elif NEGATIONS & low:
        kind = "choc"
    else:
        kind = "affirmation"
    return {"duree_s": round(first[-1]["end"], 2), "texte": texte, "type": kind}


def debit(words):
    """Débit de parole (mots/s) : mots hors hésitations / (fin du dernier − début du premier). None sous 3 mots."""
    ws = sorted((w for w in words or [] if not is_filler(w)), key=lambda w: w["start"])
    span = ws[-1]["end"] - ws[0]["start"] if len(ws) >= 3 else 0
    return round(len(ws) / span, 3) if span > 0 else None


def voix_sous_carton(words, fin):
    """On entend parler (≥ 2 mots hors hésitations dits en entier) pendant le 1er plan (carton ou plan d'accroche)."""
    return sum(1 for w in words or [] if not is_filler(w) and w["end"] <= fin) >= 2  # mots finis sous le carton


def _pcm(path, rate=8000):
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, np.int16).astype(np.float64) / 32768


def musique_sous_voix(path, words):
    """Niveau de la musique (≈ LUFS pour du mono) mesuré dans les trous entre mots ≥ 0,15 s.
    shortcut: la musique remonte souvent dans les pauses (ducking) ; surestime de 0-6 dB le niveau sous la voix."""
    if not words:
        return None
    a = _pcm(path)
    rms = []
    for x, y in zip(words, words[1:]):
        if y["start"] - x["end"] >= 0.15:
            seg = a[int((x["end"] + 0.03) * 8000): int((y["start"] - 0.03) * 8000)]
            for k in range(0, len(seg) - 399, 400):  # fenêtres de 50 ms
                rms.append(20 * np.log10(np.sqrt((seg[k:k + 400] ** 2).mean()) + 1e-9))
    # la musique est continue : son niveau = le plancher des trous (traînes de voix, souffles, bruitages au-dessus).
    # Mesuré sur des références à musique connue : la médiane surestimait de 7 dB.
    return round(float(np.percentile(rms, 20)), 1) if len(rms) >= 6 else None


def sfx(path, words):
    """Bruitages détectés par audio.detect_sfx (lot A) : [{"t", "type"}], ou None si le lot A est absent."""
    if detect_sfx is None:
        return None
    try:
        found = detect_sfx(path, words or [])
    except (Exception, SystemExit) as e:  # le lot A ne doit pas faire planter l'analyse
        print(f"bruitages non mesurés ({e})")
        return None
    return [{"t": round(float(x.get("at", x.get("t", 0))), 3), "type": x.get("type_guess", x.get("type", "inconnu"))}
            for x in found or [] if isinstance(x, dict)]


def carton_fin(words, plans):
    """Fin du carton d'ouverture muet (1er plan ≥ 1 s sans parole : aucun mot hors hésitation avant sa fin − 0,2 s),
    sinon 0. Le pro y pose ses bruitages ; on les entend nettement, hors voix."""
    if len(plans) < 2 or plans[0][1] < 1.0:
        return 0.0
    return plans[0][1] if not any(not is_filler(w) and w["start"] < plans[0][1] - 0.2 for w in words or []) else 0.0


def _near(visages, t, fps_c):
    if not visages:
        return None
    j = min(len(visages) - 1, max(0, int(t * fps_c)))
    return visages[j]


def zooms_sur_visage(z, visages, fps_c):
    """Part des zooms dont le centre tombe à ≤ 0,12 du visage (image juste avant le zoom). None si pas de visage."""
    ok = [((zz["centre"][0] - f[0]) ** 2 + (zz["centre"][1] - f[1]) ** 2) ** 0.5 <= 0.12
          for zz in z for f in [_near(visages, max(0.0, zz["t"] - 0.1), fps_c)] if f]
    return round(sum(ok) / len(ok), 3) if ok else None


def plan_stats(plans):
    """Distribution des durées de plans (le rythme d'un créateur n'est pas une moyenne : il alterne plans
    courts et longs, et coupe plus vite dans l'accroche) : quantiles 10-25-50-75-90 % et plan médian des 3 s."""
    lens = [b - a for a, b in plans]
    q = [round(float(x), 3) for x in np.percentile(lens, [10, 25, 50, 75, 90])] if lens else None
    hook = [b - a for a, b in plans if a < HOOK_S] or lens
    return q, (round(statistics.median(hook), 3) if hook else None)


def cadrage(plans, visages, fps_c, broll):
    """Taille du visage (largeur relative) et hauteur de son centre sur les plans face caméra.
    Taille de base = 30e centile des plans (les plans zoomés sont plus serrés : la base est le cadre large)."""
    if not visages or not any(visages):
        return None
    n, tailles, ys, bord = len(visages), [], [], []
    br = {tuple(x) for x in broll or []}
    for a, b in plans:
        if (a, b) in br or tuple([a, b]) in br:
            continue
        fs = [visages[j] for j in range(n) if a <= j / fps_c < b and visages[j]]
        if fs:
            tailles.append(statistics.median(f[2] for f in fs))
            ys += [f[1] for f in fs]
            # yeux ≈ ±35 % de la largeur de la boîte autour de son centre : au-delà du bord, un œil est coupé
            bord += [abs(f[0] - 0.5) + VISAGE_YEUX * f[2] > 0.5 for f in fs]
    if not tailles:
        return None
    return {"visage_largeur": round(float(np.percentile(tailles, 30)), 3), "visage_y": round(statistics.median(ys), 3),
            "visage_bord": round(sum(bord) / len(bord), 3)}


def images_noires(rgb):
    """Part des images quasi noires (luma moyenne < 0,04) : un trou dans le montage, sauf si la DA en a."""
    if rgb is None or not len(rgb):
        return None
    y = rgb.astype(np.float32).mean(axis=(1, 2, 3)) / 255
    return round(float((y < 0.04).mean()), 4)


def measure(path, words=None):
    """Toutes les mesures d'une vidéo, au format de profile.json → mesures_brutes.
    `words` = mots horodatés dans le temps de CETTE vidéo (transcript d'une référence, ou mots de la sortie
    reconstruits depuis edl.rendu.json) : active respiration, hook, musique sous la voix, bruitages."""
    info = probe(path)
    dur = info["duration"]
    fps = min(info.get("fps") or ANALYSE_FPS_MAX, ANALYSE_FPS_MAX)
    frames = decode(path, ANALYSE_LARGEUR, fps=fps, info=info)
    dur = dur or len(frames) / fps
    cuts = detect_cuts(frame_diffs(frames), fps)
    times = [0.0] + [i / fps for i in cuts] + [dur]
    plans = [[round(a, 3), round(b, 3)] for a, b in zip(times, times[1:])]
    lens = [b - a for a, b in plans]
    z = zooms(frames, cuts, fps)
    zf = zoom_fin(frames, cuts)
    del frames
    tp = []
    lufs, sil = audio(path, dur, tp) if info["has_audio"] else (None, [])
    inner = [s["end"] - s["start"] for s in sil if s["start"] > 0.05 and s["end"] < dur - 0.05]
    minutes = max(dur, 1e-6) / 60
    st_score, (st_h, st_y) = caption_score(path, info, geometry=True)

    fps_c = min(3.0, COULEUR_IMAGES_MAX / max(dur, 1e-6))
    rgb = decode(path, COULEUR_LARGEUR, fps=fps_c, gray=False, info=info)
    visages = faces(rgb, path, fps_c)
    br = broll_plans(plans, visages, fps_c)
    # la DA couleur = le look des plans face caméra : cartons et inserts graphiques (fond bleu nuit…) faussaient
    # la cible (température 0,12 au lieu de 0,20 sur la peau) et le rendu refroidissait le rush
    face = [j for j, f in enumerate(visages or []) if f]
    couleur = _rgb_stats(rgb[face]) if len(face) >= 3 else color_stats(path, rgb)
    noir = images_noires(rgb)
    cad = cadrage(plans, visages, fps_c, br)
    caps = caption_colors(rgb, st_y, st_h) if st_score >= 0.3 else None
    n_emo = emoji_count(rgb, st_y, st_h, fps_c, visages) if st_score >= 0.3 else None
    del rgb

    types = Counter(x["type"] for x in z)
    quant, hook_plan = plan_stats(plans)
    rampes = [x["rampe_s"] for x in z if x["type"] in ("punch", "crash")]
    m = {
        "duree": round(dur, 3),
        "format": {"width": info["width"], "height": info["height"], "fps": info["fps"]},
        "plans": plans,
        "duree_moyenne_plan_s": round(dur / len(plans), 3),
        "mediane_plan_s": round(statistics.median(lens), 3),
        "plans_quantiles": quant, "hook_plan_s": hook_plan,
        "coupes_par_minute": round(len(cuts) / minutes, 2),
        "zooms": z,
        "zooms_par_minute": round(len(z) / minutes, 2),
        "zoom_intensite": round(statistics.mean(x["echelle"] for x in z), 3) if z else None,
        "zoom_style": ("doux" if types["doux"] > len(z) / 2 else "cut") if z else None,
        "zoom_types": {k: round(v / len(z), 3) for k, v in types.items()} if z else {},
        "zoom_rampe_s": round(statistics.median(rampes), 3) if rampes else None,
        "zoom_sur_visage": zooms_sur_visage(z, visages, fps_c),
        "zoom_une_coupe_sur": round(len(plans) / len(z), 1) if z else None,
        "zoom_fin": zf,
        "lufs": lufs,
        "true_peak_db": tp[0] if tp else None,
        "silences": sil,
        "silence_max_s": round(max(inner, default=0.0), 3),
        "silence_part": round(sum(s["end"] - s["start"] for s in sil) / max(dur, 1e-6), 3),
        "sous_titres_presence": round(st_score, 3),
        "sous_titres_hauteur_pct": st_h,
        "sous_titres_position_y_pct": st_y,
        "sous_titres_couleurs": caps,
        "emojis_par_minute": round(n_emo / minutes, 2) if n_emo is not None else None,
        "fond_sonore_db": fond_sonore(path) if info["has_audio"] else None,
        "musique_tonalite": musique_tonalite(path) if info["has_audio"] else None,
        "couleur": couleur,
        "images_noires": noir,
        "cadrage": cad,
        "visage_part": round(sum(1 for f in visages if f) / len(visages), 3) if visages else None,
        "broll_plans": br,
        "broll_part": round(sum(b - a for a, b in br) / max(dur, 1e-6), 3) if br is not None else None,
        "broll_duree_moy_s": round(statistics.mean(b - a for a, b in br), 3) if br else None,
        "respiration": None, "hook": None, "musique_lufs": None, "sfx": None, "sfx_par_minute": None,
        "sfx_carton": None,
    }
    if words:
        # plus long silence = plus grand trou entre deux mots : le niveau seul (-35 dB) ne voit aucun silence
        # sous une musique ou un bruit de pièce (mesuré : 0 s partout sur de vrais entretiens)
        ws = sorted(words, key=lambda w: w["start"])
        trous = [{"start": round(a["end"], 3), "end": round(b["start"], 3)} for a, b in zip(ws, ws[1:])
                 if b["start"] - a["end"] >= SILENCE_D]
        m["silences_mots"] = trous
        m["silence_max_s"] = round(max((t["end"] - t["start"] for t in trous), default=0.0), 3)
        m["respiration"] = respiration(words, [i / fps for i in cuts])
        m["hook"] = hook(words)
        m["voix_sous_carton"] = voix_sous_carton(words, plans[0][1])
        # carton muet : la parole commence après lui, le débit se mesure hors carton
        m["debit_mots_s"] = debit(words if m["voix_sous_carton"] else [w for w in words if w["start"] >= plans[0][1] - 0.05])
        if info["has_audio"]:
            m["musique_lufs"] = musique_sous_voix(path, words)
            # sans mots, detect_sfx prendrait chaque début de phrase pour un bruitage. Tout ce qu'il trouve est hors
            # parole (fiable) ; sous le carton, une « hésitation » transcrite est souvent le bruitage lui-même
            fin = carton_fin(words, plans)
            m["sfx"] = sfx(path, [w for w in words if not (fin and is_filler(w) and w["end"] <= fin)])
            if m["sfx"] is not None:
                hors = [x for x in m["sfx"] if x["t"] >= fin]
                # bruitages/min hors carton : le carton en concentre une dizaine en 3 s, ce n'est pas le rythme du corps
                m["sfx_par_minute"] = round(len(hors) / max(dur - fin, 1e-6) * 60, 2)
                if fin and carton_appris is not None:
                    m["sfx_carton"] = carton_appris(path, [x for x in m["sfx"] if x["t"] < fin], fin)
    return m
