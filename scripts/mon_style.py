"""« Ce que je sais de ton style » : une page locale qui dit en phrases simples la DA de ma-da/ (rythme, sous-titres
avec un aperçu, zooms, plans de coupe, habillage, son, couleur), les règles apprises, les réglages verrouillés, les
sources du style et la progression des montages (rapports de check.py). Page statique, rien ne sort de l'ordinateur.

  python scripts/mon_style.py [--projet .] [--sans-navigateur]   → écrit .monteur/mon_style.html et l'ouvre"""
import argparse
import json
import re
import sys
import time
import webbrowser
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from anim import HEX, font_face  # noqa: E402
from captions import bundled, clean_style, graisse  # noqa: E402
from common import die  # noqa: E402

ANIM = {"pop": "qui apparaissent en grossissant un peu", "rebond": "qui rebondissent en apparaissant",
        "glisse": "qui montent en apparaissant", "fondu": "qui apparaissent en fondu",
        "flou": "qui passent du flou au net", "karaoke": "où le mot prononcé change de couleur",
        "mot_par_mot": "dont les mots arrivent un à un", "surlignage": "où le mot prononcé est surligné",
        "aucune": "sans animation"}
ZOOM = {"doux": "poussées lentes", "punch": "zooms secs", "cut": "recadrages à la coupe", "crash": "zooms éclair"}
GRAPH = {"question": "Carton question", "citation": "Citation", "lieu": "Lieu", "logo": "Logo", "chiffre": "Chiffre animé",
         "liste": "Liste", "mosaique": "Mosaïque de clips", "html": "Graphisme sur mesure"}
LIBELLES = {"sous_titres.emojis_par_minute": "emojis par minute", "sous_titres.mots_cles_part_blocs": "part des sous-titres avec un mot en couleur",
            "broll.duree_moy_s": "durée d'un plan de coupe (s)", "broll.part_pct": "part de plans de coupe",
            "rythme.duree_moyenne_plan_s": "durée d'un plan (s)", "zooms.par_minute": "zooms par minute",
            "audio.sfx_par_minute": "bruitages par minute", "audio.musique": "musique", "audio.lufs_cible": "volume de la voix",
            "sous_titres.couleur": "couleur des sous-titres", "sous_titres.police": "police des sous-titres",
            "rythme.respiration_intra_s": "pause dans une phrase (s)", "rythme.respiration_phrases_s": "pause entre deux phrases (s)"}
EXEMPLE = ["ça", "change", "tout", "pour", "toi", "maintenant"]


def fr(x, n=1):
    s = f"{float(x):.{n}f}".replace(".", ",")
    return s[:-2] if n == 1 and s.endswith(",0") else s


def get(d, cle):
    for k in cle.split("."):
        d = d.get(k) if isinstance(d, dict) else None
    return d


def couleur(v, defaut):
    """Couleur du profil sûre dans du CSS (un texte quelconque ne passe pas)."""
    return v if isinstance(v, str) and HEX.fullmatch(v) else defaut


def pastille(hexa):
    return f'<i class="sw" style="background:{couleur(hexa, "#888")}"></i>{escape(str(hexa))}'


def section_md(texte, titre):
    """Lignes de la section « ## <titre>… » de STYLE.md, sans commentaires ni puces."""
    m = re.search(rf"^##\s*{re.escape(titre)}[^\n]*\n(.*?)(?=^##\s|\Z)", texte, re.M | re.S)
    corps = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S) if m else ""
    return [re.sub(r"^[-*]\s*", "", x).strip() for x in corps.splitlines() if x.strip()]


