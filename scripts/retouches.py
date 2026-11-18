"""Apprend de ses retouches : compare SA version finale à la nôtre → différences classées + règles proposées.

Usage : python scripts/retouches.py <sa_version> [--ref-plugin <notre sortie>] [--edl .monteur/edl.rendu.json]
                                    [--root .] [--transcrire | --sans-transcription]
- <sa_version> .fcpxml / .xml (Final Cut, DaVinci, Premiere) : comparée plan par plan à notre export du même
  format (défaut : <notre sortie>.fcpxml|.xml) : passages retirés/ajoutés, plans raccourcis/rallongés, coupes
  ajoutées/retirées, zooms retirés/ajoutés, b-roll retirés/ajoutés/déplacés.
- <sa_version> vidéo (CapCut…) : mesurée comme une référence (measure.py) et comparée à la mesure de notre sortie
  (rapport de check.py réutilisé s'il est à jour) ; même durée → images comparées instant par instant (plan changé,
  fond flou) ; durée différente (ou --transcrire) → sa vidéo est transcrite (transcribe.py, cache) et les phrases
  retirées/ajoutées sont retrouvées mot à mot.
Écrit .monteur/retouches/<date>.json et ajoute les différences fortes/moyennes à ma-da/corrections.jsonl.
Ne modifie JAMAIS le profil : la skill présente les règles et demande une validation groupée.
"""
import argparse
import datetime
import difflib
import json
import os
import re
import statistics
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

import numpy as np

# deux vidéos mesurées l'une après l'autre (et une transcription) : mode économe par défaut, avant d'importer common
os.environ.setdefault("MONTEUR_ECONOME", "1")
import check  # noqa: E402
import corrections  # noqa: E402
import measure  # noqa: E402
from common import VIDEO_EXT, die, find_transcript, probe, work_dir  # noqa: E402

ZOOM_MIN = 1.04          # échelle au-dessus de laquelle un plan est zoomé
BORD_S = 0.08            # écart de bord de plan (s) en dessous duquel ce n'est pas une retouche
DECALE_S = 0.25          # b-roll déplacé au-delà
ECART_IMAGE = 6.0        # différence moyenne (0-255, images 128 px) au-delà de laquelle l'image a changé
FLOU_RATIO = 0.6         # netteté des bandes haut/bas chez elle / chez nous en dessous : plan posé sur fond flou
CLIPS_FCP = {"asset-clip", "clip", "ref-clip", "video", "mc-clip", "sync-clip"}
FORCES = ("fort", "moyen", "faible")
# (clé de mesure, chemin profile.json, tolérance absolue, tolérance relative, libellé) : écart toléré = la plus grande
MESURES = [
    ("duree", "style_verbal.duree_typique_s", 0, 0.08, "durée de la vidéo (s)"),
    ("duree_moyenne_plan_s", "rythme.duree_moyenne_plan_s", 0, 0.15, "durée moyenne d'un plan (s)"),
    ("zooms_par_minute", "zooms.par_minute", 2.0, 0.3, "zooms par minute"),
    ("zoom_intensite", "zooms.intensite", 0.04, 0, "force des zooms (×)"),
    ("silence_max_s", "rythme.silence_max_s", 0.15, 0, "plus long silence (s)"),
    ("respiration.respiration_phrases_s", "rythme.respiration_phrases_s", 0.05, 0, "pause entre phrases (s)"),
    ("debit_mots_s", "rythme.debit_mots_s", 0, 0.1, "débit (mots/s)"),
    ("sous_titres_actif", "sous_titres.actif", None, None, "sous-titres"),
    ("sous_titres_hauteur_pct", "sous_titres.taille_pct_hauteur", 0, 0.15, "taille des sous-titres (part de la hauteur)"),
    ("sous_titres_position_y_pct", "sous_titres.position_y_pct", 0.05, 0, "hauteur des sous-titres (0 = haut, 1 = bas)"),
    ("emojis_par_minute", "sous_titres.emojis_par_minute", 2.0, 0.3, "emojis par minute"),
    ("couleur.luma", "couleur.luma", 0.04, 0, "luminosité de l'image"),
    ("couleur.contraste", "couleur.contraste", 0.04, 0, "contraste"),
    ("couleur.saturation", "couleur.saturation", 0.04, 0, "saturation"),
    ("couleur.temperature", "couleur.temperature", 0.04, 0, "température (chaud +)"),
    ("lufs", "audio.lufs_cible", 1.5, 0, "volume (LUFS)"),
    ("musique", "audio.musique", None, None, "musique"),
    ("musique_sous_voix", "audio.musique_db_sous_voix", 3.0, 0, "musique sous la voix (dB)"),
    ("broll_part", "broll.part_pct", 0.05, 0, "part de b-roll"),
    ("sfx_par_minute", "audio.sfx_par_minute", 2.0, 0.3, "bruitages par minute"),
    ("cadrage.visage_largeur", "cadrage.visage_largeur", 0, 0.15, "taille du visage (part de la largeur)"),
]
PAR_MOTS = {"silence_max_s", "respiration.respiration_phrases_s", "debit_mots_s", "musique_sous_voix", "sfx_par_minute"}
# différences plan par plan → réglage du profil concerné
CHEMIN = {"raccourci": ("rythme.respiration_phrases_s", "-"), "rallongé": ("rythme.respiration_phrases_s", "+"),
          "coupe ajoutée": ("rythme.duree_moyenne_plan_s", "-"), "coupe retirée": ("rythme.duree_moyenne_plan_s", "+"),
          "zoom retiré": ("zooms.par_minute", "-"), "zoom ajouté": ("zooms.par_minute", "+"),
          "zoom réduit": ("zooms.intensite", "-"), "zoom renforcé": ("zooms.intensite", "+"),
          "b-roll retiré": ("broll.part_pct", "-"), "b-roll ajouté": ("broll.part_pct", "+"),
          "b-roll raccourci": ("broll.duree_moy_s", "-"), "b-roll rallongé": ("broll.duree_moy_s", "+"),
          "fond flou": ("broll.fond_flou", "+"), "plein cadre": ("broll.fond_flou", "-")}
