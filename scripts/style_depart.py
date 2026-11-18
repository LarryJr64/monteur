"""Style de départ : une DA de métier prête à monter, copiée dans ma-da/ quand on n'a encore aucune référence.
Ses références (reels qu'elle aime, ses vidéos) et ses corrections la remplacent ensuite.

  python scripts/style_depart.py                         → liste des styles
  python scripts/style_depart.py vlog-dynamique [--projet .]   → copie dans ma-da/ (refuse si ma-da/ existe)
  python scripts/style_depart.py vlog-dynamique --remplacer    → ma-da/ existant mis de côté dans ma-da.avant-<date>/"""
import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die  # noqa: E402

STYLES = Path(__file__).resolve().parents[1] / "styles"


def styles():
    """{nom: description d'une ligne} (1re ligne de STYLE.md, après « — »)."""
    out = {}
    for d in sorted(p for p in STYLES.iterdir() if (p / "profile.json").exists()):
        titre = (d / "STYLE.md").read_text(encoding="utf-8").splitlines()[0]
        out[d.name] = titre.split("—", 1)[-1].strip()
    return out


def appliquer(nom, projet, remplacer=False):
    if nom not in styles():
        die(f"style inconnu : {nom} (styles : {', '.join(styles())})")
    da = Path(projet) / "ma-da"
    if da.exists() and any(da.iterdir()):
        if not remplacer:
            die(f"{da} existe déjà : rien n'est écrasé (--remplacer le met de côté avant)")
        garde = da.with_name(f"ma-da.avant-{time.strftime('%Y%m%d-%H%M%S')}")
        da.rename(garde)
        print(f"Ancien ma-da/ gardé dans {garde}")
    da.mkdir(parents=True, exist_ok=True)
    for f in ("profile.json", "STYLE.md"):
        shutil.copy2(STYLES / nom / f, da / f)
    print(f"Style de départ « {nom} » copié dans {da}/ : il sera remplacé par ce que tu valides.")
    return da


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nom", nargs="?")
    ap.add_argument("--projet", default=".")
    ap.add_argument("--remplacer", action="store_true", help="met l'ancien ma-da/ de côté au lieu de refuser")
    a = ap.parse_args(argv)
    if not a.nom:
        for nom, ligne in styles().items():
            print(f"{nom:<16} {ligne}")
        return
    appliquer(a.nom, a.projet, a.remplacer)


if __name__ == "__main__":
    main()
