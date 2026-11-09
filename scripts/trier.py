"""Dossier déposé en vrac → ce qu'est chaque fichier (parle face caméra, plan, voix off, musique) et le mode de
montage. À lancer après `transcribe.py <dossier>`. Écrit .monteur/tri.md.

Usage : python scripts/trier.py depot [--root .]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import VIDEO_EXT, find_transcript, probe, work_dir  # noqa: E402
from transcribe import AUDIO_EXT  # noqa: E402

PAROLE_MOTS_S = 1.0   # parole continue ≈ 2-3 mots/s ; un plan avec un bruit de fond en a très peu
DOUTE = (0.5, 1.5)    # entre les deux : Claude regarde une image avant de décider
CONFIANCE_VOIX = 0.6  # une chanson donne des mots, mais peu sûrs
MOTS_MUSIQUE = ("musique", "music", "song", "instru", "beat")


def classer(nom, video, duree, mots, conf):
    d = mots / duree if duree else 0
    doute = DOUTE[0] <= d <= DOUTE[1]
    if not video:
        if any(m in nom.lower() for m in MOTS_MUSIQUE) or d < PAROLE_MOTS_S or conf < CONFIANCE_VOIX:
            return "musique", doute
        return "voix off", doute
    return ("parle" if d >= PAROLE_MOTS_S else "plan"), doute


def mode(types):
    if "voix off" in types:
        return "voix off"
    if "parle" in types:
        return "face caméra"
    return "aucune voix"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dossier")
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    root, dossier = Path(a.root), Path(a.dossier)
    lignes, types = [], []
    for f in sorted(dossier.iterdir()):
        ext = f.suffix.lower()
        if ext not in VIDEO_EXT | AUDIO_EXT:
            if f.is_file() and f.stem.lower().startswith("trame") and ext in (".txt", ".md"):
                lignes.append(f"| {f.name} | trame | | | à lire : ordre, ton, durée, plans demandés |")
            continue
        info = probe(f)
        t = find_transcript(root, f.as_posix()) or {}
        ws = t.get("words", [])
        conf = sum(w.get("p", 1) for w in ws) / len(ws) if ws else 0
        video = ext in VIDEO_EXT and "width" in info
        typ, doute = classer(f.name, video, info["duration"], len(ws), conf)
        types.append(typ)
        lignes.append(f"| {f.name} | {typ}{' ?' if doute else ''} | {info['duration']:.1f} | "
                      f"{len(ws) / max(info['duration'], 0.1):.1f} | {'sans son' if not info['has_audio'] else 'non transcrit' if not t else ''} |")
    m = mode(types)
    out = ["# Tri du dépôt", "", f"Mode : **{m}**", "",
           "| fichier | type | durée (s) | mots/s | note |", "|---|---|---|---|---|", *lignes, "",
           "`?` = doute : regarde une image (timeline_view.py) avant de décider."]
    if m == "aucune voix":
        out.append("Aucune voix : ce mode n'est pas géré, dis-le et demande une voix off.")
    (work_dir(root) / "tri.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
