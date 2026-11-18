"""Planche contact dense d'une vidéo montée (2 images/s par défaut) pour la regarder comme un monteur.
Avec --ref : deux bandes l'une sous l'autre, la vidéo puis la référence, au même temps (comparer la finition).

  python scripts/planche.py sorties/reel.mp4                       → .monteur/vues/reel_planche.jpg
  python scripts/planche.py sorties/reel.mp4 --ref inspi/ref.mp4   → .monteur/vues/reel_vs_ref.jpg
  python scripts/planche.py inspi/ref.mp4 --ips 6 --debut 14 --fin 16       un passage seulement (carton, citation…)
  python scripts/planche.py broll/anim_citation.mp4 --ips 6 --ref inspi/ref.mp4 --ref-debut 14
                                                                   un insert face au même passage de la référence"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, ffmpeg, probe, run  # noqa: E402

COLS = 10
FONT = (Path(__file__).parent / "fonts" / "Poppins-ExtraBold.ttf").resolve().as_posix().replace(":", r"\:")


def bande(video, ips, largeur, out, debut=0.0, duree=None):
    d = probe(video).get("duration") or 0
    if not d:
        die(f"vidéo illisible : {video}")
    d = min(d - debut, duree or d)
    n = max(1, math.ceil(d * ips))
    rows = math.ceil(n / COLS)
    # numéro de seconde incrusté (temps de la vidéo, même extraite) : on cite « à 12,5 s » sans calcul
    run([ffmpeg(), "-y", "-v", "error", "-ss", f"{debut:.3f}", "-t", f"{d:.3f}", "-i", str(video), "-vf",
         f"fps={ips},scale={largeur}:-2,drawtext=fontfile='{FONT}':text='%{{pts\\:hms\\:{debut:.3f}}}':x=4:y=4:fontsize=13:fontcolor=white:"
         f"box=1:boxcolor=black@0.6,tile={COLS}x{rows}", "-frames:v", "1", "-q:v", "3", str(out)])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--ref", help="vidéo de référence (inspi, montage d'un pro) à mettre en regard")
    ap.add_argument("--ips", type=float, default=2.0, help="images par seconde (défaut 2)")
    ap.add_argument("--debut", type=float, default=0.0, help="seconde de départ dans la vidéo")
    ap.add_argument("--fin", type=float, help="seconde de fin dans la vidéo")
    ap.add_argument("--ref-debut", type=float, default=0.0, help="seconde de la référence en face du début de la vidéo")
    ap.add_argument("--out")
    ap.add_argument("--root", default=".", help="dossier de travail (contient .monteur/)")
    a = ap.parse_args(argv)
    v = Path(a.video)
    vues = Path(a.root) / ".monteur" / "vues"
    vues.mkdir(parents=True, exist_ok=True)
    largeur = 130 if a.ref else 160
    if not a.ref:
        out = Path(a.out or vues / f"{v.stem}_planche.jpg")
        bande(v, a.ips, largeur, out, a.debut, a.fin and a.fin - a.debut)
    else:
        out = Path(a.out or vues / f"{v.stem}_vs_ref.jpg")
        duree = (a.fin or probe(v).get("duration") or 0) - a.debut  # la référence : autant que la vidéo
        b1 = bande(v, a.ips, largeur, vues / "_a.jpg", a.debut, duree)
        b2 = bande(Path(a.ref), a.ips, largeur, vues / "_b.jpg", a.ref_debut, duree)
        run([ffmpeg(), "-y", "-v", "error", "-i", str(b1), "-i", str(b2), "-filter_complex",
             "[0]pad=iw:ih+30:0:0:white[a];[a][1]vstack", str(out)])
        b1.unlink(), b2.unlink()
    print(f"Planche : {out}  (haut = la vidéo" + (", bas = la référence)" if a.ref else ")") + " → Read")


if __name__ == "__main__":
    main()