REGLE = {"rythme.respiration_phrases_s": ("couper plus serré autour des phrases", "laisser plus d'air autour des phrases"),
         "rythme.duree_moyenne_plan_s": ("plus de coupes (plans plus courts)", "moins de coupes (plans plus longs)"),
         "zooms.par_minute": ("moins de zooms", "plus de zooms"), "zooms.intensite": ("zooms plus légers", "zooms plus forts"),
         "broll.part_pct": ("moins de b-roll", "plus de b-roll"),
         "broll.duree_moy_s": ("b-roll plus courts", "b-roll plus longs"),
         "broll.fond_flou": ("b-roll plein cadre, pas sur fond flou",
                             "b-roll paysage posés entiers sur fond flou (\"fond_flou\": true), pas recadrés plein cadre")}


def fr(x, n=2):
    return f"{x:.{n}f}".replace(".", ",") if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x)


def _t(s):
    """Temps FCPXML « 1001/30000s » → secondes."""
    return float(Fraction((s or "0s").rstrip("s") or "0"))


def _nom(chemin):
    return Path(unquote(urlparse(chemin).path) if "://" in chemin else chemin).stem.lower()


# --- Lecture des timelines -------------------------------------------------------------------------------------
def lire_timeline(path):
    """{duree, main: [clips], broll: [clips]} ; clip = {src, at, in, len, zoom} en secondes (zoom hors recadrage)."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        die(f"XML illisible ({path}) : {e}")
    if root.tag == "fcpxml":
        tl = _fcpxml(root)
    elif root.tag == "xmeml":
        tl = _xmeml(root)
    else:
        die(f"{path} : ni FCPXML (Final Cut, DaVinci) ni XML Premiere (xmeml)")
    tl["duree"] = round(max((c["at"] + c["len"] for c in tl["main"]), default=0.0), 3)
    return tl


def _fcpxml(root):
    assets = {}
    for a in root.iter("asset"):
        mr = a.find("media-rep")
        src = mr.get("src") if mr is not None else a.get("src")
        assets[a.get("id")] = {"src": _nom(src) if src else (a.get("name") or "").lower(), "video": a.get("hasVideo") != "0"}

    def ref(e):
        r = e.get("ref") or next((d.get("ref") for d in e.iter() if d.get("ref") and d.get("lane") is None), None)
        return assets.get(r, {"src": (e.get("name") or "").lower(), "video": True})

    def zoom(e):
        vals = []
        for tr in e.findall("adjust-transform"):
            if tr.get("scale"):
                vals.append(float(tr.get("scale").split()[0]))
            for p in tr.iter("param"):
                if p.get("name") == "scale":
                    vals += [float(k.get("value").split()[0]) for k in p.iter("keyframe")]
                    if p.get("value"):
                        vals.append(float(p.get("value").split()[0]))
        return max(vals, default=1.0)

    seq = root.find(".//sequence")
    spine = seq.find("spine") if seq is not None else None
    if spine is None:
        die("FCPXML sans timeline (sequence/spine)")
    main, broll = [], []
    for c in spine:
        if c.tag not in CLIPS_FCP | {"gap"}:
            continue
        at, start, ln = _t(c.get("offset")), _t(c.get("start")), _t(c.get("duration"))
        if c.tag != "gap":
            main.append({"src": ref(c)["src"], "at": round(at, 3), "in": round(start, 3), "len": round(ln, 3),
                         "zoom": zoom(c)})
        for g in c:  # clips connectés : placés dans le temps source de leur parent
            if g.tag in CLIPS_FCP and int(g.get("lane") or 0) > 0 and ref(g)["video"]:
                broll.append({"src": ref(g)["src"], "at": round(at + _t(g.get("offset")) - start, 3),
                              "in": round(_t(g.get("start")), 3), "len": round(_t(g.get("duration")), 3),
                              "zoom": zoom(g)})
    return {"main": main, "broll": sorted(broll, key=lambda b: b["at"])}


def _rate(e, defaut):
    r = e.find("rate")
    if r is None or r.find("timebase") is None:
        return defaut
    tb = float(r.find("timebase").text)
    return tb * 1000 / 1001 if (r.findtext("ntsc") or "").upper() == "TRUE" else tb


def _xmeml(root):
    seq = root.find("sequence") if root.find("sequence") is not None else root.find(".//sequence")
    fps = _rate(seq, 30.0)
    sc = seq.find("media/video/format/samplecharacteristics")
    W, H = (float(sc.findtext("width") or 1080), float(sc.findtext("height") or 1920)) if sc is not None else (1080, 1920)
    files = {}
    for f in root.iter("file"):
        name = f.findtext("name") or f.findtext("pathurl")
        if name:
            v = f.find("media/video/samplecharacteristics")
            dims = (float(v.findtext("width")), float(v.findtext("height"))) if v is not None and v.findtext("width") else None
            files[f.get("id")] = (_nom(name), dims)
    tracks = []
    for tr in seq.findall("media/video/track"):
        clips = []
        for ci in tr.findall("clipitem"):
            if (ci.findtext("enabled") or "TRUE").upper() == "FALSE" or ci.find("file") is None:
                continue
            start, end, inn = (int(float(ci.findtext(k) or -1)) for k in ("start", "end", "in"))
            # shortcut: un plan collé à une transition a start/end = -1 dans Premiere : ignoré ; à calculer depuis ses voisins si besoin
            if start < 0 or end <= start:
                continue
            src, dims = files.get(ci.find("file").get("id"), ((ci.findtext("name") or "").lower(), None))
            r = _rate(ci, fps)
            scale = next((float(p.findtext("value")) for p in ci.iter("parameter")
                          if p.findtext("parameterid") == "scale" and p.findtext("value")), 100.0)
            cover = max(W / dims[0], H / dims[1]) if dims else 1.0  # Premiere : échelle = recadrage 9:16 × zoom
            clips.append({"src": src, "at": round(start / r, 3), "in": round(inn / r, 3), "len": round((end - start) / r, 3),
                          "zoom": round(scale / 100 / cover, 3)})
        tracks.append(clips)
    return {"main": tracks[0] if tracks else [], "broll": sorted((c for t in tracks[1:] for c in t), key=lambda b: b["at"])}


def resume_timeline(tl):
    """Mesures globales d'une timeline, aux clés de measure.py (comparables par compare_mesures)."""
    d = max(tl["duree"], 1e-6)
    zs = [c["zoom"] for c in tl["main"] if c["zoom"] > ZOOM_MIN]
    return {"duree": tl["duree"], "duree_moyenne_plan_s": round(d / max(len(tl["main"]), 1), 3),
            "zooms_par_minute": round(len(zs) / d * 60, 2), "zoom_intensite": round(statistics.mean(zs), 3) if zs else None,
            "broll_part": round(sum(b["len"] for b in tl["broll"]) / d, 3)}


