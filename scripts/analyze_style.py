"""Apprend la DA mesurable depuis 5-10 vidéos déjà montées → ma-da/profile.json + images pour Claude.

Usage : python scripts/analyze_style.py mes-videos/ [--out ma-da] [--force] [--sans-transcription] [--rapide]
        python scripts/analyze_style.py --ajoute sorties/mon-reel.mp4 [--type mien|inspi]   (une vidéo de plus)
Médianes pondérées (`sources` de profile.json) : ses reels validés comptent double face aux inspirations, et
une source de moins de 90 jours double face aux plus anciennes. Les `verrous` ne sont jamais touchés.
Les transcripts des références (respiration, hook, musique sous la voix) sont relus depuis .monteur/transcripts/ ;
s'il en manque, transcribe.py est lancé (phrases dans .monteur/takes_references.md).
"""
import argparse
import copy
import datetime
import json
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

import measure
from common import LUFS_DIFFUSION, die, find_transcript, list_videos, probe, tonemap

MAX_IMAGES = 40
SHEET_MAX = 24
DEFAUTS = {
    "rythme": dict(measure.DEFAUT_RESPIRATION),
    "zooms": {"par_minute": 0, "intensite": 1.15, "style": "cut", "types": {}, "rampe_s": 0.5,
              "sur_visage": True, "une_coupe_sur": 2},
    "sous_titres": {"actif": True, "police": "Montserrat", "gras": True, "taille_pct_hauteur": 0.03,
                    "couleur": "#FFFFFF", "couleur_mot_actif": "#FFE600", "contour": "#000000",
                    "epaisseur_contour": 6, "position_y_pct": 0.70, "mots_par_bloc": 3, "majuscules": True,
                    "animation": "pop", "couleur_mot_cle": "#FDE047", "mots_cles_par_bloc": 1,
                    "mots_cles_part_blocs": 0.3, "emojis_par_minute": 0, "zone_sure": True},
    "audio": {"lufs_cible": LUFS_DIFFUSION, "musique": False, "musique_db_sous_voix": None, "musique_lufs": -30,
              "sfx_par_minute": 0, "sfx_types": [], "sfx_carton": None, "voix_nettoyee": True},
    "couleur": None,
    "broll": {"part_pct": 0.15, "duree_moy_s": 1.8, "jamais_sur_hook": True},
    "hook": {"duree_s": 3},
}
# Ce que Claude complète en regardant les images / lisant les transcripts. Une entrée retirée de la liste
# = Claude l'a confirmée : une relance ne remplace plus les champs mesurés qu'elle couvre (CONFIRME_PAR).
A_COMPLETER = ["sous_titres.police", "sous_titres.couleurs", "sous_titres.mots_par_bloc", "sous_titres.animation",
               "sous_titres.emojis", "zooms.style", "hook", "broll.usage", "titres", "transitions", "style_verbal"]
CONFIRME_PAR = {"zooms.style": "zooms.style", "sous_titres.couleur": "sous_titres.couleurs",
                "sous_titres.couleur_mot_cle": "sous_titres.couleurs",
                "sous_titres.mots_cles_par_bloc": "sous_titres.couleurs",
                "sous_titres.emojis_par_minute": "sous_titres.emojis", "hook.type": "hook", "hook.duree_s": "hook"}
# métriques comparées entre références pour repérer une vidéo atypique
DISPERSION = {"duree_moyenne_plan_s": "plan moyen (s)", "zooms_par_minute": "zooms/min", "lufs": "volume (LUFS)",
              "silence_max_s": "plus long silence (s)", "broll_part": "part de b-roll", "duree": "durée (s)",
              "sous_titres_presence": "sous-titres", "hook_plan_s": "plan de l'accroche (s)",
              "mediane_plan_s": "plan médian (s)", "coupes_par_minute": "coupes/min"}
