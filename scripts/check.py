"""Contrôle avant livraison : mesure la vidéo produite et la compare au profil DA (mêmes mesures que analyze_style).

Usage : python scripts/check.py sorties/video.mp4 [--profil ma-da/profile.json] [--duree-cible 45] [--edl .monteur/edl.json]
Lit aussi .monteur/edl.rendu.json (ce qui a vraiment été rendu) quand il existe : mots coupés, b-roll sur le hook,
respiration de la sortie. Écrit .monteur/rapports/<stem>.json. Code de sortie 0 même si non conforme.
"""
import argparse
import json
from pathlib import Path

import measure
from common import die, find_transcript, work_dir

SEUIL_SOUS_TITRES = 0.3  # part d'images avec du texte pour dire « sous-titres présents »


def fr(x, n=2):
    return f"{x:.{n}f}".replace(".", ",")


def output_words(edl, root):
    """Mots dans le temps de la sortie (depuis les transcripts des rushs) + mots coupés aux raccords."""
    words, coupes, t = [], [], 0.0
    for seg in (edl or {}).get("segments", []):
        a, b = float(seg["start"]), float(seg["end"])
        tr = find_transcript(root, seg["source"])
        for w in (tr or {}).get("words", []):
            if w["end"] <= a + 0.02 or w["start"] >= b - 0.02:
                continue
            if w["start"] < a - 0.03 or w["end"] > b + 0.03:
                coupes.append({"mot": w["w"].strip(), "a_s": round(t + max(0.0, w["start"] - a), 2),
                               "source": seg["source"]})
                continue
            words.append({"w": w["w"], "start": round(t + max(0.0, w["start"] - a), 3),
                          "end": round(t + min(b, w["end"]) - a, 3)})
        t += b - a
    return words, coupes, t


def hook_end(edl, profile):
    """Fin du hook dans la sortie : dernier segment noté « hook », sinon hook.duree_s du profil."""
    t, end = 0.0, None
    for seg in (edl or {}).get("segments", []):
        t += seg["end"] - seg["start"]
        if "hook" in str(seg.get("note", "")).lower():
            end = t
    return end if end is not None else float(profile.get("hook", {}).get("duree_s") or 3)


def edl_measures(edl, root, profile):
    """Ce que l'edl rendu permet de vérifier exactement."""
    if not edl or not edl.get("segments"):
        return {}
    words, coupes, total = output_words(edl, root)
    h = hook_end(edl, profile)
    br = edl.get("broll") or []
    minutes = max(total, 1e-6) / 60
    colles = [b.get("plan") for a, b in zip(br, br[1:]) if a.get("scene") and a.get("scene") == b.get("scene")]         if edl.get("mode") == "voix_off" else []
    return {"words": words, "mots_coupes": coupes, "hook_fin_s": round(h, 2), "plans_meme_scene": colles,
            "broll_sur_hook": [b for b in br if b["at"] < h - 0.05],
            "broll_part": round(sum(b["duration"] for b in br) / max(total, 1e-6), 3),
            "sfx_par_minute": round(len(edl.get("sfx") or []) / minutes, 2)}


def dans_plage(profile, key, v, marge=0.05):
    """La valeur tombe dans la plage de SES vidéos (min-max des références, ±5 %) : c'est son style, même si elle
    s'écarte de la médiane. Faux sans plage connue (moins de 3 références)."""
    d = (profile.get("dispersion") or {}).get(key)
    return bool(d) and v is not None and d["min"] * (1 - marge) <= v <= d["max"] * (1 + marge)