def phrases(p):
    """{rubrique: [phrases en français simple]} — chaque texte est échappé ici."""
    r, z, st, b, a = (p.get(k) or {} for k in ("rythme", "zooms", "sous_titres", "broll", "audio"))
    out = {}
    ry = []
    if r.get("duree_moyenne_plan_s"):
        q = r.get("plans_quantiles") or []
        ry.append(f"Un plan toutes les {fr(r['duree_moyenne_plan_s'])} s"
                  + (f" (de {fr(q[0])} à {fr(q[-1])} s)" if len(q) >= 2 else "") + ".")
    if r.get("hook_plan_s"):
        ry.append(f"Premier plan de l'accroche : {fr(r['hook_plan_s'])} s.")
    if r.get("respiration_intra_s") is not None and r.get("respiration_phrases_s") is not None:
        ry.append(f"Pause de {fr(r['respiration_intra_s'], 2)} s entre deux mots, "
                  f"{fr(r['respiration_phrases_s'], 2)} s entre deux phrases.")
    if r.get("silence_max_s"):
        ry.append(f"Jamais plus de {fr(r['silence_max_s'])} s de silence.")
    out["Rythme"] = ry
    if st.get("actif") is False:
        out["Sous-titres"] = ["Pas de sous-titres."]
    else:
        n = int(st.get("mots_par_bloc") or 3)
        s = [f"{n} mot{'s' if n > 1 else ''} à la fois, en {'capitales' if st.get('majuscules') else 'minuscules'}, "
             f"police {escape(str(st.get('police') or 'Montserrat'))}, {ANIM.get(st.get('animation'), 'animés')}.",
             f"Texte {pastille(st.get('couleur') or '#FFFFFF')}."]
        part = st.get("mots_cles_part_blocs") or 0
        if (st.get("mots_cles_par_bloc") or 0) > 0 and part > 0:
            s.append(f"Un mot-clé en {pastille(st.get('couleur_mot_cle'))} environ une fois sur {max(1, round(1 / part))}.")
        else:
            s.append("Pas de mot-clé en couleur.")
        em = st.get("emojis_par_minute") or 0
        s.append(f"Environ {fr(em)} emoji{'s' if em >= 2 else ''} par minute"
                 + (f" ({escape(str(st['emojis_style']))})." if st.get("emojis_style") else ".") if em else "Pas d'emoji.")
        out["Sous-titres"] = s
    if not z.get("par_minute"):
        out["Zooms"] = ["Pas de zoom."]
    else:
        types = ", ".join(f"{ZOOM.get(k, escape(str(k)))} {round(100 * v)} %" for k, v in (z.get("types") or {}).items())
        out["Zooms"] = [f"Environ {fr(z['par_minute'], 0)} zooms par minute, jusqu'à ×{fr(z.get('intensite') or 1.1, 2)}"
                        + (f" : {types}" if types else "") + "."]
        if z.get("sur_visage"):
            out["Zooms"].append("Centrés sur ton visage.")
    if not b.get("part_pct"):
        out["Plans de coupe"] = ["Pas de plan de coupe."]
    else:
        out["Plans de coupe"] = [f"{round(100 * b['part_pct'])} % du temps, {fr(b.get('duree_moy_s') or 1.5)} s chacun environ"
                                 + (", jamais sur l'accroche" if b.get("jamais_sur_hook") else "") + "."]
        if b.get("usage"):
            u = str(b["usage"]).rstrip(". ")
            out["Plans de coupe"].append(escape(u[:1].upper() + u[1:]) + ".")
    h, t = p.get("hook") or {}, p.get("titres") or {}
    ha = []
    if h.get("duree_s"):
        ha.append(f"Accroche de {fr(h['duree_s'])} s" + (f" ({escape(str(h['type']))})" if h.get("type") else "")
                  + (f" : {escape(str(h['formule']))}" if h.get("formule") else "") + ".")
    if t.get("actif"):
        ha.append(f"Titres à l'écran : {escape(str(t.get('style') or 'oui'))}"
                  + (f" ({escape(str(t['quand']))})" if t.get("quand") else "") + ".")
    for g in p.get("graphismes") or []:
        if isinstance(g, dict):
            ha.append(f"{GRAPH.get(g.get('type'), escape(str(g.get('type'))))} : {escape(str(g.get('quand') or ''))}.")
    out["Habillage"] = ha or ["Rien en plus de tes plans et des sous-titres."]
    son = []
    if a.get("musique"):
        db = a.get("musique_db_sous_voix")
        son.append("Musique sous la voix" + (f", {fr(abs(db), 0)} dB plus bas que toi" if db else "") + ".")
    else:
        son.append("Pas de musique" + (" (si tu en donnes une : nappe très discrète)" if a.get("musique_db_sous_voix") else "") + ".")
    if a.get("sfx_par_minute"):
        son.append(f"Environ {fr(a['sfx_par_minute'], 0)} bruitages par minute"
                   + (f" ({escape(', '.join(map(str, a['sfx_types'])))})" if a.get("sfx_types") else "") + ".")
    else:
        son.append("Pas de bruitage.")
    if a.get("voix_nettoyee", True):
        son.append("Voix nettoyée (souffle et bruit de fond atténués).")
    out["Son"] = son
    c = p.get("couleur")
    if isinstance(c, dict) and c.get("luma") is not None:
        # shortcut: seuils à l'œil sur les mesures de measure.color_stats, à recaler si les mots sonnent faux
        lum = "sombre" if c["luma"] < 0.38 else "claire" if c["luma"] > 0.55 else "ni claire ni sombre"
        temp = c.get("temperature") or 0
        ton = "chaude" if temp > 0.08 else "froide" if temp < -0.03 else "neutre"
        sat = c.get("saturation") or 0
        vif = ", couleurs vives" if sat > 0.28 else ", couleurs douces" if sat < 0.15 else ""
        out["Couleur"] = [f"Image {lum}, {ton}{vif}."]
    else:
        out["Couleur"] = ["La couleur de tes images, corrigée sans viser de look particulier."]
    v = p.get("style_verbal") or {}
    parle = []
    if v.get("duree_typique_s"):
        parle.append(f"Vidéos d'environ {fr(v['duree_typique_s'], 0)} s.")
    if v.get("structure"):
        parle.append("Déroulé : " + escape(str(v["structure"])) + ".")
    if v.get("cta"):
        parle.append("Fin : « " + escape(" » ou « ".join(map(str, v["cta"]))) + " ».")
    if v.get("registre") in ("tu", "vous"):
        parle.append(f"Tu parles en « {v['registre']} ».")
    if parle:
        out["Ta façon de parler"] = parle
    return out


