"""Inserts animés (motion design) en MP4 1080×1920, à poser dans l'edl comme b-roll (`"texte": true`).

Le graphisme est du HTML/CSS animé, rendu image par image par le navigateur déjà installé (Edge, Chrome ou
Chromium via Playwright), puis composé par ffmpeg sur un fond : vidéo (rush ou b-roll, assombri/flouté), photo
(lente poussée) ou couleur. Hors ligne, gratuit. Couleurs et police : celles de la DA (--profil).

Gabarits :
  question  "Quel a été le plus grand *accomplissement* de ta vie ?"   carton d'ouverture, anneaux animés, mot fort
  citation  "Nul n'est prophète en son pays" --sous "Dicton" --fond depot/rush.mov --fond-debut 55.6
  lieu      "Dubaï" --sous "Émirats arabes unis" --fond broll/club.mp4 [--tampon "SIGNÉ"]
  logo      "NIKKI BEACH" --sous "Dubaï" [--image broll/logo.png] [--tampon "SIGNÉ"]
  chiffre   "15 ans" --sous "des centaines d'événements" [--fond …]
  liste     "Zéro réseau" "Zéro contact" "Petit budget" --titre "Au départ" --fond depot/rush.mov --fond-debut 80
  mosaique  --clips broll/a.mp4 broll/b.mp4 broll/c.mp4 [--texte "15 ans d'événements"]   clips l'un après l'autre
            dans une carte arrondie, sur le fond animé du carton
  html      broll/mon_insert.html [--fond …]   graphisme écrit sur mesure (ce qu'aucun gabarit ne fait) : HTML/CSS
            avec des animations CSS (@keyframes, animation-delay) ; police `font-family:F` (celle de la DA), couleurs
            var(--a) (accent) et var(--t) (texte), toile 1080×1920, fond transparent = le --fond passe dessous ;
            classes prêtes : .c (centré), .m (mot qui arrive), .pop, .fade, .ombre, .acc, .anneaux, .tampon
Habillage appris (graphismes[] du profil, élément du même type ; une option passée en ligne de commande l'emporte) :
  police, graisse, interlettrage_pct (--interlettrage), taille_pct (--taille : capitales des petits mots / du texte
  en part de la hauteur ; logo : largeur en part de la largeur), entree (--entree : monte|disperse ; logo :
  rotation|zoom|flou), duree_entree_s, anneaux (nets|flous|aucun), sortie (--fin fuite:#hex|flash:#hex|coupe),
  teinte_fond (--teinte #hex, fond vidéo teinté), sombre_fond (--sombre 0-1), flou_fond (--flou), guillemets
  (serif|ronds), vignette (logo), rayon et ombre (mosaique : coins, ombre portée 0-1), sous_titres (true/false :
  sous-titres gardés sur l'insert ; défaut : gardés sur une mosaïque sans --texte seulement).
Exemple : python scripts/anim.py citation "Nul n'est prophète en son pays" --sous Dicton --fond depot/IMG_2045.mov
          --fond-debut 55.6 --duree 2.6 --profil ma-da/profile.json --sortie broll/anim_citation.mp4"""
import argparse
import base64
import html
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import FF_THREADS, die, ffmpeg, probe, run, tonemap  # noqa: E402
from captions import bundled, font_ratios, graisse  # noqa: E402

W, H, FPS = 1080, 1920, 30
FONTS = Path(__file__).parent / "fonts"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
DEFAUT = {"fond": "#0E2233", "accent": "#F2A516", "texte": "#FFFFFF", "police": "Poppins", "graisse": 800}
# option de la ligne de commande → clé de graphismes[] dans le profil
SCHEMA = {"police": "police", "graisse": "graisse", "interlettrage": "interlettrage_pct", "taille": "taille_pct",
          "entree": "entree", "duree_entree": "duree_entree_s", "anneaux": "anneaux", "fin": "sortie",
          "teinte": "teinte_fond", "guillemets": "guillemets", "vignette": "vignette", "sombre": "sombre_fond",
          "flou": "flou_fond", "rayon": "rayon", "ombre": "ombre"}
HEX = re.compile(r"#[0-9A-Fa-f]{3,8}")


def da(profil, kind=None):
    """Couleurs/police de la DA : carton (hook.fond / couleur_mot), mot-clé, police et graisse des sous-titres ;
    `g` = l'élément de graphismes[] du même type (habillage appris sur la référence), {} sinon."""
    c = dict(DEFAUT, g={})
    if profil and Path(profil).exists():
        p = json.loads(Path(profil).read_text(encoding="utf-8"))
        hook, st = p.get("hook") or {}, p.get("sous_titres") or {}
        c["fond"] = hook.get("fond") or c["fond"]
        c["accent"] = hook.get("couleur_mot") or st.get("couleur_mot_cle") or c["accent"]
        c["police"] = st.get("police") or c["police"]
        c["graisse"] = graisse(st)
        c["g"] = next((g for g in p.get("graphismes") or [] if isinstance(g, dict) and g.get("type") == kind), {})
    return c