def _chevauche(a, b):
    return min(a["in"] + a["len"], b["in"] + b["len"]) - max(a["in"], b["in"])


def comparer_timelines(nous, sa):
    """Différences plan par plan [{quoi, t, texte}] (t = temps dans notre version)."""
    out = []

    def note(quoi, t, texte):
        out.append({"quoi": quoi, "t": round(t, 2), "texte": texte})

    lies = {id(h): [] for h in sa["main"]}
    for o in nous["main"]:
        hs = [h for h in sa["main"] if h["src"] == o["src"] and _chevauche(o, h) > 0.05]
        for h in hs:
            lies[id(h)].append(o)
        quand = f"{o['src']} {fr(o['in'])}-{fr(o['in'] + o['len'])} s"
        if not hs:
            note("passage retiré", o["at"], f"passage retiré ({quand})")
            continue
        if len(hs) >= 2:
            note("coupe ajoutée", o["at"], f"{len(hs) - 1} coupe(s) ajoutée(s) dans le plan {quand}")
        if len(hs) == 1 and len([x for x in nous["main"] if x["src"] == o["src"] and _chevauche(x, hs[0]) > 0.05]) == 1:
            h = hs[0]
            enleve = (h["in"] - o["in"]) + (o["in"] + o["len"] - h["in"] - h["len"])
            if abs(enleve) > BORD_S:
                note("raccourci" if enleve > 0 else "rallongé", o["at"],
                     f"plan {quand} {'raccourci' if enleve > 0 else 'rallongé'} de {fr(abs(enleve))} s")
        hz = max(h["zoom"] for h in hs)
        if o["zoom"] > ZOOM_MIN and hz <= ZOOM_MIN:
            note("zoom retiré", o["at"], f"zoom ×{fr(o['zoom'])} retiré ({quand})")
        elif o["zoom"] <= ZOOM_MIN and hz > ZOOM_MIN:
            note("zoom ajouté", o["at"], f"zoom ×{fr(hz)} ajouté ({quand})")
        elif o["zoom"] > ZOOM_MIN and abs(hz - o["zoom"]) > 0.03:
            note("zoom réduit" if hz < o["zoom"] else "zoom renforcé", o["at"],
                 f"zoom ×{fr(o['zoom'])} → ×{fr(hz)} ({quand})")
    for h in sa["main"]:
        if not lies[id(h)]:
            note("passage ajouté", h["at"], f"passage ajouté ({h['src']} {fr(h['in'])}-{fr(h['in'] + h['len'])} s)")
        elif len(lies[id(h)]) >= 2:
            note("coupe retirée", lies[id(h)][0]["at"], f"{len(lies[id(h)]) - 1} coupe(s) retirée(s) ({h['src']} "
                 f"{fr(h['in'])}-{fr(h['in'] + h['len'])} s d'un seul plan)")
    libres = list(sa["broll"])
    for o in nous["broll"]:
        cand = [h for h in libres if h["src"] == o["src"]]
        if not cand:
            note("b-roll retiré", o["at"], f"b-roll {o['src']} retiré (à {fr(o['at'])} s)")
            continue
        h = min(cand, key=lambda h: abs(h["at"] - o["at"]))
        libres.remove(h)
        if abs(h["at"] - o["at"]) > DECALE_S:
            note("b-roll déplacé", o["at"], f"b-roll {o['src']} déplacé de {fr(o['at'])} s à {fr(h['at'])} s")
        if abs(h["len"] - o["len"]) > BORD_S * 2:
            note("b-roll raccourci" if h["len"] < o["len"] else "b-roll rallongé", o["at"],
                 f"b-roll {o['src']} : {fr(o['len'])} s → {fr(h['len'])} s")
    for h in libres:
        note("b-roll ajouté", h["at"], f"b-roll {h['src']} ajouté (à {fr(h['at'])} s, {fr(h['len'])} s)")
    return sorted(out, key=lambda d: d["t"])


