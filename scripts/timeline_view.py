"""Une image pour « voir » une plage de rush : vignettes, forme d'onde, mots, silences."""
import argparse
import json
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from common import die, ffmpeg, find_transcript, probe, run, work_dir  # noqa: E402

W, N_THUMBS, WAVE_H, WORDS_H, AXIS_H = 1600, 8, 160, 90, 24


def font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def main():
    ap = argparse.ArgumentParser(description="PNG vignettes + forme d'onde + mots pour une plage d'un rush.")
    ap.add_argument("video")
    ap.add_argument("--de", type=float, required=True, help="début (s)")
    ap.add_argument("--a", type=float, required=True, help="fin (s)")
    ap.add_argument("--out", help="PNG de sortie (défaut .monteur/vues/<stem>_<de>-<a>.png)")
    ap.add_argument("--root", default=".", help="dossier de travail (contient .monteur/)")
    args = ap.parse_args()

    src = Path(args.video)
    if not src.is_file():
        die(f"vidéo introuvable : {src}")
    info = probe(src)
    de, a = max(0.0, args.de), min(args.a, info["duration"])
    if a <= de:
        die(f"plage vide : --de {args.de} --a {args.a} (durée {info['duration']:.1f} s)")
    dur = a - de
    out = Path(args.out) if args.out else work_dir(args.root) / "vues" / f"{src.stem}_{de:g}-{a:g}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    x_of = lambda t: int((t - de) / dur * (W - 1))

    with tempfile.TemporaryDirectory() as tmp:
        thumbs = []
        if "width" in info:
            tw = W // N_THUMBS
            for i in range(N_THUMBS):
                f = Path(tmp) / f"t{i}.png"
                run([ffmpeg(), "-y", "-v", "error", "-ss", f"{de + dur * (i + 0.5) / N_THUMBS:.3f}", "-i", str(src),
                     "-frames:v", "1", "-vf", f"scale={tw}:-2", str(f)])
                if f.exists():
                    thumbs.append(Image.open(f).convert("RGB"))
        samples = np.zeros(0)
        if info["has_audio"]:
            wav = Path(tmp) / "a.wav"
            run([ffmpeg(), "-y", "-v", "error", "-ss", str(de), "-t", str(dur), "-i", str(src),
                 "-vn", "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le", str(wav)])
            with wave.open(str(wav)) as wf:
                samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16) / 32768.0

    th_h = max((t.height for t in thumbs), default=0)
    img = Image.new("RGB", (W, th_h + WAVE_H + WORDS_H + AXIS_H), (24, 24, 28))
    d = ImageDraw.Draw(img)
    for i, t in enumerate(thumbs):
        img.paste(t, (i * (W // N_THUMBS), 0))

    found = find_transcript(args.root, src.resolve())
    tr = found or {"words": [], "silences": []}
    y0 = th_h
    for s in tr["silences"]:
        if s["end"] > de and s["start"] < a:
            d.rectangle([x_of(max(s["start"], de)), y0, x_of(min(s["end"], a)), y0 + WAVE_H + WORDS_H],
                        fill=(70, 40, 40))

    if samples.size:
        mid = y0 + WAVE_H // 2
        cols = np.array_split(samples, W)
        peak = max(np.abs(samples).max(), 1e-3)
        for x, c in enumerate(cols):
            if c.size:
                d.line([x, mid - c.max() / peak * (WAVE_H // 2 - 4), x, mid - c.min() / peak * (WAVE_H // 2 - 4)],
                       fill=(90, 200, 255))

    # mots en 3 rangées décalées pour éviter les chevauchements
    f_word, row_end = font(15), [-1] * 3
    for w in tr["words"]:
        if w["end"] <= de or w["start"] >= a:
            continue
        x = x_of(max(w["start"], de))
        label = w["w"] + ("?" if w["p"] < 0.5 else "")
        row = next((r for r in range(3) if row_end[r] < x), 0)
        y = y0 + WAVE_H + 6 + row * 27
        d.line([x, y0 + WAVE_H, x, y], fill=(120, 120, 120))
        d.text((x + 2, y), label, font=f_word, fill=(255, 230, 0) if w["p"] >= 0.5 else (255, 120, 80))
        row_end[row] = x + d.textlength(label, font=f_word) + 4

    f_axis, ya = font(13), y0 + WAVE_H + WORDS_H
    step = next(s for s in (0.5, 1, 2, 5, 10, 30, 60, 1e9) if dur / s <= 20)
    t = (de // step + 1) * step if de % step else de
    while t <= a:
        x = x_of(t)
        d.line([x, ya, x, ya + 6], fill=(200, 200, 200))
        d.text((x + 2, ya + 5), f"{t:g}s", font=f_axis, fill=(200, 200, 200))
        t += step

    img.save(out)
    n = sum(1 for w in tr["words"] if w["end"] > de and w["start"] < a)
    print(f"{out} ({img.width}x{img.height}, {len(thumbs)} vignettes, {n} mots, média de {info['duration']:.2f} s"
          f"{'' if found else ', pas de transcript'})")


if __name__ == "__main__":
    main()