def num(v, defaut=None):
    """Nombre du profil (« 0,3 », 2.5…) ; texte qui n'en est pas un → défaut."""
    try:
        return float(str(v).replace(",", ".").split()[0]) if v not in (None, "") else defaut
    except (ValueError, IndexError):
        return defaut


def part(v):
    """Part 0-1 ; un pourcentage écrit 2.5 (au lieu de 0.025) est ramené."""
    v = num(v)
    return v / 100 if v and v > 1 else v


def faces(c):
    return bundled(c["police"]) or bundled("Poppins")


def face(c):
    """Fichier de la graisse embarquée la plus proche de celle de la DA."""
    f = faces(c)
    return FONTS / f[min(f, key=lambda g: abs(g - c["graisse"]))][1]


def font_face(police):
    """Toutes les graisses embarquées de la famille sous le nom F (font-weight choisit la bonne)."""
    return "".join(f"@font-face{{font-family:F;font-weight:{g};src:url('{data_uri(FONTS / f)}')}}"
                   for g, (_, f) in faces({"police": police}).items())


def px_capitales(pct, c):
    """font-size (px) pour des capitales à `pct` de la hauteur, sur la police réellement chargée."""
    cap, em = font_ratios(face(c)) or (None, None)
    return round(pct * H * (em / cap if cap else 1 / 0.7))


def sortie_html(fin, duree):
    """Dernier calque : fuite de lumière qui monte du bas (~0,4 s), flash de couleur (~0,25 s) ou rien (coupe)."""
    kind, _, col = str(fin or "coupe").partition(":")
    if kind == "fuite":
        col = col if HEX.fullmatch(col) else "#F2B36B"
        return f'<div class="fuite" style="--f:{col};animation-delay:{max(0, duree - .4):.2f}s"></div>'
    if kind == "flash":
        col = col if HEX.fullmatch(col) else "#FFFFFF"
        return f'<div class="flash" style="--f:{col};animation-delay:{max(0, duree - .25):.2f}s"></div>'
    return ""


def data_uri(path):
    """Fichier embarqué dans la page : le navigateur refuse les file:// depuis une page sans origine."""
    mime = {".ttf": "font/ttf", ".png": "image/png", ".webp": "image/webp"}.get(Path(path).suffix.lower(), "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def mot_fort(texte):
    """« avant *mot* après » → (avant, mot, après) ; sans étoiles, le mot le plus long est le mot fort."""
    m = re.search(r"\*(.+?)\*", texte)
    if m:
        return texte[:m.start()].strip(), m.group(1).strip(), texte[m.end():].strip()
    mots = texte.split()
    i = max(range(len(mots)), key=lambda k: len(mots[k].strip("?!.,")))
    return " ".join(mots[:i]), mots[i], " ".join(mots[i + 1:])


def nombre(texte):
    """« 15 ans » → (15, "", " ans") ; « +300 % » → (300, "+", " %") ; rien de chiffré → None."""
    m = re.match(r"\s*([^\d\s]*)\s*(\d(?:[\d.,]|\s(?=\d))*)(.*)", texte)
    if not m:
        return None
    try:
        return int(re.sub(r"\D", "", m.group(2))), m.group(1), m.group(3)
    except ValueError:
        return None


def mots(texte, d0, pas, classe="m", duree=None):
    """Mots qui arrivent l'un après l'autre : « m » flou → net de bas en haut ; « d » dispersés qui s'assemblent
    (décalage fixe par index : le même rendu à chaque fois)."""
    out = []
    for i, w in enumerate(colle_ponctuation(texte)):
        st = f"animation-delay:{d0 + i * pas:.3f}s" + (f";animation-duration:{duree:.2f}s" if duree else "")
        if classe == "d":
            st += f";--dx:{((i * 7) % 5 - 2) * 70}px;--dy:{((i * 3) % 5 - 2) * 60 + 30}px"
        out.append(f'<span class="{classe}" style="{st}">{html.escape(w)}</span>')
    return " ".join(out)


def colle_ponctuation(texte):
    """Mots, la ponctuation haute française (« ? ! : ; ») collée au mot d'avant par une espace insécable :
    jamais un « ? » seul en début de ligne."""
    out = []
    for w in texte.split():
        if out and w in ("?", "!", ":", ";", "»"):
            out[-1] += " " + w
        else:
            out.append(w)
    return out


ANNEAUX = """<div class="anneaux">""" + "".join(
    f'<i style="width:{s}px;height:{s}px;animation-duration:{9 + k * 3}s;animation-delay:-{k}s"></i>'
    for k, s in enumerate((620, 900, 1200, 1520))) + "</div>"
# halos épais flous, décentrés, qui dérivent lentement (sur 3 s on n'en voit qu'une partie)
ANNEAUX_FLOUS = """<div class="anneaux flous">""" + "".join(
    f'<i style="width:{s}px;height:{s}px;left:{x}%;top:{y}%;animation-duration:{d}s"></i>'
    for s, x, y, d in ((1250, 40, 56, 8), (1800, 78, 22, 10))) + "</div>"

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1920px;background:transparent;overflow:hidden;font-family:F,sans-serif;color:var(--t)}
.c{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;
   text-align:center;padding:0 90px}