def apercu(st):
    """Bloc de sous-titre d'exemple sur un écran 9:16, avec la police embarquée la plus proche."""
    st = clean_style(st)
    n = max(1, int(st.get("mots_par_bloc") or 3))
    mots = EXEMPLE[1:2] if n == 1 else EXEMPLE[:n]
    accent = st.get("couleur_mot_cle") if (st.get("mots_cles_par_bloc") or 0) > 0 else \
        st.get("couleur_mot_actif") if st.get("animation") in ("karaoke", "surlignage") else None
    H = 480  # hauteur de l'écran d'aperçu (px) ; la vidéo fait 1920
    taille = round(float(st["taille_pct_hauteur"]) * H / 0.72)  # capitales ≈ 0,72 em
    contour = float(st.get("epaisseur_contour") or 0) * H / 1920 * 2
    flou = float(st.get("ombre_flou") or 0) * H / 1920
    style = (f"font-family:F,sans-serif;font-weight:{graisse(st)};font-size:{taille}px;top:{float(st['position_y_pct']) * 100:.0f}%;"
             f"color:{couleur(st.get('couleur'), '#FFFFFF')};letter-spacing:{float(st.get('interlettrage_pct') or 0) / 100:.3f}em;"
             f"text-transform:{'uppercase' if st.get('majuscules') else 'none'};"
             + (f"-webkit-text-stroke:{contour:.1f}px {couleur(st.get('contour'), '#000000')};" if contour else "")
             + (f"text-shadow:0 {flou / 3:.1f}px {flou:.1f}px rgba(0,0,0,.65);" if flou else ""))
    html = " ".join(f'<span style="color:{couleur(accent, "#FFE600")}">{escape(m)}</span>' if accent and m == "change"
                    else escape(m) for m in mots)
    police = st.get("police") or "Montserrat"
    face = font_face(police if bundled(police) else "Montserrat")
    return f'<style>{face}</style><div class="ecran"><div class="visage"></div><div class="st" style="{style}">{html}</div></div>'


def sources(p):
    """Lignes (nom, date, poids) : `sources` du profil si présent, sinon ses références."""
    src = p.get("sources") or p.get("references") or []
    lignes = []
    for s in src if isinstance(src, list) else []:
        if isinstance(s, dict):
            nom = next((s[k] for k in ("chemin", "fichier", "video", "nom", "source", "url") if s.get(k)), "?")
            lignes.append((Path(str(nom)).name, s.get("date") or s.get("ajoute") or "", s.get("poids")))
        else:
            lignes.append((Path(str(s)).name, "", None))
    return lignes


