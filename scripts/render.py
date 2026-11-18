"""edl.json → vidéo finale mp4 (9:16, visage suivi, zooms, couleur, b-roll, sous-titres + emojis, son fini).

Étapes (mémoire bornée, quelle que soit la longueur des rushs) :
0. pacing.normalize_edl (lot B, si présent) puis recalage sur les mots (captions.snapped_segments) ;
   analyse : suivi du visage et mesure couleur par rush (reframe.py, en cache) ;
1. chaque segment / b-roll → fichier intermédiaire : recadrage 9:16 sur le visage, zoom image par image
   (doux | punch | crash, sans saccade : rééchantillonnage sous-pixel OpenCV), couleur, fondus audio 30 ms ;
2. en parallèle : vidéo (concat + b-roll + sous-titres .ass + emojis) et son (voix montée → audio.prepare_voice
   → audio.finish_mix, lot A ; repli v1 : musique duckée + loudnorm deux passes) ;
3. mux.
"""
import argparse
from fractions import Fraction
import json
import math
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import FF_THREADS, MUSIQUE_DB_DEFAUT, PARALLELE, die, ffmpeg, probe, run, tonemap  # noqa: E402
import captions  # noqa: E402
import reframe as rf  # noqa: E402

try:  # lots A et B : modules optionnels tant qu'ils ne sont pas livrés (repli = comportement v1)
    import pacing
except ImportError:
    pacing = None
try:
    import audio
except ImportError:
    audio = None

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIGNETTE, VIGNETTE_Y = 0.74, 0.60  # b-roll « vignette » : taille (part de la largeur), centre vertical
RAYON, OMBRE, GLISSE = 0.055, 0.04, 8  # coins arrondis, ombre portée (parts du côté), entrée glissée (images)
FONT = Path(__file__).parent / "fonts" / "Poppins-ExtraBold.ttf"
FADE = 0.03
EMOJI_DIR = Path(__file__).resolve().parent.parent / "assets" / "emoji"
EMOJI_URL = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/2D/png/128/"


TRANSITIONS = {"flash": ("#FFFFFF", 0.24), "noir": ("#000000", 0.3),  # type → (couleur par défaut, durée s)
               "fuite": ("#F2B36B", 0.3)}  # fuite de lumière chaude qui monte du bas


def transition_alpha(kind, d):
    """Filtre d'opacité du calque de transition : aplat qui monte et redescend (flash, noir) ; fuite = dégradé
    vertical qui monte du bas jusqu'à ~45 % de l'image puis retombe."""
    if kind == "fuite":
        s = f"sin(PI*min(T/{d:.3f},1))"
        return (f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                f"a='217*{s}*clip((Y-H*(1-0.45*{s}))/(0.35*H),0,1)'")
    return (f"colorchannelmixer=aa=0.9,fade=t=in:st=0:d={d / 2:.3f}:alpha=1,"
            f"fade=t=out:st={d / 2:.3f}:d={d / 2:.3f}:alpha=1")


def reframe(W, H, w, h, zoom=1.0, center=(0.5, 0.5)):
    """Filtre statique : recadrage « cover » w:h centré sur `center` (crop puis scale)."""
    x, y, cw, ch = rf.crop_box(W, H, w, h, max(zoom, 1.0), center)
    return f"crop={cw:.0f}:{ch:.0f}:{x:.0f}:{y:.0f},scale={w}:{h}:flags=lanczos"