.m{display:inline-block;opacity:0;animation:mot .38s cubic-bezier(.2,.8,.2,1) both}
@keyframes mot{from{opacity:0;transform:translateY(38px);filter:blur(10px)}to{opacity:1;transform:none;filter:none}}
.pop{animation:pop .5s cubic-bezier(.3,1.6,.5,1) both}
@keyframes pop{from{opacity:0;transform:scale(.4) rotate(-12deg)}to{opacity:1;transform:none}}
.fade{animation:fade .45s ease-out both}
@keyframes fade{from{opacity:0}to{opacity:1}}
.ombre{text-shadow:0 6px 30px rgba(0,0,0,.55)}
.acc{color:var(--a)}
.anneaux{position:absolute;inset:0}
.anneaux i{position:absolute;left:50%;top:50%;border-radius:50%;border:4px solid var(--a);opacity:.38;
   transform:translate(-50%,-50%);animation:tourne linear infinite}
.anneaux i:nth-child(odd){border-style:dashed;opacity:.22}
@keyframes tourne{from{transform:translate(-50%,-50%) rotate(0) scale(.96)}50%{transform:translate(-50%,-50%) rotate(180deg) scale(1.04)}
   to{transform:translate(-50%,-50%) rotate(360deg) scale(.96)}}
.vignettage{position:absolute;inset:0;background:radial-gradient(ellipse at center,transparent 45%,rgba(0,0,0,.55))}
.tampon{display:inline-block;margin-top:70px;padding:14px 44px;border:8px solid var(--a);border-radius:18px;color:var(--a);
   font-size:96px;letter-spacing:6px;animation:tampon .35s cubic-bezier(.2,1.5,.4,1) both}
@keyframes tampon{from{opacity:0;transform:scale(2.6) rotate(-8deg)}to{opacity:1;transform:rotate(-8deg)}}
.trait{height:8px;width:0;background:var(--a);border-radius:4px;animation:trait .5s ease-out both}
@keyframes trait{to{width:220px}}
.d{display:inline-block;opacity:0;animation:disp .3s cubic-bezier(.16,1,.3,1) both}
@keyframes disp{from{opacity:0;transform:translate(var(--dx),var(--dy)) scale(.85);filter:blur(12px)}to{opacity:1;transform:none;filter:none}}
.pose{animation:pose .5s cubic-bezier(.16,1,.3,1) both}
@keyframes pose{from{opacity:0;transform:scale(.94);filter:blur(8px)}to{opacity:1;transform:none;filter:none}}
.anneaux.flous i{border:34px solid var(--a);opacity:.3;filter:blur(22px);animation:derive linear both}
@keyframes derive{from{transform:translate(-50%,-50%) scale(.97)}to{transform:translate(-42%,-56%) scale(1.06)}}
.fuite{position:absolute;left:-25%;right:-25%;top:40%;height:100%;filter:blur(40px);opacity:0;
   background:radial-gradient(ellipse at 50% 100%,var(--f),color-mix(in srgb,var(--f) 55%,transparent) 40%,transparent 72%);
   animation:fuite .4s cubic-bezier(.3,0,.6,1) both}
@keyframes fuite{from{opacity:0;transform:translateY(45%)}to{opacity:1;transform:translateY(-12%)}}
.flash{position:absolute;inset:0;background:var(--f);opacity:0;animation:fade .25s ease-in both}
.vig{position:absolute;inset:0;opacity:0;animation:fade 1.2s ease-in both;
   background:linear-gradient(rgba(60,60,60,.18),transparent 30%,transparent 62%,rgba(60,60,60,.32))}
