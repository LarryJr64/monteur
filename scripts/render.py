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
from common import die, ffmpeg, probe, run  # noqa: E402
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
FADE = 0.03
EMOJI_DIR = Path(__file__).resolve().parent.parent / "assets" / "emoji"
EMOJI_URL = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/2D/png/128/"


def reframe(W, H, w, h, zoom=1.0, center=(0.5, 0.5)):
    """Filtre statique : recadrage « cover » w:h centré sur `center` (crop puis scale)."""
    x, y, cw, ch = rf.crop_box(W, H, w, h, max(zoom, 1.0), center)
    return f"crop={cw:.0f}:{ch:.0f}:{x:.0f}:{y:.0f},scale={w}:{h}:flags=lanczos"


def venc(draft, final=False):
    # intermédiaires quasi sans perte et rapides ; la qualité de livraison se joue à la finition
    preset, crf = ("ultrafast", "28") if draft else (("veryfast", "19") if final else ("ultrafast", "14"))
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-profile:v", "high", "-pix_fmt", "yuv420p"]


# ---------- plan image par image ----------

def plan(seg, info, w, h, fps, track, kind, prev, rampe_s, use_face):
    """Recadrage par image [(x, y, cw, ch)] (px source) + visage dans le cadre [(haut, bas) relatifs | None]."""
    W, H = info["width"], info["height"]
    bl, bt, br, bb = info.get("bandes") or (0, 0, 0, 0)  # bandes noires du média : jamais dans le cadre
    aw, ah = W - bl - br, H - bt - bb
    scales = rf.segment_scales(seg, kind, prev, fps, rampe_s)
    center = seg.get("zoom_center")
    fixed = tuple(center) if isinstance(center, (list, tuple)) else (0.5, 0.5)
    crops, faces = [], []
    for i, z in enumerate(scales):
        t = track[min(i, len(track) - 1)] if track else None
        cx, cy = (t[1], t[2]) if use_face and t else fixed
        x, y, cw, ch = rf.crop_box(aw, ah, w, h, z, ((cx * W - bl) / aw, (cy * H - bt) / ah),
                                   face=use_face and bool(t))
        c = (x + bl, y + bt, cw, ch)
        crops.append(c)
        if t and t[3] > 0:
            x, y, cw, ch = c
            inside = x <= t[1] * W <= x + cw
            # boîte YuNet ≈ front → menton ; +8 % sous le menton pour le cou/la barbe
            faces.append((((t[2] - t[3] / 2) * H - y) / ch, ((t[2] + t[3] * 0.58) * H - y) / ch) if inside else None)
        else:
            faces.append(None)
    return crops, faces, scales[-1]