POIDS = {"mien": 2, "inspi": 1}  # ses reels validés comptent double face aux inspirations
RECENT_JOURS = 90               # une source plus récente compte double face aux anciennes (sans date = ancienne)
DOSSIERS_INSPI = {"inspi", "inspis", "inspiration", "inspirations", "références", "references", "refs", "ref"}
STYLE_MD = """# Ma DA

## Observations
<!-- Claude : ce que tu as vu et mesuré, avec les chiffres. -->

## Règles de montage
<!-- Règles impératives et chiffrées (voir skill apprendre-ma-da). -->

## Coups de signature
<!-- 2-3 gestes qui rendent ce créateur reconnaissable. -->

## Style parlé
<!-- Formules de hook, structure, CTA. -->

## Règles apprises (corrections)
<!-- Chaque correction de l'utilisateur devient une règle ici. -->
"""


def fr(x, n=1):
    return f"{x:.{n}f}".replace(".", ",")


def med(values, default=None):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else default


def _get(d, dotted):
    for k in dotted.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _set(d, dotted, v):
    keys = dotted.split(".")
    for k in keys[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = v


def type_source(video):
    return "inspi" if Path(video).parent.name.lower() in DOSSIERS_INSPI else "mien"


def poids(source, today):
    d = source.get("date")
    recent = bool(d) and (today - datetime.date.fromisoformat(d)).days <= RECENT_JOURS
    return POIDS.get(source.get("type"), 1) * (2 if recent else 1)


def sources_de(mes, anciennes, today, type_=None):
    """Une source par vidéo mesurée {video, type, date, poids} ; date et type d'avant gardés."""
    old = {s["video"]: s for s in anciennes or []}
    out = []
    for v in mes:
        s = dict(old.get(v) or {"video": v, "type": type_ or type_source(v), "date": today.isoformat()})
        s["poids"] = poids(s, today)
        out.append(s)
    return out


def pondere(mes, sources):
    """Chaque mesure répétée `poids` fois : médianes et comptes de measured_fields deviennent pondérés."""
    w = {s["video"]: s.get("poids", 1) for s in sources or []}
    return {f"{k}#{i}": m for k, m in mes.items() for i in range(max(1, w.get(k, 1)))}


def export_frames(path, out, budget):
    """Planche contact (≤ 24 vignettes), planches du début (hook, 0-3 s) et de la fin (CTA, 3 dernières s),
    et ≤ `budget` images isolées en 540 px."""
    info = probe(path)
    dur = info["duration"]
    step = max(1.0, dur / SHEET_MAX)
    imgs = measure.decode(path, 540, fps=1 / step, gray=False, info=info)[:SHEET_MAX]
    keep = set(round(k * (len(imgs) - 1) / max(budget - 1, 1)) for k in range(min(budget, len(imgs))))
    for k in sorted(keep):
        Image.fromarray(imgs[k]).save(out / f"{path.stem}_{k * step:05.1f}.jpg", quality=85)
    _sheet([(Image.fromarray(a), k * step) for k, a in enumerate(imgs)], out / f"{path.stem}_sheet.jpg")
    sub = measure.decode(path, 180, fps=2, gray=False, info=info)  # vignettes de 180 px : inutile de décoder plus grand
    for name, t0 in (("debut", 0.0), ("fin", max(0.0, dur - 3.0))):
        part =[(Image.fromarray(a), j / 2) for j, a in enumerate(sub) if t0 <= j / 2 < t0 + 3.0]
        _sheet(part, out / f"{path.stem}_{name}.jpg")
    return len(keep)


def _clip(path, t0, n, fps, y_pct, info, width=360):
    """`n` images à `fps` depuis t0, recadrées sur la bande des sous-titres (±13 % de hauteur autour de y_pct)."""
    W, H = info["width"], info["height"]
    bh = round(0.26 * H) // 2 * 2
    top = min(max(0, round(y_pct * H - bh / 2)), H - bh) // 2 * 2
    h = round(width * bh / W) // 2 * 2
    from common import ffmpeg
    r = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t0):.3f}", "-i", str(path),
                        "-vf", f"fps={fps},crop={W}:{bh}:0:{top},scale={width}:{h},{tonemap(info)}null", "-frames:v", str(n),
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    a = r.stdout
    k = len(a) // (width * h * 3)
    return [Image.frombytes("RGB", (width, h), a[i * width * h * 3:(i + 1) * width * h * 3]) for i in range(k)]