"""


def gabarit(kind, a, c):
    """Corps HTML du gabarit + fond HTML opaque ? (True = le gabarit dessine son propre fond)."""
    e = html.escape
    t = " ".join(a.textes or [])
    o = vars(a)  # options d'habillage (None = rendu d'avant)
    classe = "d" if o.get("entree") == "disperse" else "m"
    de = num(o.get("duree_entree"))
    anneaux = {"flous": ANNEAUX_FLOUS, "aucun": ""}.get(o.get("anneaux"), ANNEAUX)
    taille = part(o.get("taille"))
    if kind in ("question", "mosaique"):
        fond = f'<div style="position:absolute;inset:0;background:radial-gradient(ellipse at 50% 42%,' \
               f'color-mix(in srgb,{c["fond"]} 70%,#fff 12%),{c["fond"]} 60%,color-mix(in srgb,{c["fond"]} 70%,#000))"></div>'
        if kind == "mosaique":
            txt = f'<div class="c" style="justify-content:flex-start;padding-top:170px"><div class="ombre" ' \
                  f'style="font-size:70px;line-height:1.1">{mots(a.texte, .1, .07, classe, de)}</div></div>' if a.texte else ""
            return fond + anneaux + '<div class="vignettage"></div>' + txt, True
        av, fort, ap = mot_fort(t)
        n = len(colle_ponctuation(av))
        # la 1re image montre déjà le mot fort (pop entamé) ; les autres mots arrivent tous en moins de 0,5 s
        pas = min(.09, .4 / max(1, n + len(colle_ponctuation(ap))))
        from PIL import ImageFont
        ls = (num(o.get("interlettrage")) or 0) / 100
        w100 = ImageFont.truetype(str(face(c)), 100).getlength(fort.upper()) + ls * 100 * len(fort)
        grand = min(170, int(880 * 100 / w100))  # le mot fort tient toujours dans 880 px (mesuré sur la police)
        petit = px_capitales(taille, c) if taille else 64
        anim_fort = "pop" if classe == "m" else "pose"  # rebond tourné seulement avec l'entrée d'avant
        corps = (f'<div class="c"><div style="font-size:{petit}px;line-height:1.2">{mots(av, .05, pas, classe, de)}</div>'
                 f'<div class="acc {anim_fort} ombre" style="font-size:{grand}px;line-height:1.05;margin:18px 0;'
                 f'animation-delay:-.25s">{e(fort.upper())}</div>'
                 f'<div style="font-size:{petit}px;line-height:1.2">{mots(ap, .05 + n * pas, pas, classe, de)}</div></div>')
        return fond + anneaux + '<div class="vignettage"></div>' + corps, True
    if kind == "html":
        f = Path(t)
        if not f.exists():
            die(f"fichier HTML introuvable : {f}")
        return f.read_text(encoding="utf-8"), False
    if kind == "citation":  # 1er mot avec les guillemets : jamais de guillemets vides à l'écran
        n = len(t.split())
        px = px_capitales(taille, c) if taille else 96
        pas = .06 if classe == "d" else .11
        if o.get("guillemets") == "ronds":  # gras sans empattement, ouvrant en bas à gauche, fermant en haut à droite
            g = round(3.4 * px)  # le glyphe “ n'occupe que le haut de l'em : ~1,6× la hauteur du texte à l'œil
            q = f'font-size:{g}px;line-height:1;position:absolute;font-weight:800'
            boite = f'position:relative;padding:{round(.25 * g)}px {round(.2 * g)}px'
            ouvre = f'<div class="acc pose" style="{q};left:-{round(.45 * g)}px;bottom:-{round(.75 * g)}px">“</div>'
            ferme = (f'<div class="acc pose" style="{q};right:-{round(.45 * g)}px;top:-{round(.1 * g)}px;'
                     f'animation-delay:{.3 + n * pas:.2f}s">”</div>')
        else:
            q = 'font-size:300px;line-height:1;position:absolute;font-family:Georgia,serif;font-weight:900'
            boite = "position:relative;padding:150px 20px"
            ouvre = f'<div class="acc pop" style="{q};left:-30px;top:-10px">“</div>'
            ferme = f'<div class="acc pop" style="{q};right:-30px;bottom:-150px;animation-delay:{.3 + n * .11:.2f}s">”</div>'
        return (f'<div class="c"><div style="{boite}">{ouvre}'
                f'<div class="ombre" style="font-size:{px}px;line-height:1.15">{mots(t, 0, pas, classe, de)}</div>'
                f'{ferme}</div>' + (f'<div class="acc fade" style="font-size:50px;animation-delay:{.5 + n * .11:.2f}s">— '
                             f'{e(a.sous)}</div>' if a.sous else "") + '</div>'), False
    if kind == "lieu":
        pin = (f'<svg class="pop" width="150" height="190" viewBox="0 0 24 30" style="margin-bottom:30px">'
               f'<path fill="{c["accent"]}" d="M12 0C5.4 0 0 5.4 0 12c0 9 12 18 12 18s12-9 12-18C24 5.4 18.6 0 12 0z"/>'
               f'<circle cx="12" cy="12" r="4.5" fill="#fff"/></svg>')
        return (f'<div style="position:absolute;inset:0;background:linear-gradient(transparent 35%,rgba(0,0,0,.75))"></div>'
                f'<div class="c" style="justify-content:flex-end;padding-bottom:520px">{pin}'
                f'<div class="ombre" style="font-size:150px;line-height:1">{mots(t.upper(), .25, .12)}</div>'
                + (f'<div class="trait" style="margin:34px auto;animation-delay:.55s"></div>'
                   f'<div class="acc fade ombre" style="font-size:60px;animation-delay:.7s">{e(a.sous)}</div>' if a.sous else "")
                + (f'<div class="tampon" style="animation-delay:1s">{e(a.tampon)}</div>' if a.tampon else "")
                + '</div>'), False
    if kind == "logo":
        entree = o.get("entree")
        # rotation : l'échelle arrive vite, la rotation finit de se poser plus lentement (visible ~0,5 s)
        kf = {"rotation": "0%{opacity:0;transform:scale(2.6) rotate(-60deg)}15%{opacity:1}"
                          "30%{transform:scale(1) rotate(-22deg);animation-timing-function:cubic-bezier(.33,1,.68,1)}"
                          "to{opacity:1;transform:none}",
              "zoom": "0%{opacity:0;transform:scale(2.2)}15%{opacity:1}to{opacity:1;transform:none}"}.get(
            entree, "from{opacity:0;transform:scale(1.25);filter:blur(24px)}to{opacity:1;transform:none;filter:none}")
        amorti = entree in ("rotation", "zoom")
        anim_logo = (f'animation:logo {de or (.5 if amorti else .7):.2f}s '
                     f'{"cubic-bezier(.22,1,.36,1)" if amorti else "cubic-bezier(.2,.8,.2,1)"} both')
        dim = f"width:{round(taille * W)}px" if taille else "max-width:820px;max-height:620px"
        img = (f'<img src="{data_uri(a.image)}" style="{dim};mix-blend-mode:multiply;{anim_logo}">' if a.image else
               f'<div style="font-size:140px;line-height:1;color:{c["fond"]};letter-spacing:4px;'
               f'{anim_logo}">{e(t)}</div>')
        return (f'<style>@keyframes logo{{{kf}}}</style>'
                # vignette : part du blanc pur et grisonne lentement ; sinon le dégradé fixe d'avant
                + ('<div style="position:absolute;inset:0;background:#fff"></div><div class="vig"></div>'
                   if o.get("vignette") else
                   '<div style="position:absolute;inset:0;background:radial-gradient(#fff 40%,#e9ecef)"></div>') +
                f'<div class="c">{img}' + (f'<div class="fade" style="font-size:56px;margin-top:30px;color:{c["fond"]};'
                                          f'animation-delay:.5s">{e(a.sous)}</div>' if a.sous else "")
                + (f'<div class="tampon" style="animation-delay:.9s">{e(a.tampon)}</div>' if a.tampon else "")
                + '</div>'), True
    if kind == "chiffre":
        nb = nombre(t)
        val = (f'{e(nb[1])}<span id="compteur" data-n="{nb[0]}">0</span>{e(nb[2])}' if nb else e(t))
        return (f'<div class="c"><div class="acc pop ombre" style="font-size:230px;line-height:1">{val}</div>'
                + (f'<div class="ombre" style="font-size:66px;margin-top:30px">{mots(a.sous, .45, .08)}</div>' if a.sous else "")
                + '</div>'), False
    if kind == "liste":
        items = "".join(
            f'<div class="fade" style="display:flex;align-items:center;gap:36px;margin:26px 0;font-size:80px;'
            f'animation:liste .4s cubic-bezier(.2,.8,.2,1) {.35 + i * .3:.2f}s both">'
            f'<span style="flex:none;width:96px;height:96px;border-radius:50%;background:var(--a);color:{c["fond"]};'
            f'display:flex;align-items:center;justify-content:center;font-size:60px">✕</span>'
            f'<span class="ombre" style="text-align:left">{e(x)}</span></div>' for i, x in enumerate(a.textes))
        return (f'<style>@keyframes liste{{from{{opacity:0;transform:translateX(-120px)}}to{{opacity:1;transform:none}}}}</style>'
                f'<div class="c" style="align-items:flex-start;padding:0 120px">'
                + (f'<div class="acc fade" style="font-size:64px;letter-spacing:6px;margin-bottom:30px">'
                   f'{e(a.titre.upper())}</div>' if a.titre else "") + items + '</div>'), False
    die(f"gabarit inconnu : {kind}")


def page_html(corps, c):
    return (f"<html><head><meta charset='utf-8'><style>{font_face(c['police'])}"
            f":root{{--a:{c['accent']};--t:{c['texte']}}}{CSS}"
            f"body{{font-weight:{c['graisse']};letter-spacing:{c.get('interlettrage') or 0:.4f}em}}</style></head><body>{corps}"
            "<script>function seek(ms){document.getAnimations().forEach(a=>{a.pause();a.currentTime=ms});"
            "const k=document.getElementById('compteur');if(k){const p=Math.min(1,Math.max(0,(ms-250)/900));"
            "k.textContent=Math.round(k.dataset.n*(1-Math.pow(1-p,3))).toLocaleString('fr-FR')}}</script>"
            "</body></html>")


def navigateur(p):
    for ch in ("msedge", "chrome", None):  # navigateur déjà installé d'abord, sinon Chromium de Playwright
        try:
            return p.chromium.launch(channel=ch) if ch else p.chromium.launch()
        except Exception:
            continue
    die("aucun navigateur pour les animations (Edge, Chrome ou `python -m playwright install chromium`)")


def images(html_txt, n, dossier):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        die("Playwright absent : python scripts/doctor.py --installer (ou pip install playwright)")
    with sync_playwright() as p:
        b = navigateur(p)
        pg = b.new_page(viewport={"width": W, "height": H})
        pg.set_content(html_txt, wait_until="load")
        pg.evaluate("document.fonts.ready")
        for i in range(n):
            pg.evaluate(f"seek({i * 1000 / FPS:.1f})")
            pg.screenshot(path=str(dossier / f"{i:04d}.png"), omit_background=True)
        b.close()


def fond_args(a, dur):
    """Entrée ffmpeg + filtre du fond (couvre 1080×1920)."""
    cover = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    sombre = f",colorchannelmixer=rr={1 - a.sombre}:gg={1 - a.sombre}:bb={1 - a.sombre}" if a.sombre else ""
    flou = f",gblur=sigma={a.flou}" if a.flou else ""
    src = a.fond
    if not src or src.startswith("#"):
        return ["-f", "lavfi", "-i", f"color=c={src or 'black'}:s={W}x{H}:r={FPS}:d={dur:.3f}"], "null"
    if not Path(src).exists():
        die(f"fond introuvable : {src}")
    if Path(src).suffix.lower() in IMAGE_EXT:  # photo : lente poussée (8 %)
        return (["-loop", "1", "-t", f"{dur:.3f}", "-i", src],
                f"{cover},scale=iw*1.08:ih*1.08,crop={W}:{H}:x='(iw-{W})*t/{dur:.3f}':y='(ih-{H})/2'"
                f",fps={FPS}{sombre}{flou}")
    info = probe(src)
    if a.profil and info.get("width"):  # rush : même cadre et même couleur qu'au rendu, pas le plan large brut
        cover = cadre_du_rendu(src, info, a.fond_debut, dur, a.profil)
    return (["-ss", f"{a.fond_debut:.3f}", "-t", f"{dur:.3f}", "-i", src],
            f"fps={FPS},{tonemap(info)}{cover}{sombre}{flou}")


def cadre_du_rendu(src, info, t0, dur, profil):
    """Recadrage 9:16 (visage suivi, cadrage de la DA) + correction couleur que render.py applique à ce rush."""
    import reframe as rf
    import render
    p = json.loads(Path(profil).read_text(encoding="utf-8"))
    cache = Path(profil).resolve().parent.parent / ".monteur"
    track = rf.face_track(src, t0, t0 + dur, FPS, cache_dir=cache if cache.is_dir() else None)
    seg = {"start": t0, "frames": max(1, round(dur * FPS)), "zoom_center": "visage"}
    crops, _, _ = render.plan(seg, info, W, H, FPS, track, None, 1.0, 0.5, True, p.get("cadrage"))
    x, y, cw, ch = crops[len(crops) // 2]  # shortcut: cadre fixe (milieu de l'insert) ; suivi image par image si la tête bouge beaucoup
    box = (x / info["width"], y / info["height"], (x + cw) / info["width"], (y + ch) / info["height"])
    col = rf.colour_filter(rf.measure_colour(src, box=box), p.get("couleur"))
    return f"crop={cw:.0f}:{ch:.0f}:{x:.0f}:{y:.0f},scale={W}:{H}:flags=lanczos,setsar=1" + (f",{col}" if col else "")


def nettete(clip, ips=10):
    """Énergie des contours de chaque image (ips images/s, 96×96 en gris) : basse = flou, vide ou fondu."""
    import subprocess
    from PIL import Image, ImageFilter, ImageStat
    raw = subprocess.run([ffmpeg(), "-v", "error", "-i", str(clip), "-vf", f"fps={ips},scale=96:96,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    n = 96 * 96
    return [ImageStat.Stat(Image.frombytes("L", (96, 96), raw[i:i + n]).filter(ImageFilter.FIND_EDGES)
                           .crop((1, 1, 95, 95))).mean[0] for i in range(0, len(raw) - n + 1, n)]


def passage_net(clip, part, ips=10):
    """(début, fin) du passage le plus net de `part` s du clip ; les images floues/vides (< 35 % de la netteté
    médiane : fondu, flou de bougé, écran uni) du dernier cinquième sont retirées (la dernière nette est tenue)."""
    if Path(clip).suffix.lower() in IMAGE_EXT:
        return 0.0, part
    s = nettete(clip, ips)
    if len(s) < 2:
        return 0.0, part
    seuil = 0.35 * sorted(s)[len(s) // 2]
    k = max(1, round(part * ips))
    debuts = range(max(1, len(s) - k + 1))
    i = max(debuts, key=lambda d: (sum(v >= seuil for v in s[d:d + k]), sum(s[d:d + k])))
    j = i + k
    # une image floue/vide dans le dernier cinquième = fondu ou transition de fin : on coupe au début de ce passage
    bas = [m for m in range(max(i + 1, j - max(1, k // 5)), min(j, len(s))) if s[m] < seuil]
    if bas:
        j = bas[0]
        while j > i + 1 and s[j - 1] < seuil:
            j -= 1
    return i / ips, min(i / ips + part, j / ips)


def mosaique_filtre(clips, dur, k0, rayon=None, ombre=None, fenetres=None):
    """Clips l'un après l'autre dans une carte arrondie au centre, entrée en fondu. Carte dans le sens des clips :
    paysage (960×600) si la plupart sont horizontaux (vidéos d'événements), sinon portrait (860×1180).
    `rayon` (px) des coins, `ombre` (0-1, opacité de l'ombre portée sous la carte, 0 = aucune) : graphismes[] du
    profil. `fenetres` = (début, fin) de chaque clip dans son entrée (passage_net) ; défaut : son début."""
    infos = [probe(c) for c in clips]
    paysage = sum(i.get("width", 0) > i.get("height", 0) for i in infos) * 2 > len(infos)
    cw, ch, r = (960, 600, 40) if paysage else (860, 1180, 48)
    r = round(num(rayon, r))
    part = dur / len(clips)
    f, ins = [], []
    for i, (c, info) in enumerate(zip(clips, infos)):
        a, b = (fenetres or [(0.0, part)] * len(clips))[i]
        tient = f",tpad=stop_mode=clone:stop_duration={part - (b - a):.3f}" if part - (b - a) > 1e-3 else ""
        f.append(f"[{k0 + i}:v]fps={FPS},{tonemap(info)}scale={cw}:{ch}:force_original_aspect_ratio=increase,"
                 f"crop={cw}:{ch},setsar=1,trim=0:{b - a:.3f},setpts=PTS-STARTPTS{tient},format=yuva420p[c{i}]")
        ins.append(f"[c{i}]")
    mask = (f"geq=lum='255*lte(hypot(max(0,abs(X-{cw}/2)-({cw}/2-{r})),max(0,abs(Y-{ch}/2)-({ch}/2-{r}))),{r})'")
    f.append(f"{''.join(ins)}concat=n={len(clips)}:v=1:a=0,format=yuva420p[cc]")
    f.append(f"color=c=black:s={cw}x{ch}:r={FPS}:d={dur:.3f},format=gray,{mask}[mk]")
    x, y = (W - cw) // 2, (H - ch) // 2 + 60
    o = num(ombre, 0)
    if o:  # ombre portée : le masque de la carte, flouté, décalé vers le bas, en noir translucide
        f[-1] = f[-1].replace("[mk]", ",split[mk][mko]")
        p = 60
        f.append(f"color=c=black:s={cw + 2 * p}x{ch + 2 * p}:r={FPS}:d={dur:.3f}[ob];[mko]pad={cw + 2 * p}:{ch + 2 * p}:"
                 f"{p}:{p}:black,gblur=sigma=18[mko2];[ob][mko2]alphamerge,colorchannelmixer=aa={min(1, o):.2f},"
                 f"fade=in:st=0:d=0.25:alpha=1[ombre]")
    f.append("[cc][mk]alphamerge,fade=in:st=0:d=0.25:alpha=1[carte]")
    return f, x, y


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("type", choices=["question", "citation", "lieu", "logo", "chiffre", "liste", "mosaique", "html"])
    ap.add_argument("textes", nargs="*")
    ap.add_argument("--sous")
    ap.add_argument("--titre")
    ap.add_argument("--tampon")
    ap.add_argument("--texte", help="mosaique : phrase au-dessus de la carte")
    ap.add_argument("--image", help="logo : fichier image du logo")
    ap.add_argument("--clips", nargs="+", default=[], help="mosaique : vidéos/photos à enchaîner dans la carte")
    ap.add_argument("--fond", help="vidéo, photo ou couleur #RRGGBB sous le graphisme")
    ap.add_argument("--fond-debut", type=float, default=0.0, help="vidéo de fond : seconde de départ dans la source")
    ap.add_argument("--sombre", type=float, help="0-1, assombrit le fond (défaut 0,55 sur vidéo/photo)")
    ap.add_argument("--flou", type=float, help="flou du fond (défaut 14 sur vidéo/photo)")
    ap.add_argument("--duree", type=float, default=2.5)
    ap.add_argument("--profil", help="ma-da/profile.json : couleurs et police de la DA")
    ap.add_argument("--accent")
    g = ap.add_argument_group("habillage (défaut : graphismes[] du profil, sinon le rendu d'avant)")
    g.add_argument("--police", help="police des cartons (défaut : celle des sous-titres)")
    g.add_argument("--graisse", type=int, help="400, 600 ou 800")
    g.add_argument("--interlettrage", type=float, help="%% de l'em (-2.5 = serré)")
    g.add_argument("--taille", "--taille-logo", dest="taille", type=float,
                   help="capitales des petits mots / du texte en part de la hauteur ; logo : largeur en part de la largeur")
    g.add_argument("--entree", "--entree-mots", dest="entree",
                   help="question, citation, mosaique : monte|disperse ; logo : rotation|zoom|flou")
    g.add_argument("--duree-entree", type=float, help="secondes")
    g.add_argument("--anneaux", choices=["nets", "flous", "aucun"])
    g.add_argument("--fin", help="sortie : fuite:#hex | flash:#hex | coupe")
    g.add_argument("--teinte", help="#hex : fond vidéo assombri teinté de cette couleur")
    g.add_argument("--guillemets", choices=["serif", "ronds"])
    g.add_argument("--vignette", action="store_const", const=True, help="logo : vignette grise lente")
    g.add_argument("--rayon", type=float, help="mosaique : rayon des coins de la carte (px)")
    g.add_argument("--ombre", type=float, help="mosaique : opacité 0-1 de l'ombre portée de la carte (0 = aucune)")
    ap.add_argument("--sortie", required=True)
    a = ap.parse_args(argv)
    if a.type in ("question", "citation", "lieu", "chiffre", "liste", "html") and not a.textes:
        die(f"{a.type} : texte manquant")
    if a.type == "mosaique" and not a.clips:
        die("mosaique : --clips manquant")
    if a.type == "logo" and not (a.textes or a.image):
        die("logo : texte ou --image manquant")
    c = da(a.profil, a.type)
    for opt, cle in SCHEMA.items():  # ce que la ligne de commande ne dit pas, le profil le dit
        if getattr(a, opt) is None:
            setattr(a, opt, c["g"].get(cle))
    c["police"] = a.police or c["police"]
    c["graisse"] = int(num(a.graisse, c["graisse"]))
    c["interlettrage"] = (num(a.interlettrage) or 0) / 100
    if a.accent:
        c["accent"] = a.accent
    media = a.fond and not a.fond.startswith("#")
    a.sombre = num(a.sombre, 0.65 if media else 0)
    a.flou = num(a.flou, 14 if media else 0)
    if not a.fond:
        a.fond = c["fond"]
    corps, opaque = gabarit(a.type, a, c)
    teinte = a.teinte if a.teinte and HEX.fullmatch(str(a.teinte)) else None
    corps = ((f'<div style="position:absolute;inset:0;background:{teinte};opacity:.22"></div>' if teinte else "")
             + corps + sortie_html(a.fin, a.duree))
    n = max(1, round(a.duree * FPS))
    out = Path(a.sortie)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="anim_"))
    try:
        images(page_html(corps, c), n, tmp)
        args, ff = fond_args(a, a.duree) if not opaque else (
            ["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={a.duree:.3f}"], "null")
        args += ["-framerate", str(FPS), "-i", str(tmp / "%04d.png")]
        graph = [f"[0:v]{ff},format=yuv420p[bg]", "[bg][1:v]overlay=format=auto:shortest=1[v0]"]
        last = "v0"
        if a.type == "mosaique":
            fen = [passage_net(clip, a.duree / len(a.clips)) for clip in a.clips]
            for clip, (deb, fin) in zip(a.clips, fen):
                if Path(clip).suffix.lower() in IMAGE_EXT:
                    args += ["-loop", "1", "-t", f"{a.duree:.3f}", "-i", clip]
                else:
                    args += ["-ss", f"{deb:.3f}", "-t", f"{fin - deb + 0.5:.3f}", "-i", clip]
            f, x, y = mosaique_filtre(a.clips, a.duree, 2, a.rayon, a.ombre, fen)
            base = "v0"
            if any("[ombre]" in g for g in f):  # ombre sous la carte, un peu plus bas (lumière d'en haut)
                f.append(f"[v0][ombre]overlay={x - 60}:{y - 42}:format=auto[v0o]")
                base = "v0o"
            graph += f + [f"[{base}][carte]overlay={x}:{y}:format=auto[v1]"]
            last = "v1"
        args = [x for y in args for x in ([*FF_THREADS, y] if y == "-i" else [y])]  # un décodeur par entrée, sobre
        run([ffmpeg(), "-y", "-v", "error", "-filter_complex_threads", FF_THREADS[1], *args, "-filter_complex",
             ";".join(graph), "-map", f"[{last}]", "-frames:v", str(n), "-r", str(FPS), *FF_THREADS, "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
             "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
             "-an", str(out)])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # sous-titres sur l'insert : graphismes[].sous_titres du profil ; défaut = gardés seulement sur une mosaïque sans
    # texte propre (les autres gabarits affichent déjà leur phrase)
    sous = c["g"].get("sous_titres", a.type == "mosaique" and not a.texte) is True
    evite = ""
    if sous and a.type == "mosaique":  # les sous-titres évitent la carte (haut, bas en part de la hauteur)
        _, x, y = mosaique_filtre(a.clips, a.duree, 2)
        evite = f', "evite": [{y / H:.3f}, {(H - y + 120) / H:.3f}]'
    print(f"Insert animé : {out} ({a.duree:.1f} s) → edl broll : "
          f'{{"source": "{out.as_posix()}", "at": …, "duration": {a.duree}, "texte": true, "tenir": true, '
          f'"sous_titres": {str(sous).lower()}{evite}}}')


if __name__ == "__main__":
    main()