# --- Comparaison des mesures -----------------------------------------------------------------------------------
def _val(m, key):
    if key == "sous_titres_actif":
        return None if m.get("sous_titres_presence") is None else m["sous_titres_presence"] >= 0.3
    if key == "musique":
        if m.get("fond_sonore_db") is None:
            return None
        return m["fond_sonore_db"] > measure.FOND_MUSIQUE_DB and (m.get("musique_tonalite") is None
                                                                   or m["musique_tonalite"] >= measure.MUSIQUE_TONALITE)
    if key == "musique_sous_voix":
        return None if m.get("musique_lufs") is None or m.get("lufs") is None else round(m["musique_lufs"] - m["lufs"], 1)
    for k in key.split("."):
        m = m.get(k) if isinstance(m, dict) else None
    return m


def compare_mesures(nous, sa, avec_mots=True):
    """Différences de mesures globales → [{force, chemin, sens, regle, proposition}].
    avec_mots=False : une des deux mesures n'a pas eu de mots, celles qui en dépendent ne se comparent pas."""
    out = []
    st_ok = _val(nous, "sous_titres_actif") and _val(sa, "sous_titres_actif")
    for key, chemin, tol, rel, lib in MESURES:
        a, b = _val(nous, key), _val(sa, key)
        if a is None or b is None or (key.startswith(("sous_titres_h", "sous_titres_p", "emojis")) and not st_ok)                 or (not avec_mots and key in PAR_MOTS):
            continue
        if tol is None:  # oui / non
            if a != b:
                out.append({"force": "fort", "chemin": chemin, "sens": "+" if b else "-", "proposition": b,
                            "regle": f"{lib} : {'ajoutés' if b else 'retirés'} dans ta version"})
            continue
        t = max(tol, rel * abs(a))
        r = abs(b - a) / max(t, 1e-9)
        if r < 1:
            continue
        out.append({"force": "fort" if r >= 3 else "moyen" if r >= 1.5 else "faible", "chemin": chemin,
                    "sens": "+" if b > a else "-", "proposition": round(b) if key == "duree" else round(b, 3),
                    "regle": f"{lib} : {fr(b)} dans ta version, {fr(a)} dans la mienne"})
    return out