def corrections(da):
    """Journal ma-da/corrections.jsonl (une correction JSON par ligne) ; lignes illisibles ignorées."""
    f = da / "corrections.jsonl"
    out = []
    for ligne in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        try:
            c = json.loads(ligne)
        except ValueError:
            continue
        if isinstance(c, dict):
            txt = next((c[k] for k in ("texte", "correction", "demande", "regle", "message") if c.get(k)), None)
            out.append((str(c.get("date") or c.get("quand") or ""), str(txt or json.dumps(c, ensure_ascii=False))))
        else:
            out.append(("", str(c)))
    return out


def progression(root):
    """Rapports de check.py, du plus ancien au plus récent : (nom, date, nb d'écarts, écarts en clair)."""
    out = []
    for f in sorted((root / ".monteur" / "rapports").glob("*.json"), key=lambda x: x.stat().st_mtime):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        ec = r.get("ecarts") or []
        out.append((f.stem, time.strftime("%d/%m %H:%M", time.localtime(f.stat().st_mtime)), len(ec),
                    [constat(str(e.get("conseil") or e.get("critere") or "")) for e in ec if isinstance(e, dict)]))
    return out


def constat(conseil):
    """« trop de b-roll (27 % au lieu de 12 %) : retirer… » → le constat, sans couper dans une parenthèse."""
    i = conseil.find(" : ")
    while i >= 0 and conseil[:i].count("(") > conseil[:i].count(")"):
        i = conseil.find(" : ", i + 1)
    return conseil[:i] if i >= 0 else conseil


def valeur(v):
    return f"{v:g}".replace(".", ",") if isinstance(v, (int, float)) and not isinstance(v, bool) else \
        json.dumps(v, ensure_ascii=False)


def courbe(points):
    """Petite courbe SVG du nombre d'écarts par montage (0 en bas)."""
    if len(points) < 2:
        return ""
    W, H, m = 560, 120, 18
    haut = max(max(points), 1)
    xy = [(m + i * (W - 2 * m) / (len(points) - 1), H - m - v * (H - 2 * m) / haut) for i, v in enumerate(points)]
    pts = " ".join(f"{x:.0f},{y:.0f}" for x, y in xy)
    ronds = "".join(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="4"/>' for x, y in xy)
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="écarts avec ton style au fil des montages">'
            f'<line x1="{m}" y1="{H - m}" x2="{W - m}" y2="{H - m}" class="axe"/><polyline points="{pts}"/>{ronds}'
            f'<text x="{m}" y="12">{haut} écart{"s" if haut > 1 else ""}</text><text x="{m}" y="{H - 2}">0</text></svg>')


CSS = """:root{--f:#f6f4ef;--c:#1d1b18;--g:#6b665e;--l:#e4e0d6;--a:#b4532a;--carte:#fff}
@media(prefers-color-scheme:dark){:root{--f:#16151a;--c:#ece9e2;--g:#9a958b;--l:#2b2a30;--a:#e08a5c;--carte:#1f1e24}}
body{margin:0;background:var(--f);color:var(--c);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:860px;margin:auto;padding:28px 16px 60px}h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}
.sous{color:var(--g);margin:0 0 24px}.badge{display:inline-block;background:var(--carte);border:1px solid var(--l);
border-left:3px solid var(--a);padding:8px 12px;border-radius:6px;margin-bottom:20px}
.grille{display:grid;grid-template-columns:1fr 308px;gap:24px;align-items:start}@media(max-width:720px){.grille{grid-template-columns:1fr}}
section{background:var(--carte);border:1px solid var(--l);border-radius:10px;padding:16px 18px;margin-bottom:16px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--g);margin:0 0 8px;font-weight:600}
ul{margin:0;padding-left:18px}li{margin:3px 0}.vide{color:var(--g)}
.sw{display:inline-block;width:.85em;height:.85em;border-radius:3px;border:1px solid var(--l);vertical-align:-1px;margin-right:4px}
.ecran{position:relative;width:270px;height:480px;border-radius:18px;overflow:hidden;background:linear-gradient(170deg,#6d7b86,#3b4048 55%,#25272c)}
.visage{position:absolute;left:50%;top:18%;width:96px;height:120px;margin-left:-48px;border-radius:48% 48% 44% 44%;background:#c9a58b;opacity:.55}
.st{position:absolute;left:8px;right:8px;transform:translateY(-50%);text-align:center;line-height:1.1;paint-order:stroke fill}
table{width:100%;border-collapse:collapse}td{padding:5px 6px;border-bottom:1px solid var(--l);vertical-align:top}.n{color:var(--g);white-space:nowrap}
svg{width:100%;height:auto;margin:6px 0 10px}svg polyline{fill:none;stroke:var(--a);stroke-width:2.5}svg circle{fill:var(--a)}
svg .axe{stroke:var(--l)}svg text{fill:var(--g);font-size:11px}"""