def export_bursts(path, out, words, y_pct):
    """Rafales pour reconnaître l'animation des sous-titres (une image fixe ne la montre pas) :
    `_anim_entree` = 8 images consécutives autour de l'apparition d'un bloc (pop, rebond, glissé, fondu) ;
    `_anim_mots` = 8 images espacées de 0,1 s pendant le bloc (mot par mot, karaoké, surlignage)."""
    info = probe(path)
    if not info.get("width"):
        return 0
    dur, fps = info["duration"], info.get("fps") or 30
    starts = [w["start"] for i, w in enumerate(words or [])
              if w["start"] > 1.0 and (i == 0 or w["start"] - words[i - 1]["end"] >= 0.15)]
    t = starts[len(starts) // 3] if starts else dur / 3
    n = 0
    for name, t0, k, rate in (("anim_entree", t - 2 / fps, 8, fps), ("anim_mots", t - 0.1, 8, 10)):
        imgs = _clip(path, t0, k, rate, y_pct or 0.7, info)
        if imgs:
            _sheet([(im, t0 + j / rate) for j, im in enumerate(imgs)], out / f"{path.stem}_{name}.jpg", cols=4,
                   size=imgs[0].width, decimals=2)
            n += 1
    return n


def _sheet(items, dest, cols=6, size=180, decimals=1):
    if not items:
        return
    thumbs = []
    for img, t in items:
        th = img.resize((size, round(size * img.height / img.width)))
        ImageDraw.Draw(th).text((4, 4), f"{t:.{decimals}f}s", fill="yellow", stroke_width=2, stroke_fill="black")
        thumbs.append(th)
    cols = min(len(thumbs), cols)
    tw, tht = thumbs[0].size
    sheet = Image.new("RGB", (cols * tw, -(-len(thumbs) // cols) * tht))
    for k, th in enumerate(thumbs):
        sheet.paste(th, ((k % cols) * tw, (k // cols) * tht))
    sheet.save(dest, quality=85)


def measured_fields(mes):
    """{champ pointé du profil: valeur mesurée ou None} — médianes sur les références."""
    ms = list(mes.values())

    def col(key):
        return [m.get(key) for m in ms]

    def sub(key, k):
        return [(m.get(key) or {}).get(k) for m in ms]

    f = {}
    for k in ("duree_moyenne_plan_s", "mediane_plan_s", "coupes_par_minute", "silence_max_s"):
        f[f"rythme.{k}"] = med(col(k))
    for k in measure.DEFAUT_RESPIRATION:
        f[f"rythme.{k}"] = med(sub("respiration", k))
    qs = [m["plans_quantiles"] for m in ms if m.get("plans_quantiles")]
    f["rythme.plans_quantiles"] = [round(statistics.median(c), 3) for c in zip(*qs)] if qs else None
    f["rythme.hook_plan_s"] = med(col("hook_plan_s"))
    f["rythme.debit_mots_s"] = med(col("debit_mots_s"))
    vs = [x for x in col("voix_sous_carton") if x is not None]
    f["hook.voix_sous_carton"] = 2 * sum(vs) >= len(vs) if vs else None
    f["cadrage.visage_largeur"] = med(sub("cadrage", "visage_largeur"))
    f["cadrage.visage_y"] = med(sub("cadrage", "visage_y"))

    n_zooms = sum(len(m.get("zooms") or []) for m in ms)
    pool = Counter(z["type"] for m in ms for z in m.get("zooms") or [])
    zst = Counter(m.get("zoom_style") for m in ms if m.get("zoom_style"))
    f.update({"zooms.par_minute": med(col("zooms_par_minute")), "zooms.intensite": med(col("zoom_intensite")),
              "zooms.style": zst.most_common(1)[0][0] if zst else None,
              "zooms.types": {k: round(v / n_zooms, 2) for k, v in pool.most_common()} if n_zooms else None,
              "zooms.rampe_s": med(col("zoom_rampe_s")), "zooms.une_coupe_sur": med(col("zoom_une_coupe_sur"))})
    fins = [m["zoom_fin"] for m in ms if m.get("zoom_fin") is not None]
    f["zooms.fin"] = 2 * sum(fins) > len(fins) if fins else None
    vis = med(col("zoom_sur_visage"))
    f["zooms.sur_visage"] = None if vis is None else vis >= 0.6

    presence = med(col("sous_titres_presence"), 0.0)
    f["sous_titres.actif"] = presence >= 0.3
    f["sous_titres.taille_pct_hauteur"] = med(col("sous_titres_hauteur_pct"))
    f["sous_titres.position_y_pct"] = med(col("sous_titres_position_y_pct"))
    caps = [m["sous_titres_couleurs"] for m in ms if m.get("sous_titres_couleurs")]
    couleurs = Counter(c["couleur"] for c in caps if c.get("couleur"))
    accents = Counter(c["couleur_accent"] for c in caps if c.get("couleur_accent") and c["accent_part_blocs"] >= 0.05)
    f["sous_titres.couleur"] = _median_hex([c["couleur"] for c in caps if c.get("couleur")]) if couleurs else None
    if caps:
        part = med([c["accent_part_blocs"] for c in caps], 0.0)
        f["sous_titres.mots_cles_part_blocs"] = part
        f["sous_titres.mots_cles_par_bloc"] = (2 if med([c["accent_surface"] for c in caps], 0) > 0.55 else 1) \
            if part >= 0.05 else 0
        f["sous_titres.couleur_mot_cle"] = _median_hex([c["couleur_accent"] for c in caps
                                                        if c.get("couleur_accent") and c["accent_part_blocs"] >= 0.05]) \
            if accents else None
    f["sous_titres.emojis_par_minute"] = med(col("emojis_par_minute"))

    tps = [x for x in col("true_peak_db") if x is not None]
    f["audio.true_peak_max_db"] = max(tps) if tps else None
    bords = [(m.get("cadrage") or {}).get("visage_bord") for m in ms]
    bords = [x for x in bords if x is not None]
    f["visage_bord_max"] = max(bords) if bords else None
    noirs = [x for x in col("images_noires") if x is not None]
    f["images_noires_max"] = max(noirs) if noirs else None
    lufs = med(col("lufs"))
    # on diffuse à -14 LUFS (réseaux) quelle que soit la loudness du pro, gardée pour information
    f["audio.lufs_cible"] = LUFS_DIFFUSION
    f["audio.lufs_reference"] = round(lufs, 1) if lufs is not None else None
    # musique = fond continu (niveau) ET tonal (notes) : un bruit de pièce a le niveau mais pas les notes
    ton = med(col("musique_tonalite"))
    f["audio.musique"] = med(col("fond_sonore_db"), -180) > measure.FOND_MUSIQUE_DB and \
        (ton is None or ton >= measure.MUSIQUE_TONALITE)
    music = med(col("musique_lufs"))
    f["audio.musique_lufs"] = music if f["audio.musique"] else None
    f["audio.musique_db_sous_voix"] = round(music - lufs) if f["audio.musique"] and music is not None and lufs else None
    f["audio.sfx_par_minute"] = med(col("sfx_par_minute"))  # hors carton, hors parole : fiable
    # shortcut: le carton de la 1re référence qui en a un ; une médiane par rôle si plusieurs DA de carton diffèrent
    f["audio.sfx_carton"] = next((m["sfx_carton"] for m in ms if m.get("sfx_carton")), None)
    types = Counter(x["type"] for m in ms for x in m.get("sfx") or [])
    f["audio.sfx_types"] = [t for t, _ in types.most_common(4)] if types else None

    for k in ("luma", "contraste", "saturation", "temperature"):
        f[f"couleur.{k}"] = med(sub("couleur", k))
    f["broll.part_pct"] = med(col("broll_part"))
    f["broll.duree_moy_s"] = med(col("broll_duree_moy_s"))
    hooks = [m["hook"] for m in ms if m.get("hook")]
    f["hook.duree_s"] = med([h["duree_s"] for h in hooks])
    f["hook.type"] = Counter(h["type"] for h in hooks).most_common(1)[0][0] if hooks else None
    if hooks:
        f["broll.jamais_sur_hook"] = all(not any(a < h["duree_s"] for a, _ in (m.get("broll_plans") or []))
                                         for m in ms for h in [m.get("hook")] if h)
    return f


def _median_hex(hexes):
    rgb = [[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in hexes if h]
    return measure._hex([statistics.median(c) for c in zip(*rgb)]) if rgb else None


def dispersion(mes):
    """Médiane, min, max par métrique, et références atypiques (≥ 2 métriques loin de la médiane des autres)."""
    disp, flags = {}, {}
    if len(mes) < 3:
        return disp, []
    for key, label in DISPERSION.items():
        vals = {n: m.get(key) for n, m in mes.items() if m.get(key) is not None}
        if len(vals) < 3:
            continue
        mid = statistics.median(vals.values())
        mad = statistics.median(abs(v - mid) for v in vals.values())
        disp[key] = {"mediane": round(mid, 3), "min": round(min(vals.values()), 3), "max": round(max(vals.values()), 3)}
        for n, v in vals.items():
            # loin = au-delà de 3 MAD ET de 50 % de la médiane (plancher absolu pour les métriques proches de 0)
            if abs(v - mid) > max(3 * mad, 0.5 * abs(mid), 0.1):
                flags.setdefault(n, []).append(f"{label} {fr(v, 2)} vs {fr(mid, 2)}")
    fmt = Counter(tuple(m["format"].values()) for m in mes.values()).most_common(1)[0][0]
    for n, m in mes.items():
        if tuple(m["format"].values()) != fmt:
            flags.setdefault(n, []).append(f"format {m['format']['width']}x{m['format']['height']}")
            flags[n].append("")  # un format différent suffit à la signaler
    atyp = [{"reference": n, "ecarts": [e for e in v if e]} for n, v in flags.items() if len(v) >= 2]
    return disp, atyp


def build_profile(mes, sources=None):
    ms = list(mes.values())
    fmt = Counter(tuple(m["format"].values()) for m in ms).most_common(1)[0][0]
    p = copy.deepcopy(DEFAUTS)
    p.update({"version": 2, "references": list(mes), "format": dict(zip(("width", "height", "fps"), fmt))})
    p["rythme"].update({"duree_moyenne_plan_s": None, "mediane_plan_s": None, "coupes_par_minute": None,
                        "silence_max_s": None})
    for k, v in measured_fields(pondere(mes, sources)).items():
        if v is not None:
            _set(p, k, v)
    if p["couleur"] is None:
        p.pop("couleur")
    disp, atyp = dispersion(mes)
    p.update({"dispersion": disp, "references_atypiques": atyp, "mesures_brutes": mes,
              "a_completer_par_claude": list(A_COMPLETER), "verrous": []})
    if sources:
        p["sources"] = sources
    return p


def merge_existing(new, old):
    """Relance : les champs mesurés sont remplacés, sauf ceux verrouillés (corrections de l'utilisateur,
    `verrous`) ou confirmés par Claude (entrée retirée de `a_completer_par_claude`). Tout le reste est gardé."""
    merged = copy.deepcopy(old)
    for key in ("references", "format", "mesures_brutes", "dispersion", "references_atypiques", "sources"):
        if key in new:
            merged[key] = new[key]
    reste = old.get("a_completer_par_claude", [])
    verrous = set(old.get("verrous", []))
    mf = measured_fields(pondere(new["mesures_brutes"], new.get("sources")))
    for k, v in mf.items():
        # DA sans musique : le niveau de musique d'avant (ancien défaut -18) ne vaut plus rien
        if v is None and not (k == "audio.musique_db_sous_voix" and mf.get("audio.musique") is False):
            continue
        if k in verrous:
            continue
        if k in CONFIRME_PAR and CONFIRME_PAR[k] not in reste and _get(old, k) is not None:
            continue
        _set(merged, k, v)
    for key, val in new.items():
        merged.setdefault(key, val)
    for key, val in DEFAUTS.items():  # profil v1 : ajoute les nouveaux champs v2 par défaut
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            for k, v in val.items():
                merged[key].setdefault(k, v)
    if old.get("version", 1) < 2:
        merged["a_completer_par_claude"] = reste + [e for e in A_COMPLETER if e not in reste]
    merged["version"] = 2
    merged.setdefault("verrous", [])
    return merged


def transcripts(videos, root, rapide, takes):
    """Mots de chaque référence ; lance transcribe.py pour celles qui n'ont pas encore de transcript."""
    manquants = [v for v in videos if find_transcript(root, v.as_posix()) is None]
    if manquants or not Path(takes).exists():  # tout en cache : transcribe.py réécrit juste les phrases (rapide)
        if manquants:
            print(f"Transcription de {len(manquants)} référence(s) (≈1 min par minute de vidéo)...", flush=True)
        cmd = [sys.executable, str(Path(__file__).with_name("transcribe.py")), *map(str, videos),
               "--takes", takes, "--root", str(root)] + (["--rapide"] if rapide else [])
        if subprocess.run(cmd).returncode != 0:
            print("Transcription impossible : respiration, hook et musique non mesurés (défauts gardés).")
    return {v.as_posix(): (find_transcript(root, v.as_posix()) or {}).get("words") for v in videos}


def summary(p):
    r, z, a, st, b = p["rythme"], p["zooms"], p["audio"], p["sous_titres"], p["broll"]
    types = ", ".join(f"{k} {round(100 * v)} %" for k, v in (z.get("types") or {}).items()) or "aucun"
    lignes = [
        f"Rythme : un plan toutes les {fr(r['duree_moyenne_plan_s'] or 0)} s "
        f"({fr(r['coupes_par_minute'] or 0, 0)} coupes/min, médiane {fr(r['mediane_plan_s'] or 0)} s), "
        f"plus long silence {fr(r['silence_max_s'] or 0, 2)} s, respiration {fr(r['respiration_intra_s'], 2)} s "
        f"dans une phrase / {fr(r['respiration_phrases_s'], 2)} s entre phrases.",
        f"Zooms : {fr(z['par_minute'] or 0, 0)}/min (×{fr(z['intensite'], 2)}, {types}, rampe {fr(z['rampe_s'], 2)} s, "
        f"un zoom toutes les {fr(z['une_coupe_sur'] or 0, 0)} coupes"
        + (", centrés sur le visage" if z.get("sur_visage") else "") + ").",
        f"Sous-titres : {'détectés' if st['actif'] else 'non détectés'}"
        + (f", {st['couleur']} avec mot-clé {st['couleur_mot_cle']} sur {round(100 * st['mots_cles_part_blocs'])} % "
           f"des blocs, {fr(st['emojis_par_minute'] or 0, 1)} emoji/min" if st["actif"] else "") + ".",
        f"Son : voix à {fr(a['lufs_cible'])} LUFS, musique {'oui (' + fr(a['musique_lufs'], 0) + ' LUFS)' if a['musique'] else 'non'}, "
        f"bruitages {fr(a['sfx_par_minute'] or 0, 1)}/min" + (f" ({', '.join(a['sfx_types'])})" if a.get("sfx_types") else "") + ".",
        f"B-roll : {round(100 * (b['part_pct'] or 0))} % du temps, plans de {fr(b['duree_moy_s'] or 0)} s"
        + (", jamais sur le hook" if b.get("jamais_sur_hook") else "") + f". Hook : {fr(p['hook']['duree_s'])} s"
        + (f" ({p['hook']['type']})" if p["hook"].get("type") else "") + ".",
    ]
    if p.get("couleur"):
        c = p["couleur"]
        lignes.append(f"Image : luminosité {fr(c['luma'], 2)}, contraste {fr(c['contraste'], 2)}, "
                      f"saturation {fr(c['saturation'], 2)}, température {fr(c['temperature'], 2)}.")
    for a_ in p.get("references_atypiques", []):
        lignes.append(f"ATTENTION : {a_['reference']} ne ressemble pas aux autres ({' ; '.join(a_['ecarts'])}). "
                      "Vérifie que c'est bien ta DA, sinon retire-la de mes-videos/ et relance.")
    return "\n".join(lignes)


NEUTRE_MD = """# Ma DA (neutre : aucune vidéo de référence)

## Règles de montage
- Reel organique, sobre : plans de ~2 s, visage serré dans le haut du cadre, coupes sèches, pas de bruitage ni d'emoji.
- Sous-titres blancs, 3 mots, un mot-clé jaune sur 3 blocs environ.
- Zooms doux, 1 coupe sur 3. Musique seulement si un fichier est fourni, très bas.
- Le message (ou la trame) peut changer tout cela : ses demandes priment.
"""


def neutre(out):
    """Pas de référence : DA sobre de reel, pour que le montage et check.py marchent quand même."""
    p = copy.deepcopy(DEFAUTS)
    p.update(version=2, neutre=True, format={"width": 1080, "height": 1920, "fps": 30})
    p["rythme"].update(duree_moyenne_plan_s=2.0, plans_quantiles=[1.1, 1.5, 2.0, 2.6, 3.4], hook_plan_s=1.5)
    # visage serré et haut, sous-titres dessous : cadrage d'un reel interview monté par un pro (mesuré : 0,32 / 0,27)
    p["cadrage"] = {"visage_largeur": 0.30, "visage_y": 0.32}
    p["zooms"].update(par_minute=8, intensite=1.12, style="doux", types={"doux": 0.7, "punch": 0.3}, une_coupe_sur=3)
    p["sous_titres"]["emojis_par_minute"] = 0
    p["audio"].update(musique_db_sous_voix=-24)  # DA sans musique : fichier déposé = nappe discrète
    p["broll"]["part_pct"] = 0.0
    p["verrous"] = []
    out.mkdir(parents=True, exist_ok=True)
    (out / "profile.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "STYLE.md").write_text(NEUTRE_MD, encoding="utf-8")
    print(f"DA neutre écrite dans {out}/ (profile.json, STYLE.md) : reel sobre, à ajuster selon sa demande.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mesure la DA de vidéos déjà montées → profile.json + images.")
    ap.add_argument("dossier", nargs="?", help="dossier des vidéos de référence (ex. mes-videos/)")
    ap.add_argument("--neutre", action="store_true", help="aucune référence : écrit une DA de reel sobre")
    ap.add_argument("--out", default="ma-da", help="dossier de sortie (défaut ma-da)")
    ap.add_argument("--force", action="store_true", help="réécrire tout profile.json (perd les champs complétés)")
    ap.add_argument("--root", default=".", help="dossier de travail (contient .monteur/)")
    ap.add_argument("--sans-transcription", action="store_true",
                    help="ne pas transcrire les références (respiration/hook/musique : transcripts existants seulement)")
    ap.add_argument("--rapide", action="store_true", help="transcription rapide (modèle small)")
    ap.add_argument("--ajoute", metavar="VIDEO", help="ajoute UNE vidéo (reel validé, inspi) au profil existant sans tout refaire")
    ap.add_argument("--type", choices=("mien", "inspi"),
                    help="ses vidéos (poids 2) ou inspiration (poids 1) ; défaut : mien, inspi si le dossier s'appelle inspi/Références")
    args = ap.parse_args(argv)
    if args.neutre:
        if (Path(args.out) / "profile.json").exists() and not args.force:
            die(f"{args.out}/profile.json existe déjà (--force pour le remplacer par la DA neutre)")
        return neutre(Path(args.out))
    root = Path(args.root)
    out = Path(args.out)
    path = out / "profile.json"
    old = None
    if path.exists() and not args.force:
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            die(f"{path} illisible ({e}) : corrige-le ou relance avec --force")
    today = datetime.date.today()
    if args.ajoute:
        return ajoute(Path(args.ajoute), old, path, root, args, today)
    if not args.dossier:
        die("indique le dossier des vidéos de référence, ou --neutre")
    folder = Path(args.dossier)
    if not folder.is_dir():
        die(f"dossier introuvable : {folder}")
    videos = list_videos(folder)
    if not videos:
        die(f"aucune vidéo dans {folder} (formats : mp4, mov, mkv...)")
    if len(videos) < 5:
        print(f"Note : {len(videos)} vidéo(s) seulement ; 5 à 10 donnent une DA bien plus fiable.")
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    if args.sans_transcription:
        words = {v.as_posix(): (find_transcript(root, v.as_posix()) or {}).get("words") for v in videos}
    else:
        words = transcripts(videos, root, args.rapide, str(root / ".monteur" / "takes_references.md"))
    mes, budget, n_img = {}, max(1, MAX_IMAGES // len(videos)), 0
    for v in videos:
        print(f"Analyse de {v.name}...", flush=True)
        mes[v.as_posix()] = measure.measure(v, words.get(v.as_posix()))
        n_img += export_frames(v, frames_dir, budget)
        if (mes[v.as_posix()].get("sous_titres_presence") or 0) >= 0.3:
            n_img += export_bursts(v, frames_dir, words.get(v.as_posix()),
                                   mes[v.as_posix()].get("sous_titres_position_y_pct"))

    anciennes = (old or {}).get("sources") or []
    for s in anciennes:  # reels ajoutés un à un (--ajoute) : une relance sur le dossier ne les oublie pas
        if s.get("ajoute") and s["video"] not in mes and s["video"] in (old.get("mesures_brutes") or {}):
            mes[s["video"]] = old["mesures_brutes"][s["video"]]
    profile = build_profile(mes, sources_de(mes, anciennes, today, args.type))
    if old is not None:
        profile = merge_existing(profile, old)
        print("profile.json existant : champs complétés et verrouillés conservés (--force pour tout réécrire).")
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    style = out / "STYLE.md"
    if not style.exists():
        style.write_text(STYLE_MD, encoding="utf-8")

    print(summary(profile))
    sans_mots = [Path(k).name for k, w in words.items() if not w]
    if sans_mots:
        print(f"Sans transcript (respiration/hook non mesurés) : {', '.join(sans_mots)}.")
    print(f"{len(videos)} vidéo(s) mesurée(s), {n_img} images + planches (_sheet, _debut, _fin, _anim_entree, _anim_mots) dans {frames_dir}.")
    print(f"Profil : {path}. À compléter par Claude (en regardant les images) : "
          + (", ".join(profile["a_completer_par_claude"]) or "rien"))


def ajoute(video, old, path, root, args, today):
    """Une vidéo de plus dans le profil (médianes pondérées recalculées), sans re-mesurer les autres."""
    if old is None:
        die(f"{path} introuvable : apprends d'abord la DA (ou --neutre), puis ajoute des vidéos une à une")
    if not video.is_file():
        die(f"vidéo introuvable : {video}")
    key = video.as_posix()
    words = {} if args.sans_transcription else transcripts([video], root, args.rapide,
                                                           str(root / ".monteur" / "takes_ajout.md"))
    words = words.get(key) or (find_transcript(root, key) or {}).get("words")
    print(f"Analyse de {video.name}...", flush=True)
    mes = dict(old.get("mesures_brutes") or {})
    mes[key] = measure.measure(video, words)
    # profil d'avant les sources pondérées : ses références restent, sans date (comptent comme anciennes)
    anciennes = [s for s in old.get("sources") or [{"video": v, "type": type_source(v), "date": None} for v in mes]
                 if s["video"] != key]
    anciennes.append({"video": key, "type": args.type or "mien", "date": today.isoformat(), "ajoute": True})
    profile = merge_existing(build_profile(mes, sources_de(mes, anciennes, today)), old)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    src = next(s for s in profile["sources"] if s["video"] == key)
    print(f"{video.name} ajoutée au profil ({src['type']}, poids {src['poids']} ; {len(mes)} vidéos en tout). "
          f"Réglages verrouillés inchangés : {', '.join(profile.get('verrous') or []) or 'aucun'}.")
    print(summary(profile))


if __name__ == "__main__":
    main()
