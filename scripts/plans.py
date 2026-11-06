"""Index des plans des rushs + cadence de la voix off.

1. `python plans.py rushs [broll]` : découpe chaque vidéo en plans (même détecteur de coupes que la mesure de la DA),
   note pour chacun durée, netteté, mouvement, et écrit :
   - `.monteur/plans.json` (index lu par pacing : "plan": "P12" dans l'edl) ;
   - `.monteur/plans.md` : une ligne par plan, avec une description que Claude complète UNE fois (gardée ensuite) ;
   - `.monteur/plans_1.jpg`, `_2.jpg`… : planches numérotées (24 plans par planche) — Claude les lit pour décrire
     les plans. Une planche ≈ 2 500 jetons : économique, même avec un abonnement Claude à 20 $.
2. `python plans.py --voix voix/vo.wav` : découpe la voix off en créneaux de plans au rythme de la DA (distribution
   des durées de plans des références, accroche plus rapide) calés sur les débuts de mots → `.monteur/cadence.md`.
   Claude n'a plus qu'à choisir quel plan va dans quel créneau (le sens), le rythme est déjà celui du créateur.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
import measure  # noqa: E402
from common import VIDEO_EXT, die, find_transcript, probe, work_dir  # noqa: E402

PLAN_MIN_S = 0.6        # plus court : un flash, inutilisable comme plan
NOIR = 12               # luminosité moyenne (0-255) sous laquelle une image est noire (fondu d'ouverture)
PAR_PLANCHE = 24
CASE = 240              # côté d'une vignette (px)
SPLIT_S = 8.0           # un plan plus long est proposé en morceaux (P12a, P12b…) : plus de choix de cadrage
DEFAUT_QUANTILES = [1.2, 1.6, 2.2, 3.0, 4.0]
MOTIF = [2, 1, 3, 2, 0, 4, 1, 2, 3]  # ordre de tirage des quantiles : alterne court/long comme un monteur


def _fr(x, n=1):
    return f"{x:.{n}f}".replace(".", ",")


def _sharpness(gray):
    g = gray.astype(np.float32)
    return float(np.abs(np.diff(g, axis=0)).mean() + np.abs(np.diff(g, axis=1)).mean())


def shots(path):
    """[(start, end, netteté, mouvement, vignette PIL)] d'une vidéo."""
    info = probe(path)
    if not info.get("width"):
        return []
    dur = info["duration"]
    fps = min(info.get("fps") or 25, 25)
    gray = measure.decode(path, measure.ANALYSE_LARGEUR, fps=fps, info=info)
    d = measure.frame_diffs(gray)
    cuts = measure.detect_cuts(d, fps)
    times = [0.0] + [i / fps for i in cuts] + [dur]
    rgb = measure.decode(path, CASE, fps=2, gray=False, info=info)
    out = []
    lum = gray.reshape(len(gray), -1).mean(axis=1) if len(gray) else []
    for scene, (a, b) in enumerate(zip(times, times[1:])):
        i = int(a * fps)
        while i < min(len(lum), int(b * fps)) and lum[i] < NOIR:  # fondu au noir en début de plan : jamais montré
            i += 1
        a = max(a, i / fps)
        if b - a < PLAN_MIN_S:
            continue
        pieces = max(1, int((b - a) // SPLIT_S))
        step = (b - a) / pieces
        for k in range(pieces):
            s, e = a + k * step, a + (k + 1) * step
            i0, i1 = int(s * fps), max(int(s * fps) + 1, int(e * fps))
            mv = float(np.mean(d[i0 + 1:i1])) if i1 - i0 > 2 else 0.0
            mid = min(len(rgb) - 1, int((s + e) / 2 * 2)) if len(rgb) else None
            img = Image.fromarray(rgb[mid]) if mid is not None else None
            sharp = _sharpness(np.asarray(img.convert("L"))) if img else 0.0
            out.append((round(s, 3), round(e, 3), sharp, mv, img, scene))
    return out


def _label(mv, sharp, sharp_med):
    m = "fixe" if mv < 1.0 else "lent" if mv < 4 else "rapide"
    n = "flou" if sharp < 0.55 * sharp_med else "net"
    return m, n


def index(folders, root):
    wd = work_dir(root)
    old = {}
    if (wd / "plans.json").exists():
        try:
            old = json.loads((wd / "plans.json").read_text(encoding="utf-8")).get("plans", {})
        except ValueError:
            old = {}
    desc = {(v["source"], round(v["start"], 2)): v.get("description", "") for v in old.values()}
    if (wd / "plans.md").exists():  # Claude décrit les plans dans plans.md : ces descriptions priment
        for line in (wd / "plans.md").read_text(encoding="utf-8").splitlines():
            parts = line.split("	")
            if len(parts) >= 5 and parts[0].startswith("P") and parts[4].strip() not in ("", "—"):
                try:
                    start = round(float(parts[2].split("–")[0].replace(",", ".")), 2)
                except ValueError:
                    continue
                desc[(parts[1], start)] = parts[4].strip()
    files = []
    for f in folders:
        p = Path(f)
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files += sorted(x for x in p.iterdir() if x.suffix.lower() in VIDEO_EXT)
        else:
            die(f"introuvable : {p}")
    if not files:
        die("aucune vidéo à indexer (dossiers vides ?)")
    rows = []
    for f in files:
        rel = f.resolve().relative_to(root.resolve()).as_posix() if f.resolve().is_relative_to(root.resolve()) \
            else f.as_posix()
        got = shots(f)
        print(f"{rel} : {len(got)} plan(s)")
        rows += [(rel, *x) for x in got]
    sharp_med = statistics.median([r[3] for r in rows]) if rows else 1.0
    plans, thumbs, scenes = {}, [], {}
    for n, (src, s, e, sharp, mv, img, scene) in enumerate(rows, 1):
        pid = f"P{n}"
        m, net = _label(mv, sharp, sharp_med)
        sid = scenes.setdefault((src, scene), f"S{len(scenes) + 1}")
        plans[pid] = {"source": src, "start": s, "end": e, "duree": round(e - s, 3), "mouvement": m, "nettete": net,
                      "scene": sid, "description": desc.get((src, round(s, 2)), "")}
        thumbs.append((pid, e - s, img))
    a_decrire = descriptions(wd, plans)
    for old_sheet in wd.glob("plans_*.jpg"):
        old_sheet.unlink()
    sheets = [_sheet(thumbs[k:k + PAR_PLANCHE], wd / f"plans_{k // PAR_PLANCHE + 1}.jpg")
              for k in range(0, len(thumbs), PAR_PLANCHE)]
    print(f"{len(plans)} plans ({len(scenes)} scènes) → .monteur/plans.md ; planches : "
          f"{', '.join(x.name for x in sheets)}" + (f" ; {a_decrire} à décrire" if a_decrire else
                                                  " ; descriptions déjà faites"))


def descriptions(wd, plans=None):
    """Fusionne `.monteur/descriptions.txt` (« P12: mains versant un café », écrit par Claude en une fois avec Write)
    dans l'index, réécrit plans.json et plans.md. Renvoie le nombre de plans encore sans description."""
    if plans is None:
        try:
            plans = json.loads((wd / "plans.json").read_text(encoding="utf-8"))["plans"]
        except (OSError, ValueError, KeyError):
            return None
    f = wd / "descriptions.txt"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            pid, _, txt = line.partition(":")
            pid = pid.strip().upper()
            if pid in plans and txt.strip():
                plans[pid]["description"] = txt.strip()
    (wd / "plans.json").write_text(json.dumps({"plans": plans}, ensure_ascii=False, indent=1), encoding="utf-8")
    tab = "\t"
    lines = [tab.join([pid, v["source"], f"{_fr(v['start'], 2)}–{_fr(v['end'], 2)} ({_fr(v['duree'])} s)",
                       f"{v['mouvement']}, {v['nettete']}, scène {v.get('scene', '?')}", v["description"] or "—"])
             for pid, v in plans.items()]
    head = ("# Plans des rushs\n"
            "<!-- id, fichier, bornes (s), mouvement, netteté, scène (deux plans de la même scène se ressemblent :\n"
            "     jamais l'un juste après l'autre), description. Claude : décris les plans « — » d'après les planches\n"
            "     plans_N.jpg en écrivant .monteur/descriptions.txt (Write, une ligne par plan :\n"
            "     « P12: mains versant un café, gros plan »). Les descriptions sont gardées aux relances. -->\n")
    (wd / "plans.md").write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return sum(1 for v in plans.values() if not v["description"])


def _sheet(items, dest, cols=6):
    rows = (len(items) + cols - 1) // cols
    pad = 22
    sheet = Image.new("RGB", (cols * CASE, rows * (CASE + pad)), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(str(Path(__file__).parent / "fonts" / "Montserrat-ExtraBold.ttf"), 16)
    except OSError:
        font = ImageFont.load_default()
    for i, (pid, dur, img) in enumerate(items):
        x, y = (i % cols) * CASE, (i // cols) * (CASE + pad)
        if img is not None:
            t = img.copy()
            t.thumbnail((CASE - 4, CASE - 4))
            sheet.paste(t, (x + (CASE - t.width) // 2, y + pad + (CASE - t.height) // 2))
        draw.text((x + 4, y + 2), f"{pid}  {_fr(dur)} s", fill=(255, 230, 0), font=font)
    sheet.save(dest, quality=82)
    return dest


# ---------- cadence de la voix off ----------

def targets(profile):
    """Suite de durées de plans visées (s) : quantiles de la DA tirés dans un ordre qui alterne court/long."""
    r = (profile or {}).get("rythme") or {}
    q = r.get("plans_quantiles")
    if not q or len(q) != 5:
        med = r.get("mediane_plan_s") or r.get("duree_moyenne_plan_s")
        q = [round(med * f, 3) for f in (0.55, 0.75, 1.0, 1.35, 1.8)] if med else DEFAUT_QUANTILES
    return q, r.get("hook_plan_s") or q[1]


def cadence(words, profile, total=None):
    """Créneaux [(start, end, mot_index)] au rythme de la DA, chaque coupe sur un début de mot (de préférence une
    fin de phrase ou une pause), en temps du fichier de la voix."""
    ws = [w for w in words if not w.get("filler")]
    if not ws:
        return []
    q, hook_s = targets(profile)
    end_all = total or ws[-1]["end"]
    starts = [(k, w["start"]) for k, w in enumerate(ws)]
    slots, cur, cur_k, n = [], 0.0, 0, 0
    while True:
        want = hook_s if cur < measure.HOOK_S else q[MOTIF[n % len(MOTIF)]]
        n += 1
        lo, hi = cur + 0.6 * want, cur + 1.4 * want
        if end_all - cur < 1.5 * want:  # le reste tient en un plan
            slots.append((cur, end_all, cur_k))
            break
        cands = [(k, t) for k, t in starts if lo <= t <= hi and k > cur_k]
        if not cands:
            nxt = next(((k, t) for k, t in starts if t > hi and k > cur_k), None)
            if nxt is None:
                slots.append((cur, end_all, cur_k))
                break
            cands = [nxt]

        def cost(c):
            k, t = c
            prev = ws[k - 1] if k else None
            bonus = 0.0
            if prev is not None and prev["w"].rstrip()[-1:] in measure.FIN_PHRASE:
                bonus = 0.35 * want  # couper entre deux phrases : la coupe tombe juste
            elif prev is not None and t - prev["end"] >= 0.2:
                bonus = 0.2 * want
            return abs(t - (cur + want)) - bonus
        k, t = min(cands, key=cost)
        slots.append((cur, t, cur_k))
        cur, cur_k = t, k
    return [(round(a, 3), round(b, 3), ws[k]) for a, b, k in slots]


def write_cadence(voix, root, profile):
    reste = descriptions(work_dir(root))
    if reste:
        print(f"Attention : {reste} plan(s) sans description (écris .monteur/descriptions.txt)")
    t = find_transcript(root, voix)
    if not t or not t.get("words"):
        die(f"pas de transcription pour {voix} : lance d'abord transcribe.py sur son dossier")
    slots = cadence(t["words"], profile, t.get("duration"))
    q, hook_s = targets(profile)
    lines = ["# Cadence de la voix off",
             f"<!-- {voix} : {len(slots)} créneaux au rythme de la DA (plans visés {', '.join(_fr(x) for x in q)} s,"
             f" accroche {_fr(hook_s)} s). Pour chaque créneau, choisis un plan de plans.md qui illustre ce qui est"
             " dit ; dans l'edl : {\"plan\": \"P12\", \"voix_t\": <début>, \"mot\": \"<1er mot>\"}. -->"]
    ws = t["words"]
    for i, (a, b, w0) in enumerate(slots, 1):
        txt = " ".join(w["w"] for w in ws if a - 1e-6 <= w["start"] < b)
        lines.append(f"C{i}\t{_fr(a, 2)}–{_fr(b, 2)} ({_fr(b - a)} s)\tmot « {w0['w']} »\t{txt}")
    wd = work_dir(root)
    (wd / "cadence.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(slots)} créneaux → .monteur/cadence.md (durée {_fr(slots[-1][1] if slots else 0)} s)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Index des plans des rushs (planches numérotées) et cadence de la voix off.")
    ap.add_argument("dossiers", nargs="*", help="dossiers ou fichiers vidéo à indexer (ex. rushs broll)")
    ap.add_argument("--voix", help="fichier de la voix off (déjà transcrit) : écrit .monteur/cadence.md")
    ap.add_argument("--profil", help="ma-da/profile.json (défaut)")
    a = ap.parse_args(argv)
    root = Path.cwd()
    prof = Path(a.profil) if a.profil else root / "ma-da" / "profile.json"
    profile = json.loads(prof.read_text(encoding="utf-8")) if prof.is_file() else {}
    if not a.dossiers and not a.voix:
        die("donne des dossiers à indexer (ex. rushs broll) et/ou --voix <fichier>")
    if a.dossiers:
        index(a.dossiers, root)
    if a.voix:
        write_cadence(a.voix, root, profile)


if __name__ == "__main__":
    main()