def venc(draft, final=False):
    # intermédiaires quasi sans perte et rapides ; la qualité de livraison se joue à la finition
    preset, crf = ("ultrafast", "28") if draft else (("veryfast", "19") if final else ("ultrafast", "14"))
    return [*FF_THREADS, "-c:v", "libx264", "-preset", preset, "-crf", crf, "-profile:v", "high", "-pix_fmt", "yuv420p",
            # mêmes étiquettes sur tous les intermédiaires : sinon le concat reconfigure ses filtres et perd des images
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


# ---------- plan image par image ----------

def plan(seg, info, w, h, fps, track, kind, prev, rampe_s, use_face, cadrage=None):
    """Recadrage par image [(x, y, cw, ch)] (px source) + visage dans le cadre [(haut, bas) relatifs | None].
    `cadrage` (profil) : taille et hauteur du visage de la DA sur les plans face caméra."""
    W, H = info.get("width"), info.get("height")
    if not W:  # voix off (son seul) : fond noir, l'image vient des plans posés dessus
        return [(0, 0, w, h)] * seg["frames"], [None] * seg["frames"], 1.0
    bl, bt, br, bb = info.get("bandes") or (0, 0, 0, 0)  # bandes noires du média : jamais dans le cadre
    aw, ah = W - bl - br, H - bt - bb
    scales = rf.segment_scales(seg, kind, prev, fps, rampe_s)
    last = scales[-1]
    cad = cadrage if use_face and track else None
    base = rf.base_zoom(track, aw, ah, w, h, cad) if cad else 1.0
    if base != 1.0:
        scales = [z * base for z in scales]
    face_y = (cad or {}).get("visage_y")
    vmax = max(rf.VISAGE_MAX, 1.1 * ((cadrage or {}).get("visage_largeur") or 0))  # une DA plus serrée reste la sienne
    center = seg.get("zoom_center")
    fixed = tuple(center) if isinstance(center, (list, tuple)) else (0.5, 0.5)
    crops, faces = [], []
    for i, z in enumerate(scales):
        t = track[min(i, len(track) - 1)] if track else None
        cx, cy = (t[1], t[2]) if use_face and t else fixed
        if use_face and t and t[3] > 0:  # zoom (base × type) borné : le visage ne déborde pas au point de couper les yeux
            z = min(z, max(1.0, vmax * min(aw, ah * w / h) / (t[3] * H)))
        x, y, cw, ch = rf.crop_box(aw, ah, w, h, z, ((cx * W - bl) / aw, (cy * H - bt) / ah),
                                   face=use_face and bool(t), face_y=face_y)
        c = (x + bl, y + bt, cw, ch)
        crops.append(c)
        if t and t[3] > 0:
            x, y, cw, ch = c
            inside = x <= t[1] * W <= x + cw
            # boîte YuNet ≈ front → menton ; +8 % sous le menton pour le cou/la barbe
            faces.append((((t[2] - t[3] / 2) * H - y) / ch, ((t[2] + t[3] * 0.58) * H - y) / ch) if inside else None)
        else:
            faces.append(None)
    return crops, faces, last


def transitions_of(edl_r, brolls, fps):
    """Transitions à poser : edl `transitions` + b-roll `entree` (à son début) / `sortie` (à sa fin)."""
    trs = list(edl_r.get("transitions") or [])
    for b in brolls:
        for cle, at in (("entree", b["at"]), ("sortie", b["at"] + b["frames"] / fps)):
            if b.get(cle) in TRANSITIONS:
                coul = b.get(f"{cle}_couleur")
                trs.append({"at": round(at, 6), "type": b[cle], **({"couleur": coul} if coul else {})})
    return trs


def _even(n):
    return max(2, int(n) // 2 * 2)


def calques(cote, tmp, k, etiquette=None, w=1080):
    """PNG des habillages d'une vignette : masque à coins arrondis, ombre portée ; étiquette de lieu (pilule)."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    r, m = round(RAYON * cote), round(OMBRE * cote)
    masque = Image.new("L", (cote, cote), 0)
    ImageDraw.Draw(masque).rounded_rectangle((0, 0, cote - 1, cote - 1), radius=r, fill=255)
    ombre = Image.new("RGBA", (cote + 2 * m, cote + 2 * m), (0, 0, 0, 0))
    ImageDraw.Draw(ombre).rounded_rectangle((m, m + m // 3, m + cote, m + cote + m // 3), radius=r, fill=(0, 0, 0, 150))
    ombre = ombre.filter(ImageFilter.GaussianBlur(m / 2))
    out = {"masque": tmp / f"masque{k}.png", "ombre": tmp / f"ombre{k}.png"}
    masque.save(out["masque"])
    ombre.save(out["ombre"])
    if etiquette:
        f = ImageFont.truetype(str(FONT), round(w * 0.036))
        tw = round(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(etiquette, font=f))
        hh = round(f.size * 1.9)
        im = Image.new("RGBA", (tw + hh + hh // 2, hh), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, im.width - 1, hh - 1), radius=hh // 2, fill=(255, 255, 255, 235))
        cx, cy, pr = hh * 0.55, hh * 0.44, hh * 0.2  # épingle
        d.ellipse((cx - pr, cy - pr, cx + pr, cy + pr), fill=(232, 72, 60, 255))
        d.polygon([(cx - pr * 0.8, cy + pr * 0.5), (cx + pr * 0.8, cy + pr * 0.5), (cx, cy + pr * 2.1)], fill=(232, 72, 60, 255))
        d.ellipse((cx - pr * 0.4, cy - pr * 0.4, cx + pr * 0.4, cy + pr * 0.4), fill=(255, 255, 255, 255))
        d.text((hh * 0.95, hh / 2), etiquette, font=f, fill=(20, 20, 20, 255), anchor="lm")
        out["etiquette"] = tmp / f"etiquette{k}.png"
        im.save(out["etiquette"])
    return out


def masque_filtre(m, W, H):
    """b-roll `masque` [x, y, w, h] (fractions de l'image source) → « delogo=…, » en px, gardé à 1 px des bords
    (delogo refuse une zone qui touche le cadre) ; "" sans masque."""
    if not m:
        return ""
    x, y = max(1, round(m[0] * W)), max(1, round(m[1] * H))
    mw, mh = min(W - 1 - x, round(m[2] * W)), min(H - 1 - y, round(m[3] * H))
    return f"delogo=x={x}:y={y}:w={mw}:h={mh}," if mw > 0 and mh > 0 else ""


def fond_flou_filtre(crop, w, h, pad):
    """Plan entier (zone `crop` de la source) ajusté dans w×h, agrandi au plus d'AGRANDI_MAX, sur le même plan
    flouté et assombri en fond (paysage trop peu défini pour remplir le 9:16 sans flou)."""
    x, y, cw, ch = crop
    s = min(w / cw, h / ch, rf.AGRANDI_MAX)
    fw, fh = _even(cw * s), _even(ch * s)
    bw, bh = _even(w / 8), _even(h / 8)  # flou calculé petit puis agrandi : même rendu, bien plus rapide
    return (f"crop={cw:.0f}:{ch:.0f}:{x:.0f}:{y:.0f},split[ff][fb];"
            f"[fb]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},gblur=sigma=4,"
            f"colorchannelmixer=rr=0.6:gg=0.6:bb=0.6,scale={w}:{h}[fbg];[ff]scale={fw}:{fh}:flags=lanczos[ffg];"
            f"[fbg][ffg]overlay=(W-w)/2:(H-h)/2,{pad}")


def _input_args(src, seg, fps, image):
    dur = seg["frames"] / fps
    if image:
        return ["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 0.5:.6f}", "-i", str(src)]
    return [*FF_THREADS, "-ss", f"{seg['start']:.6f}", "-t", f"{dur + 0.5:.6f}", "-i", str(src)]


def render_segment(seg, src, out, w, h, fps, crops, colour, draft, with_audio=True, image=False):
    info = probe(src)
    dur = seg["frames"] / fps
    if "width" not in info:
        if not (with_audio and info.get("has_audio")):
            die(f"pas de piste vidéo dans {src}")
        # voix off (fichier son) : fond noir sous la voix, recouvert par les plans des rushs
        af = (f"aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{dur:.6f},"
              f"afade=t=in:d={FADE},afade=t=out:st={max(0, dur - FADE):.6f}:d={FADE}")
        run([ffmpeg(), "-y", "-v", "error", "-ss", f"{seg['start']:.6f}", "-t", f"{dur + 0.5:.6f}", "-i", str(src),
             "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={fps}", "-filter_complex",
             f"[1:v]setsar=1,format=yuv420p[v];[0:a]{af}[a]", "-map", "[v]", "-frames:v", str(seg["frames"]),
             "-r", str(fps), *venc(draft), "-map", "[a]", "-c:a", "pcm_s16le", str(out)])
        return
    # source très agrandie (rush horizontal recadré en vertical : ×1,8) : un peu de netteté sur la luminance
    net = ",unsharp=5:5:0.6:5:5:0" if not image and crops and w / max(1, crops[0][2]) > 1.4 else ""
    post = "setsar=1" + net + (f",{colour}" if colour else "") + ",format=yuv420p"
    af = (f"aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{dur:.6f},"
          f"afade=t=in:d={FADE},afade=t=out:st={max(0, dur - FADE):.6f}:d={FADE}")
    x0 = crops[0]
    # < 1 px de dérive sur tout le segment : invisible, on prend le chemin ffmpeg statique (bien plus rapide)
    moving = any(max(abs(a - b) for a, b in zip(c, x0)) > 1.0 for c in crops)
    try:
        import cv2  # noqa: F401
    except ImportError:
        moving = False  # shortcut: sans OpenCV, cadre fixe (celui du milieu du segment)
        x0 = crops[len(crops) // 2]
    # segment « muet » (question sous un carton muet) : silence à la place du son, la piste reste continue
    src_audio = with_audio and info["has_audio"] and not image and not seg.get("muet")
    silence = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"] if with_audio and not src_audio else []
    a_map = ["-map", "[a]", "-c:a", "pcm_s16le"] if with_audio else ["-an"]
    tail = ["-frames:v", str(seg["frames"]), "-r", str(fps), *venc(draft), *a_map, str(out)]
    if not moving:
        x, y, cw, ch = x0
        # tpad : un plan plus court que sa place à l'écran garde sa dernière image (jamais de trou noir)
        pad = f"tpad=stop_mode=clone:stop_duration={dur + 1:.3f}," if not with_audio else ""  # b-roll seulement
        msk = masque_filtre(seg.get("masque"), info["width"], info["height"])  # filigrane effacé avant recadrage
        vf = (f"fps={fps},{tonemap(info)}{msk}" + (fond_flou_filtre(x0, w, h, pad) if seg.get("fond_flou") else
                                                   f"crop={cw:.0f}:{ch:.0f}:{x:.0f}:{y:.0f},scale={w}:{h}:flags=lanczos,{pad}")
              + post)
        graph = f"[0:v]{vf}[v]" + (f";[{0 if src_audio else 1}:a]{af}[a]" if with_audio else "")
        run([ffmpeg(), "-y", "-v", "error", *_input_args(src, seg, fps, image), *silence,
             "-filter_complex", graph, "-map", "[v]", *tail])
        return
    # vidéo par l'entrée standard (entrée 0) : le son vient d'une 2e lecture du rush (entrée 1)
    a_args = ["-ss", f"{seg['start']:.6f}", "-t", f"{dur + 0.5:.6f}", "-i", str(src)] if src_audio else silence
    _render_warp(src, info, seg, crops, w, h, fps, post, af, a_args, tail, draft, image)


def _render_warp(src, info, seg, crops, w, h, fps, post, af, a_args, tail, draft, image):
    """Zoom/suivi image par image : décodage brut → cv2.warpAffine (sous-pixel, aucune saccade) → encodeur."""
    import cv2
    import numpy as np
    W, H = _even(info["width"]), _even(info["height"])
    kx, ky = W / info["width"], H / info["height"]
    dec = subprocess.Popen([ffmpeg(), "-v", "error", *_input_args(src, seg, fps, image),
                            "-vf", f"fps={fps},{masque_filtre(seg.get('masque'), info['width'], info['height'])}"
                            f"scale={W}:{H},{tonemap(info)}null", "-pix_fmt", "yuv420p",
                            "-f", "rawvideo", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    err = tempfile.TemporaryFile()
    graph = f"[0:v]{post}[v]" + (f";[1:a]{af}[a]" if a_args else "")
    enc = subprocess.Popen([ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}",
                            "-r", str(fps), "-i", "-", *a_args, "-filter_complex", graph, "-map", "[v]", *tail],
                           stdin=subprocess.PIPE, stderr=err)
    fsize, osize = W * H * 3 // 2, w * h * 3 // 2
    out = np.empty(osize, np.uint8)
    oy, ou, ov = out[:w * h].reshape(h, w), out[w * h:w * h * 5 // 4].reshape(h // 2, w // 2), \
        out[w * h * 5 // 4:].reshape(h // 2, w // 2)
    lin = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    flags = lin if draft else cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP
    frame = None
    try:
        for x, y, cw, ch in crops:
            buf = dec.stdout.read(fsize)
            if len(buf) == fsize:
                frame = np.frombuffer(buf, np.uint8)
            elif frame is None:
                die(f"aucune image lisible dans {src} à {seg['start']:.2f} s")
            x, y, cw, ch = x * kx, y * ky, cw * kx, ch * ky
            sx, sy = cw / w, ch / h
            fy = frame[:W * H].reshape(H, W)
            fu = frame[W * H:W * H * 5 // 4].reshape(H // 2, W // 2)
            fv = frame[W * H * 5 // 4:].reshape(H // 2, W // 2)
            m = np.float32([[sx, 0, x + 0.5 * sx - 0.5], [0, sy, y + 0.5 * sy - 0.5]])
            mc = np.float32([[sx, 0, x / 2 + 0.5 * sx - 0.5], [0, sy, y / 2 + 0.5 * sy - 0.5]])
            cv2.warpAffine(fy, m, (w, h), dst=oy, flags=flags, borderMode=cv2.BORDER_REPLICATE)
            # chroma à demi-résolution : le bilinéaire suffit (invisible), 2-3x plus rapide que le bicubique
            cv2.warpAffine(fu, mc, (w // 2, h // 2), dst=ou, flags=lin, borderMode=cv2.BORDER_REPLICATE)
            cv2.warpAffine(fv, mc, (w // 2, h // 2), dst=ov, flags=lin, borderMode=cv2.BORDER_REPLICATE)
            enc.stdin.write(out.data)
        enc.stdin.close()
    except BrokenPipeError:
        pass
    finally:
        dec.kill()
        dec.wait()
    if enc.wait() != 0:
        err.seek(0)
        die(f"commande échouée (ffmpeg, zoom image par image) :\n{err.read().decode('utf-8', 'replace')[-1500:]}")


# ---------- son ----------

def loudness(path, target):
    out = run([ffmpeg(), "-hide_banner", "-i", str(path), "-vn",
               "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    return json.loads(out[out.rindex("{"):out.rindex("}") + 1])


def build_audio(tmp, edl_r, profile, root, total, draft=False):
    """Piste son finale (wav) : voix montée → nettoyage → mix (lot A) ; repli v1 sans audio.py."""
    voice = tmp / "voix.wav"
    run([ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"), "-map", "0:a",
         "-c:a", "pcm_s16le", str(voice)])
    mix = tmp / "mix.wav"
    if audio is not None:
        # brouillon : on juge le montage, pas le son → pas de débruitage (le plus lent)
        mode = "aucun" if draft else (edl_r.get("audio") or {}).get("nettoyage_voix", "auto")
        clean = tmp / "voix_propre.wav"
        res = audio.prepare_voice(voice, clean, mode, profile) or {}
        audio.finish_mix(clean if clean.exists() else voice, edl_r, profile, root, mix)
        etat = (f"voix nettoyée ({res.get('methode')})" if res.get("applique") else
                "voix déjà propre, pas de débruitage" if mode == "auto" else "voix non nettoyée (demandé)")
        return mix, f"{etat}, mix audio.py"
    target = (profile.get("audio") or {}).get("lufs_cible", -14)
    pre = voice
    music = edl_r.get("music")
    if music and music.get("source"):
        msrc = (root / music["source"]).resolve()
        if not msrc.exists():
            die(f"musique introuvable : {msrc}")
        vol = music.get("volume_db", (profile.get("audio") or {}).get("musique_db_sous_voix") or MUSIQUE_DB_DEFAUT)
        pre = tmp / "pre.wav"
        run([ffmpeg(), "-y", "-v", "error", "-i", str(voice), "-stream_loop", "-1", "-i", str(msrc), "-filter_complex",
             f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol}dB,atrim=0:{total:.6f},"
             f"afade=t=out:st={max(0, total - 0.4):.6f}:d=0.4[mus];[0:a]asplit[voix][sc];"
             "[mus][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[duck];"
             "[voix][duck]amix=inputs=2:normalize=0:duration=first[a]", "-map", "[a]", "-c:a", "pcm_s16le", str(pre)])
    m = loudness(pre, target)
    try:
        ok = float(m.get("input_i")) > -70
    except (TypeError, ValueError):
        ok = False
    af = (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,"
          "aresample=48000") if ok else "aresample=48000"  # vidéo muette : rien à normaliser
    run([ffmpeg(), "-y", "-v", "error", "-i", str(pre), "-af", af, "-c:a", "pcm_s16le", str(mix)])
    return mix, "repli v1 (musique duckée + loudnorm)"


# ---------- emojis ----------

def sans_sous_titres(brolls, st, fps):
    """Intervalles sans sous-titres : `sous_titres` du b-roll (anim.py l'écrit d'après graphismes[] du profil) ;
    sinon un insert (« texte » : true) dit déjà la phrase et un plan filmé suit `sous_titres.sur_broll` (défaut oui)."""
    sur_broll = (st or {}).get("sur_broll", True) is not False
    return [(b["at"], b["at"] + b["frames"] / fps) for b in brolls
            if not b.get("sous_titres", sur_broll and not b.get("texte"))]


def luma_zone(path, t, haut, bas):
    """Luma moyenne 0-1 de la bande [haut, bas] (parts de la hauteur) de l'image à `t` s de `path` ; None sinon."""
    r = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1", "-vf",
                        f"crop=iw:ih*{max(0.01, bas - haut):.4f}:0:ih*{max(0.0, haut):.4f},scale=64:8,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    px = r.stdout
    return min(1.0, max(0.0, (sum(px) / len(px) - 16) / 219)) if px else None  # luma vidéo 16-235


def emoji_png(e):
    """PNG Noto (assets/emoji, OFL) d'un emoji ; téléchargé dans ~/.cache/monteur/emoji s'il manque ; None sinon."""
    name = "emoji_u" + "_".join(f"{ord(c):04x}" for c in e if ord(c) != 0xFE0F) + ".png"
    for d in (EMOJI_DIR, rf.CACHE / "emoji"):
        if (d / name).is_file():
            return d / name
    try:
        data = urllib.request.urlopen(EMOJI_URL + name, timeout=15).read()
        (rf.CACHE / "emoji").mkdir(parents=True, exist_ok=True)
        (rf.CACHE / "emoji" / name).write_bytes(data)
        return rf.CACHE / "emoji" / name
    except OSError:
        print(f"Emoji « {e} » introuvable (ni embarqué ni téléchargeable) : ignoré.")
        return None


# ---------- programme ----------

def main():
    ap = argparse.ArgumentParser(description="Monte la vidéo finale depuis .monteur/edl.json.")
    ap.add_argument("edl")
    ap.add_argument("--profil", help="ma-da/profile.json (défaut : ma-da/profile.json du dossier de travail)")
    ap.add_argument("--brouillon", action="store_true", help="aperçu rapide 540x960")
    a = ap.parse_args()

    t0 = lap = time.time()

    def stage(name):
        nonlocal lap
        now = time.time()
        print(f"  {name} : {now - lap:.1f} s", flush=True)
        lap = now

    edl_path = Path(a.edl)
    if not edl_path.exists():
        die(f"edl introuvable : {edl_path}")
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    if not edl.get("segments"):
        die("l'edl ne contient aucun segment")
    root = captions.root_of(edl_path)
    prof_path = Path(a.profil) if a.profil else root / "ma-da" / "profile.json"
    profile = json.loads(prof_path.read_text(encoding="utf-8")) if prof_path.exists() else {}

    fmt = edl.get("format") or profile.get("format") or {"width": 1080, "height": 1920, "fps": 30}
    edl["format"] = fmt
    if pacing is not None:
        edl = pacing.normalize_edl(edl, root, profile)
        edl["format"] = edl.get("format") or fmt
    fps = fmt["fps"]
    w, h = (540, 960) if a.brouillon else (fmt["width"], fmt["height"])
    zp = profile.get("zooms") or {}
    style, rampe_s = zp.get("style", "cut"), zp.get("rampe_s", 0.5)
    v2 = (edl.get("version") or 1) >= 2

    out = root / edl.get("output", "sorties/video.mp4")
    if a.brouillon:
        out = out.with_name(out.stem + "_brouillon.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)

    segs = captions.snapped_segments(edl, root)
    total_frames = sum(s["frames"] for s in segs)
    total = total_frames / fps
    edl_r = dict(edl, segments=segs)
    if pacing is None:  # sinon c'est pacing qui l'écrit
        (root / ".monteur").mkdir(exist_ok=True)
        (root / ".monteur" / "edl.rendu.json").write_text(json.dumps(edl_r, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
    for s in segs:
        if not (root / s["source"]).exists():
            die(f"rush introuvable : {root / s['source']}")
    brolls = []
    for k, b in enumerate(edl.get("broll") or []):
        src = (root / b["source"]).resolve()
        if not src.exists():
            die(f"b-roll introuvable : {src}")
        brolls.append(dict(b, start=b.get("source_start", 0), frames=max(1, round(b["duration"] * fps)), index=k,
                           image=src.suffix.lower() in IMAGE_EXT, path=src,
                           zoom_center=b.get("zoom_center", "visage" if v2 else [0.5, 0.5])))
        if brolls[-1]["image"] and "zoom" not in b and not b.get("texte"):  # photo : lente poussée (Ken Burns)
            brolls[-1].update(zoom=1.10, zoom_type="doux")
    stage("plan")

    # 0. analyse : visages (centrage + sous-titres hors visage) et couleur, en cache / en parallèle
    infos = {}
    inserts = {b["path"] for b in brolls if b.get("texte")}
    for p in {root / s["source"] for s in segs} | {b["path"] for b in brolls}:
        infos[p] = probe(p)
        # inserts à texte (anim.py, cartes) : graphisme plein cadre voulu, ses bords sombres ne sont pas des bandes
        if p.suffix.lower() not in IMAGE_EXT and infos[p].get("width") and p not in inserts:
            infos[p]["bandes"] = rf.active_area(p, infos[p])
            if any(infos[p]["bandes"]):
                print(f"{p.name} : bandes noires retirées du cadre (g/h/d/b px) {infos[p]['bandes']}")
    cache_dir = root / ".monteur"
    jobs = [(root / s["source"], s) for s in segs if infos[root / s["source"]].get("width")] +         [(b["path"], b) for b in brolls if not b["image"]]
    with ThreadPoolExecutor(PARALLELE) as pool:
        tracks = list(pool.map(lambda j: rf.face_track(j[0], j[1]["start"], j[1]["start"] + j[1]["frames"] / fps,
                                                       fps, cache_dir=cache_dir), jobs))
    track_of = {id(s): t for (_, s), t in zip(jobs, tracks)}
    stage("analyse (visages)")

    plans, prev, faces = [], 1.0, []
    rf.doux_u(segs, style)
    for s in segs:
        kind = s.get("zoom_type") or ("doux" if style == "doux" and s.get("zoom", 1.0) > 1.0 else None)
        use_face = s.get("zoom_center") == "visage" or (v2 and "zoom_center" not in s)
        crops, f, prev = plan(s, infos[root / s["source"]], w, h, fps, track_of.get(id(s)), kind, prev, rampe_s, use_face,
                              profile.get("cadrage"))
        plans.append(crops)
        faces += f
    bplans = []
    for b in brolls:
        # vignette : carré à 84 % de la largeur posé sur l'orateur (le visage reste visible au-dessus)
        b["wh"] = (_even(VIGNETTE * w),) * 2 if b.get("vignette") else (w, h)
        inf = infos[b["path"]]
        if inf.get("width") and not b.get("texte"):
            bl, bt, br_, bb = inf.get("bandes") or (0, 0, 0, 0)
            aw, ah = inf["width"] - bl - br_, inf["height"] - bt - bb
            wh = (b["wh"][0] * fmt["width"] / w, b["wh"][1] * fmt["height"] / h)  # jugé en définition finale
            if not b["image"]:  # photo : la lente poussée (Ken Burns) reste un mouvement voulu
                voulu = isinstance(edl["broll"][b["index"]].get("zoom_center"), (list, tuple))
                z = rf.zoom_borne(aw, ah, *wh, b.get("zoom"), voulu)
                if z < (b.get("zoom") or 1.0) - 1e-3:
                    print(f"{b['path'].name} : zoom {b['zoom']} ramené à {z:.2f} (au-delà, plan agrandi : flou)")
                    b["zoom"] = z
                    if z <= 1.0:
                        b["zoom_type"] = None
            ag = rf.agrandissement(aw, ah, *wh, b.get("zoom"), b.get("fond_flou"))
            if ag > rf.AGRANDI_MAX + 0.01:
                print(f"{b['path'].name} à {b['at']:.1f} s : plan agrandi ×{ag:.2f} (flou) → \"fond_flou\": true "
                      "(plan entier sur fond flou) ou un plan plus défini")
            if b.get("fond_flou"):  # plan entier, cadre fixe : zone `zoom` autour de zoom_center, au ratio source
                c = b["zoom_center"] if isinstance(b["zoom_center"], (list, tuple)) else (0.5, 0.5)
                x, y, cw, ch = rf.crop_box(aw, ah, aw, ah, max(b.get("zoom") or 1.0, 1.0), c)
                bplans.append([(x + bl, y + bt, cw, ch)] * b["frames"])
                continue
        crops, _, _ = plan(b, infos[b["path"]], *b["wh"], fps, track_of.get(id(b)), b.get("zoom_type"), 1.0, rampe_s,
                           b["zoom_center"] == "visage" and not b["image"])
        bplans.append(crops)
    colours = {}
    target = profile.get("couleur")
    if edl.get("couleur") == "auto" and target:
        # cadre réellement à l'écran par média (médiane des cadres) : un rush 16:9 recadré en 9:16 se mesure
        # à travers ce cadre, comme les références 9:16 du créateur
        spans = {}
        for item, crops in [(s, c) for s, c in zip(segs, plans)] + [(b, c) for b, c in zip(brolls, bplans)]:
            p = item["path"] if "path" in item else root / item["source"]
            if not infos[p].get("width") or p in inserts:
                continue  # voix off : pas d'image ; insert graphique : ses couleurs sont voulues (logo sur blanc)
            W, H = infos[p]["width"], infos[p]["height"]
            spans.setdefault(p, []).extend((x / W, y / H, (x + cw) / W, (y + ch) / H) for x, y, cw, ch in crops[::5])
        boxes = {p: tuple(statistics.median(c[k] for c in v) for k in range(4)) for p, v in spans.items()}
        with ThreadPoolExecutor(PARALLELE) as pool:
            ms = dict(zip(boxes, pool.map(lambda p: rf.measure_colour(p, box=boxes[p]), boxes)))
        # mesure impossible (None) → colour_filter rend "" : ce média reste tel quel
        # plans filmés en b-roll : calés sur le rush face caméra tel qu'il sortira corrigé (luma, température), pas
        # sur la moyenne de la DA (une boîte de nuit bleue au milieu de rushs chauds sautait aux yeux)
        rushs = {root / s["source"] for s in segs} & {p for p, m in ms.items() if m}
        apres = [rf.colour_plan(ms[p], target)[1] for p in rushs]
        cal = rf.pooled_colour(apres, [1] * len(apres))
        colours = {p: rf.colour_filter(m, target if p in rushs or not cal else
                                       dict(target, luma=cal["luma"], temperature=cal["temperature"]))
                   for p, m in ms.items()}
        if any(m is None for m in ms.values()):
            print("couleur auto : certains médias sans image mesurable, laissés tels quels.")
    elif edl.get("couleur") == "auto":
        print("couleur auto : profile.couleur absent, rien appliqué.")
    for b in brolls:  # b-roll plein écran : pas de visage à éviter ; vignette : les sous-titres l'évitent
        i0 = round(b["at"] * fps)
        demi = b["wh"][0] / 2 / h
        zone = (VIGNETTE_Y - demi, VIGNETTE_Y + demi) if b.get("vignette") else None
        zone = tuple(b["evite"]) if b.get("evite") else zone  # insert : carte de la mosaïque (haut, bas en part de h)
        faces[i0:i0 + b["frames"]] = [zone] * len(faces[i0:i0 + b["frames"]])

    tmp = Path(tempfile.mkdtemp(prefix="monteur_"))
    try:
        # 1. segments et b-roll
        names = [f"seg{s['index']:04d}.mkv" for s in segs]
        bnames = [f"broll{b['index']:03d}.mkv" for b in brolls]
        with ThreadPoolExecutor(PARALLELE) as pool:  # x264 est déjà multithread
            futs = [pool.submit(render_segment, s, root / s["source"], tmp / n, w, h, fps, c,
                                colours.get(root / s["source"], ""), a.brouillon) for s, n, c in zip(segs, names, plans)]
            futs += [pool.submit(render_segment, b, b["path"], tmp / n, *b["wh"], fps, c, colours.get(b["path"], ""),
                                 a.brouillon, False, b["image"]) for b, n, c in zip(brolls, bnames, bplans)]
            for f in futs:
                f.result()
        (tmp / "list.txt").write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
        stage(f"{len(segs)} segments + {len(brolls)} b-roll rendus")

        # 2. son (fil parallèle) pendant la finition vidéo
        res = {}

        def sound():
            try:
                res["mix"] = build_audio(tmp, edl_r, profile, root, total, a.brouillon)
            except SystemExit as e:  # die() dans le fil : on le relaie au fil principal
                res["err"] = e
            except Exception as e:  # noqa: BLE001
                res["err"] = e
        th = threading.Thread(target=sound)
        th.start()
        t_audio = time.time()

        args = [ffmpeg(), "-y", "-v", "error", "-reinit_filter", "0", "-f", "concat", "-safe", "0", "-i", "list.txt"]
        fr = Fraction(fps).limit_denominator(1001)
        tb = f"settb={fr.denominator}/{fr.numerator}"  # une unité de temps = une image (29,97 compris)
        graph, v, n = [f"[0:v]{tb},setpts=N[base]"], "[base]", 1
        if colours:
            # boucle fermée : on mesure les plans tels qu'ils seront vus (zooms, cadrage compris) et on corrige
            # l'écart restant à la DA en une passe globale (la correction par média, bornée, n'y arrive pas seule)
            # ce qui est à l'écran : chaque segment pour le temps où aucun b-roll ne le recouvre, et les b-roll
            vus, poids, off = [], [], 0.0
            for s, nm in zip(segs, names):
                d = s["frames"] / fps
                cache = sum(max(0.0, min(off + d, b["at"] + b["frames"] / fps) - max(off, b["at"])) for b in brolls)
                if d - cache > 1 / fps and infos[root / s["source"]].get("width"):
                    vus.append(tmp / nm)
                    poids.append(d - cache)
                off += d
            vus += [tmp / bn for b, bn in zip(brolls, bnames) if not b.get("texte")]
            poids += [b["frames"] / fps for b in brolls if not b.get("texte")]
            with ThreadPoolExecutor(PARALLELE) as pool:
                st_segs = list(pool.map(lambda f: rf.measure_colour(f, n=3), vus))
            vu = rf.pooled_colour(st_segs, poids)
            reste = rf.colour_filter(vu, target)
            print(f"  couleur à l'écran {vu} → cible {target} : correction finale « {reste or 'aucune'} »")
        else:
            reste = ""
        if reste:
            graph.append(f"{v}{reste}[basec]")
            v = "[basec]"
        trans = (profile.get("broll") or {}).get("transition", "coupe")
        for k, (b, bn) in enumerate(zip(brolls, bnames)):
            args += ["-i", bn]
            d = b["frames"] / fps
            fade = ""
            if b.get("transition", trans) == "fondu":  # fondu enchaîné court (3 images)
                fd = 3 / fps
                fade = (f"format=yuva420p,fade=t=in:st=0:d={fd:.4f}:alpha=1,"
                        f"fade=t=out:st={d - fd:.4f}:d={fd:.4f}:alpha=1,")
            # temps en numéros d'image (base de temps 1/fps) : les instants des fichiers intermédiaires (mkv, 1 ms)
            # sont arrondis ; comparés en secondes, la 1re ou la dernière image d'un b-roll sautait (noir en voix off,
            # visage de l'orateur ailleurs)
            f0 = round(b["at"] * fps)
            en = f"enable='between(n,{f0},{f0 + b['frames'] - 1})'"
            # correction finale sur les images filmées seulement : un insert graphique garde ses couleurs
            cc = f"{reste}," if reste and not b.get("texte") else ""
            graph.append(f"[{n}:v]{cc}{fade}{tb},setpts=N+{f0}[b{k}]")
            n += 1
            lieu = b.get("etiquette")
            if b.get("vignette"):  # coins arrondis + ombre, sous le visage ; entre en glissant vers le haut
                cote = b["wh"][0]
                cq = calques(cote, tmp, k, lieu, w)
                boucle = ["-loop", "1", "-framerate", str(fps), "-t", f"{d + 0.5:.3f}", "-i"]
                args += [*boucle, cq["masque"].name, *boucle, cq["ombre"].name]
                x0, y0, m = (w - cote) // 2, round(VIGNETTE_Y * h - cote / 2), round(OMBRE * cote)
                glisse = f"{y0}+{round(0.03 * h)}*pow(max(0,1-(n-{f0})/{GLISSE}),2)"
                apparait = f"fade=t=in:st={f0 / fps:.4f}:d={GLISSE / fps:.4f}:alpha=1"
                graph.append(f"[{n}:v]format=gray,{tb},setpts=N+{f0}[m{k}]")
                graph.append(f"[b{k}]format=yuva420p[ba{k}];[ba{k}][m{k}]alphamerge,{apparait}[bm{k}]")
                graph.append(f"[{n + 1}:v]format=rgba,{tb},setpts=N+{f0},{apparait}[o{k}]")
                graph.append(f"{v}[o{k}]overlay=eof_action=pass:eval=frame:x={x0 - m}:y='{glisse}-{m}':{en}[vo{k}]")
                graph.append(f"[vo{k}][bm{k}]overlay=eof_action=pass:eval=frame:x={x0}:y='{glisse}':{en}[v{k}]")
                n += 2
                lx, ly = x0 + round(0.04 * cote), y0 + cote - round(0.04 * cote)  # coin bas gauche, à cheval
            else:
                graph.append(f"{v}[b{k}]overlay=eof_action=pass:{en}[v{k}]")
                cq = calques(64, tmp, k, lieu, w) if lieu else {}
                lx, ly = round(0.06 * w), round(0.76 * h)
            v = f"[v{k}]"
            if lieu:  # étiquette de lieu, posée un temps après l'image
                f1 = f0 + min(GLISSE + 4, b["frames"] // 3)
                args += ["-loop", "1", "-framerate", str(fps), "-t", f"{d + 0.5:.3f}", "-i", cq["etiquette"].name]
                graph.append(f"[{n}:v]format=rgba,{tb},setpts=N+{f0},"
                             f"fade=t=in:st={f1 / fps:.4f}:d={6 / fps:.4f}:alpha=1[e{k}]")
                graph.append(f"{v}[e{k}]overlay=eof_action=pass:x={lx}:y={ly}-overlay_h/2:"
                             f"enable='between(n,{f1},{f0 + b['frames'] - 1})'[ve{k}]")
                v, n = f"[ve{k}]", n + 1

        st = profile.get("sous_titres")
        fw, fh = fmt["width"], fmt["height"]

        def face_at(t_a, t_b):
            boxes = [f for f in faces[max(0, int(t_a * fps)):max(1, math.ceil(t_b * fps))] if f]
            return (min(f[0] for f in boxes) * fh, max(f[1] for f in boxes) * fh) if boxes else None

        words = captions.timeline_words(segs, root) if edl.get("captions", True) else []
        cartes = sans_sous_titres(brolls, st, fps)
        words = [w for w in words if not any(a <= captions.mi(w) < z for a, z in cartes)]
        if words or edl.get("titles"):
            def luma_at(t_a, t_b, haut, bas):
                """Luma derrière un bloc : le plan à l'écran à son milieu (b-roll plein cadre, sinon segment), lu dans
                les intermédiaires déjà rendus. shortcut: vignette et correction finale ignorées."""
                t = (t_a + t_b) / 2
                for b, bn in reversed(list(zip(brolls, bnames))):
                    if not b.get("vignette") and b["at"] <= t < b["at"] + b["frames"] / fps:
                        return luma_zone(tmp / bn, t - b["at"], haut, bas)
                off = 0.0
                for s, nm in zip(segs, names):
                    if t < off + s["frames"] / fps:
                        return luma_zone(tmp / nm, t - off, haut, bas)
                    off += s["frames"] / fps
                return None

            ass = captions.build_ass(words, st, fw, fh, edl.get("titles") or [], face_at=face_at, caches=cartes,
                                     luma_at=luma_at)
            (tmp / "captions.ass").write_text(ass, encoding="utf-8")
            shutil.copytree(captions.FONTS_DIR, tmp / "fonts", dirs_exist_ok=True)
            # chemins relatifs (cwd = tmp) : évite l'échappement des « C:\ » dans les filtres
            graph.append(f"{v}ass=captions.ass:fontsdir=fonts[vass]")
            v = "[vass]"
        k = w / fw
        emojis = captions.emoji_overlays(words, st, fw, fh, face_at=face_at) if words else []
        for j, e in enumerate(emojis):
            png = emoji_png(e["emoji"])
            if not png:
                continue
            d = e["end"] - e["start"]
            args += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", str(png)]
            px, pop = e["size"] * k, captions.POP_MS / 1000
            graph.append(f"[{n}:v]format=rgba,scale=w='{px:.1f}*min(1,0.8+0.2*t/{pop})':h=-1:eval=frame:flags=bicubic,"
                         f"setpts=PTS-STARTPTS+{e['start']:.3f}/TB[e{j}]")
            graph.append(f"{v}[e{j}]overlay=x='{e['x'] * k:.1f}-w/2':y='{e['y'] * k:.1f}-h/2':eval=frame:"
                         f"eof_action=pass:enable='between(t,{e['start']:.3f},{e['end']:.3f})'[ve{j}]")
            v, n = f"[ve{j}]", n + 1

        # transitions (flash, noir) : par-dessus tout, sous-titres compris ; edl `transitions` + b-roll `entree`
        trs = transitions_of(edl_r, brolls, fps)
        for j, t in enumerate(trs):
            if t.get("type") not in TRANSITIONS:
                print(f"transition « {t.get('type')} » inconnue (connues : {', '.join(TRANSITIONS)}) : ignorée")
                continue
            d = float(t.get("duree", TRANSITIONS[t["type"]][1]))
            col = t.get("couleur") or TRANSITIONS[t["type"]][0]
            ta = max(0.0, float(t["at"]) - d / 2)  # centrée sur la coupe : moitié avant, moitié après
            graph.append(f"color=c={col}:s={w}x{h}:r={fps}:d={d:.3f},format=rgba,{transition_alpha(t['type'], d)},"
                         f"setpts=PTS+{ta:.3f}/TB[tr{j}]")
            graph.append(f"{v}[tr{j}]overlay=eof_action=pass:enable='between(t,{ta:.3f},{ta + d:.3f})'[vt{j}]")
            v = f"[vt{j}]"

        args += ["-filter_complex", ";".join(graph), "-map", v, "-an", "-t", f"{total:.6f}",
                 "-r", str(fps), *venc(a.brouillon, final=True), "-video_track_timescale", "90000", "video.mp4"]
        # chaque entrée (segments, b-roll, emojis) a son décodeur : sans limite, 12 cœurs = 12 fils et leurs images chacun
        args = [x for y in args for x in ([*FF_THREADS, y] if y == "-i" else [y])]
        args[1:1] = ["-filter_complex_threads", FF_THREADS[1]]
        run_in(args, tmp)
        stage(f"finition vidéo (b-roll, sous-titres, {len(emojis)} emojis)")
        th.join()
        if "err" in res:
            raise res["err"]
        mix, how = res["mix"]
        print(f"  son : {how} (fini {max(0, time.time() - t_audio):.1f} s après le début de la finition)")

        # 3. mux
        run([ffmpeg(), "-y", "-v", "error", "-i", str(tmp / "video.mp4"), "-i", str(mix), "-map", "0:v", "-map", "1:a",
             "-video_track_timescale", "90000",  # mkv (1 ms) arrondissait les instants : 29,999 i/s au lieu de 30
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total:.6f}",
             "-movflags", "+faststart", str(out)])
        stage("mux")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    dt = time.time() - t0
    d = probe(out)["duration"]
    print(f"Vidéo : {out}\nDurée : {d:.2f} s ({len(segs)} segments, {w}x{h} @ {fps} fps)\n"
          f"Rendu : {dt:.1f} s ({d / dt:.2f}x temps réel)")


def run_in(args, cwd):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        die(f"commande échouée (ffmpeg) :\n{r.stderr[-1500:]}")


if __name__ == "__main__":
    main()