def regles(mesurees, details):
    """Règles = écarts de mesures + différences plan par plan regroupées par réglage (exemples joints)."""
    out = list(mesurees)
    groupes = {}
    for d in details:
        if d["quoi"] in CHEMIN:
            groupes.setdefault(CHEMIN[d["quoi"]], []).append(d["texte"])
    for (chemin, sens), ex in groupes.items():
        r = next((x for x in out if x["chemin"] == chemin and x["sens"] == sens), None)
        if r is None:
            n = len(ex)
            r = {"force": "fort" if n >= 3 else "moyen" if n == 2 else "faible", "chemin": chemin, "sens": sens,
                 "proposition": True if chemin == "broll.fond_flou" and sens == "+" else None,
                 "regle": REGLE.get(chemin, (chemin + " −", chemin + " +"))[sens == "+"] + f" ({n} fois)"}
            out.append(r)
        r["exemples"] = ex[:3]
    return sorted(out, key=lambda r: FORCES.index(r["force"]))


# --- Vidéo : mêmes instants, mots retirés ----------------------------------------------------------------------
def _nettete(frames):
    """Netteté des bandes haut et bas (12 %) : un plan posé sur fond flou y est flou."""
    h = frames.shape[1]
    k = max(2, h * 12 // 100)
    bandes = np.concatenate([frames[:, :k], frames[:, -k:]], axis=1).astype(np.int16)
    return float(np.abs(np.diff(bandes, axis=2)).mean() + np.abs(np.diff(bandes, axis=1)).mean())


def zones_changees(sa_frames, nous_frames, fps, rendu=None):
    """Instants où l'image diffère (même durée) → [{quoi, t, texte}], plan nommé d'après edl.rendu.json."""
    n = min(len(sa_frames), len(nous_frames))
    d = np.abs(sa_frames[:n].astype(np.int16) - nous_frames[:n].astype(np.int16)).mean(axis=(1, 2))
    zones, debut = [], None
    for i, x in enumerate(list(d > ECART_IMAGE) + [False]):
        if x and debut is None:
            debut = i
        elif not x and debut is not None:
            if i - debut >= 2:  # une image isolée = bruit d'encodage
                zones.append((debut, i))
            debut = None
    out = []
    for a, b in zones:
        t0, t1 = a / fps, b / fps
        br = next((x for x in (rendu or {}).get("broll") or []
                   if x["at"] - 0.2 <= (t0 + t1) / 2 < x["at"] + x["duration"] + 0.2), None)
        quoi_plan = f"b-roll {Path(br['source']).stem}" if br and br.get("source") else "image"
        ratio = _nettete(sa_frames[a:b]) / max(_nettete(nous_frames[a:b]), 1e-6)
        if ratio < FLOU_RATIO:
            out.append({"quoi": "fond flou", "t": round(t0, 2),
                        "texte": f"{quoi_plan} ({fr(t0, 1)}-{fr(t1, 1)} s) : posé entier sur fond flou au lieu de plein cadre"})
        elif ratio > 1 / FLOU_RATIO:
            out.append({"quoi": "plein cadre", "t": round(t0, 2),
                        "texte": f"{quoi_plan} ({fr(t0, 1)}-{fr(t1, 1)} s) : plein cadre au lieu de fond flou"})
        else:
            out.append({"quoi": "image changée", "t": round(t0, 2),
                        "texte": f"{quoi_plan} ({fr(t0, 1)}-{fr(t1, 1)} s) : image différente (plan remplacé ou recadré)"})
    return out


def _norm(w):
    return re.sub(r"[^\w']", "", w.lower())


def phrases_changees(nous_mots, sa_mots, min_mots=2):
    """Alignement mot à mot → [{quoi, t, texte}] : passages d'au moins `min_mots` mots retirés ou ajoutés."""
    a = [_norm(w["w"]) for w in nous_mots]
    b = [_norm(w["w"]) for w in sa_mots]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("delete", "replace") and i2 - i1 >= min_mots:
            out.append({"quoi": "phrase retirée", "t": nous_mots[i1]["start"],
                        "texte": "retiré : « " + " ".join(w["w"].strip() for w in nous_mots[i1:i2]) + " »"})
        if op in ("insert", "replace") and j2 - j1 >= min_mots:
            out.append({"quoi": "phrase ajoutée", "t": sa_mots[j1]["start"],
                        "texte": "ajouté : « " + " ".join(w["w"].strip() for w in sa_mots[j1:j2]) + " »"})
    return out


def mesure_notre(video, root, rendu):
    """Mesure de notre sortie : rapport de check.py s'il est plus récent que la vidéo, sinon measure.py."""
    rap = root / ".monteur" / "rapports" / f"{video.stem}.json"
    if rap.is_file() and rap.stat().st_mtime >= video.stat().st_mtime:
        m = json.loads(rap.read_text(encoding="utf-8")).get("mesures")
        if m:
            return m
    words = check.output_words(rendu, root)[0] if rendu else None
    print(f"Mesure de {video.name}...", flush=True)
    return measure.measure(video, words)


def sa_transcription(video, root):
    takes = work_dir(root) / "retouches" / f"takes_{video.stem}.md"  # ne remplace pas .monteur/takes.md
    takes.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(Path(__file__).with_name("transcribe.py")), str(video), "--root", str(root),
           "--takes", str(takes)]
    if subprocess.run(cmd).returncode != 0:  # hérite de MONTEUR_ECONOME
        print("Transcription de ta version impossible : phrases retirées non comparées.")
        return None
    return (find_transcript(root, os.path.relpath(video, root)) or {}).get("words")


