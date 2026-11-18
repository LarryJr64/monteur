"""Journal des corrections (ma-da/corrections.jsonl) : repère ce qu'elle corrige d'une vidéo à l'autre.

Usage : python scripts/corrections.py ajoute --categorie zooms.par_minute --sens - --texte "trop de zooms"
                                      [--portee video|toujours] [--video <nom>] [--projet <nom>] [--root .]
        python scripts/corrections.py bilan [--root .]
Une ligne par correction : date, projet, video, categorie (chemin profile.json, ou « regle:<mot-clé> » pour une
règle de STYLE.md), sens (+ / −), texte (ses mots), portee (video / toujours), source (corriger / retouche).
`bilan` : même catégorie et même sens dits sur ≥ 2 vidéos différentes sans être devenus une règle durable →
à proposer (« tu as dit … deux fois, je le retiens pour la suite ? »).
"""
import argparse
import datetime
import json
from pathlib import Path

import common  # noqa: F401  (sortie UTF-8 sous Windows)

FICHIER = Path("ma-da") / "corrections.jsonl"


def journal(root):
    return Path(root) / FICHIER


def ajoute(root, categorie, sens, texte, portee="video", video=None, projet=None, source="corriger"):
    e = {"date": datetime.date.today().isoformat(), "projet": projet or Path(root).resolve().name,
         "video": video or "", "categorie": categorie.strip().lower(), "sens": "-" if sens in ("-", "−") else "+",
         "texte": texte, "portee": "toujours" if portee == "toujours" else "video", "source": source}
    f = journal(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as out:
        out.write(json.dumps(e, ensure_ascii=False) + "\n")
    return e


def lire(root):
    f = journal(root)
    if not f.is_file():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # ligne abîmée (écrite à la main) : ignorée, les autres comptent
    return out


def repetitions(entries, seuil=2):
    """[(categorie, sens, nb de vidéos, textes)] dits sur ≥ seuil vidéos différentes, pas encore durables."""
    groupes = {}
    for e in entries:
        g = groupes.setdefault((e.get("categorie"), e.get("sens")), {"videos": set(), "textes": [], "durable": False})
        g["videos"].add((e.get("projet"), e.get("video")))
        g["durable"] |= e.get("portee") == "toujours"
        if e.get("texte") and e["texte"] not in g["textes"]:
            g["textes"].append(e["texte"])
    return sorted(((c, s, len(g["videos"]), g["textes"]) for (c, s), g in groupes.items()
                   if len(g["videos"]) >= seuil and not g["durable"]), key=lambda x: -x[2])


def main():
    ap = argparse.ArgumentParser(description="Journal des corrections : ajouter une correction, repérer celles qui reviennent.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ajoute", help="note une correction (même « cette vidéo seulement »)")
    a.add_argument("--categorie", required=True, help="chemin profile.json (zooms.par_minute) ou regle:<mot-clé>")
    a.add_argument("--sens", required=True, choices=("+", "-", "−"))
    a.add_argument("--texte", required=True, help="ses mots")
    a.add_argument("--portee", default="video", choices=("video", "toujours"))
    a.add_argument("--video", help="nom de la vidéo corrigée (ex. javed-reussite-v2)")
    a.add_argument("--projet", help="défaut : nom du dossier de travail")
    a.add_argument("--root", default=".")
    b = sub.add_parser("bilan", help="corrections qui reviennent sur plusieurs vidéos")
    b.add_argument("--root", default=".")
    args = ap.parse_args()
    if args.cmd == "ajoute":
        e = ajoute(args.root, args.categorie, args.sens, args.texte, args.portee, args.video, args.projet)
        print(f"Noté dans {journal(args.root)} : {e['categorie']} {e['sens']} ({e['portee']}).")
        return
    entries = lire(args.root)
    reps = repetitions(entries)
    print(f"{len(entries)} correction(s) dans {journal(args.root)}.")
    if not reps:
        print("Aucune correction répétée à proposer.")
    for c, s, n, textes in reps:
        print(f"À PROPOSER : {c} {s} sur {n} vidéos (« {' » / « '.join(textes[:3])} ») → "
              "« tu me l'as demandé plusieurs fois, je le retiens pour la suite ? »")


if __name__ == "__main__":
    main()