def compare(m, profile, duree_cible=None, edl=None, e=None):
    ecarts = []
    e = e or {}
    voix_off = (edl or {}).get("mode") == "voix_off"

    def ecart(critere, attendu, mesure, tolerance, conseil):
        ecarts.append({"critere": critere, "attendu": attendu, "mesure": mesure,
                       "tolerance": tolerance, "conseil": conseil})

    r = profile.get("rythme", {})
    z = profile.get("zooms", {})
    cible = r.get("duree_moyenne_plan_s")
    plan = m["duree_moyenne_plan_s"]
    if cible and abs(plan - cible) > 0.15 * cible and not dans_plage(profile, "duree_moyenne_plan_s", plan):
        besoin = round(m["duree"] / cible) - len(m["plans"])
        conseil = (f"plans trop longs : ajouter ~{besoin} coupe(s) (couper plus serré, retirer respirations "
                   f"et hésitations, ou alterner des punch-ins ×{z.get('intensite', 1.15)})"
                   if besoin > 0 else
                   f"plans trop courts : retirer ~{-besoin} coupe(s) (fusionner des segments voisins, moins de zooms)")
        ecart("duree_moyenne_plan_s", cible, plan, "±15%", conseil)

    q, mq = r.get("plans_quantiles"), m.get("plans_quantiles")
    if q and mq and len(q) == 5 and len(m["plans"]) >= 6:
        for i, nom in ((1, "courts"), (3, "longs")):  # 25 % et 75 % : la forme du rythme, pas seulement sa moyenne
            if abs(mq[i] - q[i]) > 0.25 * q[i]:
                plus = mq[i] > q[i]
                ecart(f"plans_{nom}_s", q[i], mq[i], "±25%",
                      f"tes plans {nom} durent {fr(q[i])} s, ceux de la vidéo {fr(mq[i])} s : "
                      + ("couper plus souvent " if plus else "laisser durer davantage ")
                      + ("les plans les plus courts" if i == 1 else "les plans les plus longs")
                      + " (alterne court / long comme dans tes vidéos)")
    hp, mhp = r.get("hook_plan_s"), m.get("hook_plan_s")
    if hp and mhp and abs(mhp - hp) > 0.3 * hp and not dans_plage(profile, "hook_plan_s", mhp):
        ecart("hook_plan_s", hp, mhp, "±30%",
              f"rythme de l'accroche (3 premières s) : un plan toutes les {fr(mhp)} s au lieu de {fr(hp)} s → "
              + ("ajouter une coupe ou un zoom dans les 3 premières secondes" if mhp > hp else
                 "moins de coupes dans les 3 premières secondes"))

    cad, mcad = profile.get("cadrage") or {}, m.get("cadrage") or {}
    if not voix_off and cad.get("visage_largeur") and mcad.get("visage_largeur"):
        cv, mv = cad["visage_largeur"], mcad["visage_largeur"]
        if abs(mv - cv) > 0.25 * cv:
            ecart("cadrage_visage", cv, mv, "±25%",
                  f"visage {'trop gros' if mv > cv else 'trop petit'} dans le cadre ({round(100 * mv)} % de la largeur "
                  f"au lieu de {round(100 * cv)} %) : le rendu cadre d'après profile.cadrage ; vérifier que le visage "
                  "est détecté (zoom_center \"visage\")" + (" — le rush est peut-être déjà plus serré que ta DA"
                                                             if mv > cv else ""))

    bord_ref, bord = profile.get("visage_bord_max") or 0.0, (mcad or {}).get("visage_bord") or 0.0
    # shortcut: boîte du visage sans les yeux (lunettes au bord comptées) → seuil large ; points des yeux YuNet si faux positifs
    if not voix_off and bord > max(0.20, bord_ref + 0.10):
        ecart("visage_coupe", bord_ref, bord, "comme tes vidéos",
              f"visage coupé par le bord du cadre sur {round(100 * bord)} % des plans face caméra (un œil sort) : "
              "zoom trop fort ou visage mal suivi → baisser le zoom de ces segments ou zoom_center \"visage\"")
    noir_max = profile.get("images_noires_max") or 0.0
    if (m.get("images_noires") or 0.0) > noir_max + 0.01:
        ecart("images_noires", noir_max, m["images_noires"], "comme tes vidéos",
              f"{round(100 * m['images_noires'])} % d'images noires : un trou dans le montage (plan manquant, "
              "b-roll trop court ou voix off non couverte) → vérifier les plans de l'edl")
    tp, tp_ref = m.get("true_peak_db"), (profile.get("audio") or {}).get("true_peak_max_db")
    if tp is not None and tp > max(0.0, tp_ref if tp_ref is not None else -1.0) + 0.1:
        ecart("saturation_son", f"≤ {fr(max(0.0, tp_ref or -1.0), 1)} dBTP", tp, "pas de saturation",
              "le son sature (crêtes au-dessus de 0 dB) : relancer le rendu ; si ça persiste, baisser la musique")

    smax = r.get("silence_max_s")
    if smax is not None and m["silence_max_s"] > smax + 0.15:
        pire = max((s for s in m.get("silences_mots") or m["silences"]
                    if 0.05 < s["start"] and s["end"] < m["duree"] - 0.05),
                   key=lambda s: s["end"] - s["start"], default={"start": 0.0})
        ecart("silence_max_s", smax, m["silence_max_s"], f"≤ {smax} + 0.15 s",
              f"silence de {fr(m['silence_max_s'])} s à {fr(pire['start'])} s dans la sortie : raccourcir ce "
              f"raccord (fin du segment ou début du suivant) à ≤ {fr(smax)} s")

    resp = m.get("respiration") or {}
    refs = (profile.get("mesures_brutes") or {}).values()
    for k, nom in (("respiration_phrases_s", "entre deux phrases"), ("respiration_intra_s", "dans une phrase")):
        want, got = r.get(k), resp.get(k)
        # valeur par défaut (références sans transcript) : rien à comparer, sauf réglage imposé par l'utilisateur
        mesure_ref = any((x.get("respiration") or {}).get(k) is not None for x in refs) or not refs
        if want and got is not None and (mesure_ref or f"rythme.{k}" in profile.get("verrous", [])) and abs(got - want) > 0.3 * want:
            plus = got > want
            ecart(k, want, got, "±30%",
                  f"pauses {nom} trop {'longues' if plus else 'courtes'} ({fr(got)} s au lieu de {fr(want)} s) : "
                  + ("resserrer les bornes (end = fin du dernier mot + 0,1 s, start = début du mot − 0,05 s)"
                     if plus else "laisser plus d'air aux raccords (bornes moins serrées)")
                  + " ; le rendu recale sur rythme." + k)

    lufs = profile.get("audio", {}).get("lufs_cible")
    if lufs is not None:
        if m["lufs"] is None:
            ecart("lufs", lufs, None, "±1.5 LU", "pas de piste audio dans la sortie : vérifier render.py")
        elif abs(m["lufs"] - lufs) > 1.5:
            ecart("lufs", lufs, m["lufs"], "±1.5 LU",
                  f"volume à {'baisser' if m['lufs'] > lufs else 'monter'} de {fr(abs(m['lufs'] - lufs), 1)} dB "
                  f"(normalisation finale à {lufs} LUFS au rendu)")

    for k, v in profile.get("format", {}).items():
        got = m["format"].get(k)
        if got is None or abs(got - v) > (0.05 if k == "fps" else 0):
            ecart(k, v, got, "exact", f"rendre en {profile['format'].get('width')}x{profile['format'].get('height')} "
                  f"à {profile['format'].get('fps')} i/s (edl.json → format)")

    if e.get("plans_meme_scene"):
        ecart("plans_meme_scene", 0, e["plans_meme_scene"], "aucun",
              "deux plans de la même scène se suivent (la coupe ne se voit pas, le plan paraît trop long) : "
              f"remplacer {', '.join(map(str, e['plans_meme_scene']))} par un plan d'une autre scène (plans.md)")

    zpm = z.get("par_minute")
    # voix off : les zooms de la DA sont des zooms sur le visage, sans objet sur des plans d'illustration
    if not voix_off and zpm is not None and (zpm > 0 or m["zooms_par_minute"] > 2):
        if abs(m["zooms_par_minute"] - zpm) > max(0.35 * zpm, 1.0):
            n = round(abs(m["zooms_par_minute"] - zpm) * m["duree"] / 60)
            mix = ", ".join(f"{k} {round(100 * v)} %" for k, v in (z.get("types") or {}).items())
            ecart("zooms_par_minute", zpm, m["zooms_par_minute"], "±35%",
                  (f"ajouter ~{n} zoom(s) en début de phrase ou sur les phrases fortes (un toutes les "
                   f"{z.get('une_coupe_sur', 2)} coupes" + (f", mélange {mix}" if mix else "") + ")")
                  if m["zooms_par_minute"] < zpm else
                  f"retirer ~{n} zoom(s) : garder ceux des débuts de phrase et des mots forts")

    st_profil = profile.get("sous_titres", {}).get("actif")
    st_edl = (edl or {}).get("captions")
    present = m["sous_titres_presence"] >= SEUIL_SOUS_TITRES
    if st_profil and st_edl is False:
        ecart("sous_titres", "présents", "désactivés dans l'edl", "présents",
              "la DA a des sous-titres : mettre \"captions\": true dans .monteur/edl.json puis relancer le rendu")
    elif (st_profil or st_edl) and not present:
        ecart("sous_titres", "présents", f"détectés sur {round(100 * m['sous_titres_presence'])} % des images",
              "présents", "sous-titres absents ou peu visibles : vérifier qu'ils sont brûlés (captions.py) "
              "et contrastés (texte clair + contour sombre)")

    taille, mesure_t = profile.get("sous_titres", {}).get("taille_pct_hauteur"), m.get("sous_titres_hauteur_pct")
    if present and taille and mesure_t and abs(mesure_t - taille) > 0.25 * taille:
        ecart("sous_titres_taille", taille, mesure_t, "±25%",
              f"sous-titres trop {'grands' if mesure_t > taille else 'petits'} (hauteur des capitales "
              f"{fr(100 * mesure_t, 1)} % de l'image) : vérifier sous_titres.taille_pct_hauteur et la police")

    part_cible = profile.get("sous_titres", {}).get("mots_cles_part_blocs")
    part = (m.get("sous_titres_couleurs") or {}).get("accent_part_blocs")
    if present and part_cible and part is not None and abs(part - part_cible) > 0.4 * part_cible:
        ecart("mots_cles_part_blocs", part_cible, part, "±40%",
              f"{'trop' if part > part_cible else 'pas assez'} de mots en couleur ({round(100 * part)} % des blocs au "
              f"lieu de {round(100 * part_cible)} %) : " + ("retirer des mots_cles (garder chiffres, négations, mots "
                                                            "forts)" if part > part_cible else
                                                            "ajouter des mots_cles sur les mots forts"))

    b = profile.get("broll", {})
    part = m.get("broll_part") if m.get("broll_part") is not None else e.get("broll_part")
    if not voix_off and b.get("part_pct") and b["part_pct"] >= 0.05 and part is not None and abs(part - b["part_pct"]) > 0.4 * b["part_pct"]:
        plus = part > b["part_pct"]
        ecart("broll_part", b["part_pct"], part, "±40%",
              f"{'trop' if plus else 'pas assez'} de b-roll ({round(100 * part)} % du temps au lieu de "
              f"{round(100 * b['part_pct'])} %) : " + ("retirer les plans de coupe les moins liés à un mot"
                                                       if plus else "poser des b-roll sur les noms concrets dits "
                                                       f"(~{fr(b.get('duree_moy_s') or 1.8, 1)} s chacun, champ \"mot\")"))
    if not voix_off and e.get("broll_sur_hook") and b.get("jamais_sur_hook", True):
        ecart("broll_sur_hook", "aucun", [x["source"] for x in e["broll_sur_hook"]], "0 avant la fin du hook",
              f"le hook (0-{fr(e['hook_fin_s'], 1)} s) doit rester sur le visage : décaler ou retirer ces b-roll")

    sfx_cible = profile.get("audio", {}).get("sfx_par_minute")
    # nos bruitages sont dans l'edl (compte exact) ; l'oreille du code ne sert que sans edl
    sfx_m = e.get("sfx_par_minute") if e.get("sfx_par_minute") is not None else m.get("sfx_par_minute")
    # bruitages mesurés à l'oreille du code = estimation peu fiable : contrôlés seulement une fois confirmés (verrous)
    sfx_ok = "audio.sfx_par_minute" in (profile.get("verrous") or [])
    if sfx_ok and sfx_cible and sfx_m is not None and abs(sfx_m - sfx_cible) > 0.5 * sfx_cible:
        ecart("sfx_par_minute", sfx_cible, sfx_m, "±50%",
              ("ajouter des bruitages sur les entrées de b-roll et les crash zooms" if sfx_m < sfx_cible else
               "retirer des bruitages : garder ceux des entrées de b-roll et des crash zooms")
              + f" (types : {', '.join(profile['audio'].get('sfx_types') or ['whoosh'])})")

    pc, mc = profile.get("couleur") or {}, m.get("couleur") or {}
    for k, mot in (("luma", ("sombre", "claire")), ("saturation", ("terne", "saturée"))):
        if pc.get(k) is not None and mc.get(k) is not None and abs(mc[k] - pc[k]) > 0.08:
            ecart(f"couleur_{k}", pc[k], mc[k], "±0.08",
                  f"image trop {mot[0] if mc[k] < pc[k] else mot[1]} par rapport à tes vidéos : mettre "
                  "\"couleur\": \"auto\" dans edl.json (ou vérifier que la correction s'applique)")

    if e.get("mots_coupes"):
        ex = ", ".join(f"« {c['mot']} » à {fr(c['a_s'], 1)} s" for c in e["mots_coupes"][:4])
        ecart("mots_coupes", 0, len(e["mots_coupes"]), "aucun",
              f"mot(s) coupé(s) à un raccord : {ex} → caler start/end sur les mots (début − 0,05 s, fin + 0,1 s)")

    if duree_cible:
        if abs(m["duree"] - duree_cible) > 0.15 * duree_cible:
            delta = m["duree"] - duree_cible
            ecart("duree_totale_s", duree_cible, m["duree"], "±15%",
                  f"{'retirer' if delta > 0 else 'ajouter'} ~{fr(abs(delta), 0)} s "
                  f"({'enlever les prises les plus faibles' if delta > 0 else 'reprendre des phrases coupées'})")
    return ecarts


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        die(f"JSON illisible ({path}) : {e}")