# --- Programme -------------------------------------------------------------------------------------------------
def ecrire(root, rapport, video_nous):
    d = work_dir(root) / "retouches"
    d.mkdir(parents=True, exist_ok=True)
    base = datetime.date.today().isoformat()
    out, k = d / f"{base}.json", 2
    while out.exists():
        out, k = d / f"{base}-{k}.json", k + 1
    out.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    n = 0
    for r in rapport["differences"]:
        if r["force"] in ("fort", "moyen") and r.get("chemin"):
            corrections.ajoute(root, r["chemin"], r["sens"], r["regle"], "video", video_nous, source="retouche")
            n += 1
    return out, n


def main():
    ap = argparse.ArgumentParser(description="Compare sa version retouchée à la nôtre et propose des règles (sans rien appliquer).")
    ap.add_argument("sa_version", help="sa vidéo finale (CapCut…) ou son export .fcpxml / .xml")
    ap.add_argument("--ref-plugin", help="notre sortie (vidéo, ou .fcpxml/.xml) ; défaut : celle de edl.rendu.json")
    ap.add_argument("--edl", help="défaut : .monteur/edl.rendu.json")
    ap.add_argument("--root", default=".", help="dossier de travail (contient .monteur/ et ma-da/)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--transcrire", action="store_true", help="transcrire sa vidéo même si la durée est la même")
    g.add_argument("--sans-transcription", action="store_true", help="ne jamais transcrire sa vidéo")
    args = ap.parse_args()
    root = Path(args.root)
    sa = Path(args.sa_version)
    if not sa.is_file():
        die(f"introuvable : {sa}")
    edl_path = Path(args.edl) if args.edl else root / ".monteur" / "edl.rendu.json"
    rendu = json.loads(edl_path.read_text(encoding="utf-8")) if edl_path.is_file() else None
    xml = sa.suffix.lower() in (".xml", ".fcpxml")
    if args.ref_plugin:
        nous = Path(args.ref_plugin)
    elif rendu and rendu.get("output"):
        nous = root / rendu["output"]
        nous = nous.with_suffix(sa.suffix.lower()) if xml else nous
    else:
        die("indique notre version avec --ref-plugin (aucun .monteur/edl.rendu.json)")
    if not nous.is_file():
        die(f"notre version introuvable : {nous}" + (" (lance export_xml.py, ou --ref-plugin)" if xml else ""))
    if rendu and Path(rendu.get("output", "")).stem != nous.stem:
        rendu = None  # edl d'une autre version : ses temps ne valent pas pour celle-ci

    details, phrases = [], []
    if xml:
        if nous.suffix.lower() not in (".xml", ".fcpxml"):
            die("sa version est un export XML : donne notre .fcpxml/.xml en --ref-plugin")
        tn, ts = lire_timeline(nous), lire_timeline(sa)
        details = comparer_timelines(tn, ts)
        mesurees = compare_mesures(resume_timeline(tn), resume_timeline(ts))
    else:
        if sa.suffix.lower() not in VIDEO_EXT:
            die(f"format non reconnu : {sa.suffix} (vidéo, .fcpxml ou .xml)")
        mn = mesure_notre(nous, root, rendu)
        d_sa = probe(sa)["duration"]
        mots_nous = check.output_words(rendu, root)[0] if rendu else None
        mots_sa = None
        meme = abs(d_sa - mn["duree"]) <= 0.2  # même montage retouché : mêmes mots, images comparables instant par instant
        if args.transcrire or (not args.sans_transcription and abs(d_sa - mn["duree"]) > 0.5):
            mots_sa = sa_transcription(sa, root)
        elif meme:
            mots_sa = mots_nous
        print(f"Mesure de {sa.name}...", flush=True)
        ms = measure.measure(sa, mots_sa)
        mesurees = compare_mesures(mn, ms, avec_mots=bool(mots_sa and mots_nous))
        if meme:
            fps = 5
            details = zones_changees(measure.decode(sa, measure.ANALYSE_LARGEUR, fps=fps),
                                     measure.decode(nous, measure.ANALYSE_LARGEUR, fps=fps), fps, rendu)
        if mots_sa and mots_nous:
            phrases = phrases_changees(mots_nous, mots_sa)
    diffs = regles(mesurees, details)
    rapport = {"date": datetime.date.today().isoformat(), "projet": root.resolve().name, "sa_version": str(sa),
               "notre_version": str(nous), "mode": "xml" if xml else "video", "differences": diffs,
               "details": details, "phrases": phrases}
    out, n = ecrire(root, rapport, nous.stem)

    print(f"Ta version ({sa.name}) comparée à la mienne ({nous.name}) : {len(diffs)} différence(s) de style.")
    for r in diffs:
        print(f"- {r['force'].upper()} [{r['chemin']} {r['sens']}] {r['regle']}"
              + (f" → proposé : {r['proposition']}" if r.get("proposition") is not None else ""))
        for ex in r.get("exemples", []):
            print(f"    · {ex}")
    autres = [d for d in details if d["quoi"] not in CHEMIN]
    for d in autres + phrases:  # contenu de CETTE vidéo : à dire, pas une règle
        print(f"- (cette vidéo) {fr(d['t'], 1)} s : {d['texte']}")
    print(f"Écrit : {out} ; {n} différence(s) notée(s) dans {corrections.journal(root)}.")
    print("Rien n'est appliqué au profil : propose-lui ces règles en une question groupée.")


if __name__ == "__main__":
    main()