def _even(n):
    return max(2, int(n) // 2 * 2)


def _input_args(src, seg, fps, image):
    dur = seg["frames"] / fps
    if image:
        return ["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 0.5:.6f}", "-i", str(src)]
    return ["-ss", f"{seg['start']:.6f}", "-t", f"{dur + 0.5:.6f}", "-i", str(src)]


def render_segment(seg, src, out, w, h, fps, crops, colour, draft, with_audio=True, image=False):
    info = probe(src)
    if "width" not in info:
        die(f"pas de piste vidéo dans {src}")
    dur = seg["frames"] / fps
    post = "setsar=1" + (f",{colour}" if colour else "") + ",format=yuv420p"
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
    src_audio = with_audio and info["has_audio"] and not image
    silence = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"] if with_audio and not src_audio else []
    a_map = ["-map", "[a]", "-c:a", "pcm_s16le"] if with_audio else ["-an"]
    tail = ["-frames:v", str(seg["frames"]), "-r", str(fps), *venc(draft), *a_map, str(out)]
    if not moving:
        x, y, cw, ch = x0
        vf = f"fps={fps},crop={cw:.0f}:{ch:.0f}:{x:.0f}:{y:.0f},scale={w}:{h}:flags=lanczos,{post}"
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
                            "-vf", f"fps={fps},scale={W}:{H}", "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"],
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
        return mix, f"voix {res.get('methode', mode) if res.get('applique') else 'non nettoyée'}, mix audio.py"
    target = (profile.get("audio") or {}).get("lufs_cible", -14)
    pre = voice
    music = edl_r.get("music")
    if music and music.get("source"):
        msrc = (root / music["source"]).resolve()
        if not msrc.exists():
            die(f"musique introuvable : {msrc}")
        vol = music.get("volume_db", (profile.get("audio") or {}).get("musique_db_sous_voix", -18))
        pre = tmp / "pre.wav"
        run([ffmpeg(), "-y", "-v", "error", "-i", str(voice), "-stream_loop", "-1", "-i", str(msrc), "-filter_complex",
             f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol}dB,atrim=0:{total:.6f},"
             f"afade=t=out:st={max(0, total - 1):.6f}:d=1[mus];[0:a]asplit[voix][sc];"
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
    stage("plan")

    # 0. analyse : visages (centrage + sous-titres hors visage) et couleur, en cache / en parallèle
    infos = {}
    for p in {root / s["source"] for s in segs} | {b["path"] for b in brolls}:
        infos[p] = probe(p)
        if p.suffix.lower() not in IMAGE_EXT:
            infos[p]["bandes"] = rf.active_area(p, infos[p])
            if any(infos[p]["bandes"]):
                print(f"{p.name} : bandes noires retirées du cadre (g/h/d/b px) {infos[p]['bandes']}")
    cache_dir = root / ".monteur"
    jobs = [(root / s["source"], s) for s in segs] + [(b["path"], b) for b in brolls if not b["image"]]
    with ThreadPoolExecutor(4) as pool:
        tracks = list(pool.map(lambda j: rf.face_track(j[0], j[1]["start"], j[1]["start"] + j[1]["frames"] / fps,
                                                       fps, cache_dir=cache_dir), jobs))
    track_of = {id(s): t for (_, s), t in zip(jobs, tracks)}
    stage("analyse (visages)")

    plans, prev, faces = [], 1.0, []
    rf.doux_u(segs, style)
    for s in segs:
        kind = s.get("zoom_type") or ("doux" if style == "doux" and s.get("zoom", 1.0) > 1.0 else None)
        use_face = s.get("zoom_center") == "visage" or (v2 and "zoom_center" not in s)
        crops, f, prev = plan(s, infos[root / s["source"]], w, h, fps, track_of[id(s)], kind, prev, rampe_s, use_face)
        plans.append(crops)
        faces += f
    bplans = []
    for b in brolls:
        crops, _, _ = plan(b, infos[b["path"]], w, h, fps, track_of.get(id(b)), None, 1.0, rampe_s,
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
            W, H = infos[p]["width"], infos[p]["height"]
            spans.setdefault(p, []).extend((x / W, y / H, (x + cw) / W, (y + ch) / H) for x, y, cw, ch in crops[::5])
        boxes = {p: tuple(statistics.median(c[k] for c in v) for k in range(4)) for p, v in spans.items()}
        with ThreadPoolExecutor(4) as pool:
            ms = dict(zip(boxes, pool.map(lambda p: rf.measure_colour(p, box=boxes[p]), boxes)))
        # mesure impossible (None) → colour_filter rend "" : ce média reste tel quel
        colours = {p: rf.colour_filter(m, target) for p, m in ms.items()}
        if any(m is None for m in ms.values()):
            print("couleur auto : certains médias sans image mesurable, laissés tels quels.")
    elif edl.get("couleur") == "auto":
        print("couleur auto : profile.couleur absent, rien appliqué.")
    for b in brolls:  # b-roll à l'écran : pas de visage de l'orateur à éviter
        i0 = round(b["at"] * fps)
        faces[i0:i0 + b["frames"]] = [None] * len(faces[i0:i0 + b["frames"]])

    tmp = Path(tempfile.mkdtemp(prefix="monteur_"))
    try:
        # 1. segments et b-roll
        names = [f"seg{s['index']:04d}.mkv" for s in segs]
        bnames = [f"broll{b['index']:03d}.mkv" for b in brolls]
        with ThreadPoolExecutor(3) as pool:  # x264 est déjà multithread ; 3 suffit à occuper les cœurs
            futs = [pool.submit(render_segment, s, root / s["source"], tmp / n, w, h, fps, c,
                                colours.get(root / s["source"], ""), a.brouillon) for s, n, c in zip(segs, names, plans)]
            futs += [pool.submit(render_segment, b, b["path"], tmp / n, w, h, fps, c, colours.get(b["path"], ""),
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

        args = [ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt"]
        graph, v, n = [], "[0:v]", 1
        if colours:
            # boucle fermée : on mesure les plans tels qu'ils seront vus (zooms, cadrage compris) et on corrige
            # l'écart restant à la DA en une passe globale (la correction par média, bornée, n'y arrive pas seule)
            with ThreadPoolExecutor(4) as pool:
                st_segs = list(pool.map(lambda nm: rf.measure_colour(tmp / nm, n=3), names))
            vu = rf.pooled_colour(st_segs, [s["frames"] for s in segs])
            reste = rf.colour_filter(vu, target)
            if reste:
                graph.append(f"[0:v]{reste}[vc]")
                v = "[vc]"
            print(f"  couleur à l'écran {vu} → cible {target} : correction finale « {reste or 'aucune'} »")
        trans = (profile.get("broll") or {}).get("transition", "coupe")
        for k, (b, bn) in enumerate(zip(brolls, bnames)):
            args += ["-i", bn]
            d = b["frames"] / fps
            fade = ""
            if b.get("transition", trans) == "fondu":  # fondu enchaîné court (3 images)
                fd = 3 / fps
                fade = (f"format=yuva420p,fade=t=in:st=0:d={fd:.4f}:alpha=1,"
                        f"fade=t=out:st={d - fd:.4f}:d={fd:.4f}:alpha=1,")
            graph.append(f"[{n}:v]{fade}setpts=PTS-STARTPTS+{b['at']}/TB[b{k}]")
            graph.append(f"{v}[b{k}]overlay=eof_action=pass:enable='between(t,{b['at']},{b['at'] + d})'[v{k}]")
            v, n = f"[v{k}]", n + 1

        st = profile.get("sous_titres")
        fw, fh = fmt["width"], fmt["height"]

        def face_at(t_a, t_b):
            boxes = [f for f in faces[max(0, int(t_a * fps)):max(1, math.ceil(t_b * fps))] if f]
            return (min(f[0] for f in boxes) * fh, max(f[1] for f in boxes) * fh) if boxes else None

        words = captions.timeline_words(segs, root) if edl.get("captions", True) else []
        if words or edl.get("titles"):
            ass = captions.build_ass(words, st, fw, fh, edl.get("titles") or [], face_at=face_at)
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
        if v == "[0:v]":
            graph.append("[0:v]null[vout]")
            v = "[vout]"
        args += ["-filter_complex", ";".join(graph), "-map", v, "-an", "-t", f"{total:.6f}",
                 "-r", str(fps), *venc(a.brouillon, final=True), "video.mkv"]
        run_in(args, tmp)
        stage(f"finition vidéo (b-roll, sous-titres, {len(emojis)} emojis)")
        th.join()
        if "err" in res:
            raise res["err"]
        mix, how = res["mix"]
        print(f"  son : {how} (fini {max(0, time.time() - t_audio):.1f} s après le début de la finition)")

        # 3. mux
        run([ffmpeg(), "-y", "-v", "error", "-i", str(tmp / "video.mkv"), "-i", str(mix), "-map", "0:v", "-map", "1:a",
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