def liste(items, vide="Rien pour l'instant."):
    """`items` déjà échappés."""
    return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>" if items else f'<p class="vide">{vide}</p>'


def page(root):
    da = root / "ma-da"
    f = da / "profile.json"
    if not f.exists():
        die(f"pas de {f} : choisis un style de départ (style_depart.py) ou donne des reels que tu aimes")
    try:
        p = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        die(f"{f} illisible ({e})")
    md = (da / "STYLE.md").read_text(encoding="utf-8") if (da / "STYLE.md").exists() else ""
    if p.get("origine") == "style de depart":
        badge = (f"Style de départ « {escape(str(p.get('style_depart') or ''))} » : une base de métier, pas encore tes "
                 "vidéos. Il sera remplacé par ce que tu valides (tes corrections, les reels que tu aimes).")
    else:
        n = len(p.get("references") or [])
        badge = f"Appris sur {n} vidéo{'s' if n > 1 else ''}." if n else "Style réglé à la main."
    corps = "".join(f"<section><h2>{escape(k)}</h2>{liste(v)}</section>" for k, v in phrases(p).items())
    regles = [escape(x) for x in section_md(md, "Règles apprises")]
    verrous = [f"{escape(LIBELLES.get(k, k.replace('.', ' › ').replace('_', ' ')))} : "
               f"<b>{escape(valeur(get(p, k)))}</b>" for k in p.get("verrous") or []]
    src = sources(p)
    src_html = ("<table>" + "".join(f"<tr><td>{escape(n)}</td><td class='n'>{escape(str(d))}</td><td class='n'>"
                                    + (f"poids {escape(fr(w, 2) if isinstance(w, (int, float)) else str(w))}" if w is not None else "")
                                    + "</td></tr>" for n, d, w in src) + "</table>") if src else '<p class="vide">Aucune vidéo encore.</p>'
    corr = corrections(da)
    corr_html = (f"<section><h2>Tes corrections</h2>{liste([(f'<span class=n>{escape(d)}</span> ' if d else '') + escape(t) for d, t in corr[-20:]])}</section>"
                 if corr else "")
    prog = progression(root)
    prog_html = (courbe([n for _, _, n, _ in prog])
                 + "<table>" + "".join(f"<tr><td class='n'>{escape(d)}</td><td>{escape(nom)}</td><td>"
                                       + ("conforme à ton style" if not n else
                                          f"{n} écart{'s' if n > 1 else ''} : {escape(' ; '.join(x for x in ec if x))}")
                                       + "</td></tr>" for nom, d, n, ec in reversed(prog[-12:])) + "</table>"
                 ) if prog else '<p class="vide">Aucun montage contrôlé pour l\'instant.</p>'
    st = p.get("sous_titres") or {}
    cote = (f"<section><h2>Aperçu des sous-titres</h2>{apercu(st)}</section>" if st.get("actif", True) else "")
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Mon style</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head><body><main>
<h1>Ce que je sais de ton style</h1><p class="sous">Mis à jour le {time.strftime('%d/%m/%Y à %H:%M')} — tout reste sur ton ordinateur.</p>
<div class="badge">{badge}</div>
<div class="grille"><div>{corps}</div><aside>{cote}</aside></div>
<section><h2>Règles apprises</h2>{liste(regles, "Aucune pour l'instant : dis-moi ce que tu changerais après un montage.")}</section>
<section><h2>Réglages que tu as fixés</h2>{liste(verrous, "Aucun : tout peut encore bouger avec tes vidéos.")}</section>
<section><h2>D'où vient ce style</h2>{src_html}</section>{corr_html}
<section><h2>Progression des montages</h2><p class="vide">Écarts entre chaque montage et ton style (moins = plus fidèle).</p>{prog_html}</section>
</main></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projet", default=".")
    ap.add_argument("--sans-navigateur", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.projet)
    out = root / ".monteur" / "mon_style.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page(root), encoding="utf-8")
    print(f"Ce que je sais de ton style : {out.resolve()}")
    if not a.sans_navigateur:
        webbrowser.open(out.resolve().as_uri())
    return out


if __name__ == "__main__":
    main()