def main():
    ap = argparse.ArgumentParser(description="Compare une vidéo montée au profil DA ; écrit un rapport JSON.")
    ap.add_argument("video")
    ap.add_argument("--profil", default="ma-da/profile.json")
    ap.add_argument("--duree-cible", type=float, help="durée voulue dans le brief, en secondes")
    ap.add_argument("--edl", help="plan de montage (.monteur/edl.json) ; edl.rendu.json à côté est lu s'il existe")
    ap.add_argument("--root", default=".", help="dossier de travail (contient .monteur/)")
    args = ap.parse_args()
    video, prof, root = Path(args.video), Path(args.profil), Path(args.root)
    if not video.is_file():
        die(f"vidéo introuvable : {video}")
    if not prof.is_file():
        die(f"profil introuvable : {prof} (lance d'abord apprendre-ma-da)")
    profile = load_json(prof)
    edl = load_json(args.edl) if args.edl else None
    rendu_path = Path(args.edl).with_name("edl.rendu.json") if args.edl else root / ".monteur" / "edl.rendu.json"
    rendu = load_json(rendu_path) if rendu_path.is_file() else None
    if rendu and rendu.get("output") and Path(rendu["output"]).name != video.name:
        print(f"Note : {rendu_path} concerne {Path(rendu['output']).name}, pas {video.name} : ignoré.")
        rendu = None
    e = edl_measures(rendu or edl, root, profile)

    m = measure.measure(video, e.get("words"))
    ecarts = compare(m, profile, args.duree_cible, edl or rendu, e)
    rapport = {"conforme": not ecarts, "ecarts": ecarts, "mesures": m,
               "edl": {k: v for k, v in e.items() if k != "words"}}
    out = work_dir(root) / "rapports" / f"{video.stem}.json"
    out.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")

    resp = (m.get("respiration") or {}).get("respiration_phrases_s")
    print(f"Mesuré : {fr(m['duree'], 1)} s, un plan toutes les {fr(m['duree_moyenne_plan_s'])} s, "
          f"{fr(m['zooms_par_minute'], 0)} zooms/min, "
          f"{'pas d’audio' if m['lufs'] is None else fr(m['lufs'], 1) + ' LUFS'}, "
          f"plus long silence {fr(m['silence_max_s'])} s"
          + (f", pause entre phrases {fr(resp)} s" if resp is not None else "")
          + (f", b-roll {round(100 * m['broll_part'])} %" if m.get("broll_part") is not None else "") + ".")
    if not ecarts:
        print(f"CONFORME à la DA. Rapport : {out}")
    else:
        print(f"NON CONFORME ({len(ecarts)} écart(s)) — rapport : {out}")
        for x in ecarts:
            print(f"- {x['critere']} : attendu {x['attendu']}, mesuré {x['mesure']} ({x['tolerance']}) → {x['conseil']}")


if __name__ == "__main__":
    main()
