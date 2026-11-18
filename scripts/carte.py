"""Illustrations graphiques (sans internet) pour couvrir ce qui est dit : citation, chiffre, liste, lieu, trajet A → B.
Sortie PNG 1080×1920 (plein écran) ou 1080×1080 (--vignette, posée par-dessus l'orateur). Couleurs de la DA.

Exemples :
  python scripts/carte.py citation "Nul n'est prophète en son pays" --sous "Dicton" --sortie broll/citation.png
  python scripts/carte.py chiffre "15 ans" --sous "des centaines d'événements" --vignette --sortie broll/15ans.png
  python scripts/carte.py liste "Réseau : 0" "Contacts : 0" "Budget : petit" --titre "Au départ" --sortie broll/zero.png
  python scripts/carte.py lieu "Nikki Beach" --sous "Dubaï" --tampon "SIGNÉ" --sortie broll/nikki.png
  python scripts/carte.py trajet "Maurice" "Dubaï" --sortie broll/trajet.png
  … --photo broll/dubai.jpg : photo floutée en fond (cartes plus vivantes)"""
import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FONT = Path(__file__).parent / "fonts" / "Poppins-ExtraBold.ttf"
FOND, ACCENT, TEXTE = "#0E2233", "#F2A516", "#FFFFFF"


def font(px):
    return ImageFont.truetype(str(FONT), px)


def hexa(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def fond(w, h, haut, bas):
    """Dégradé vertical + halo doux : moins plat qu'un aplat."""
    a, b = np.array(hexa(haut), float), np.array(hexa(bas), float)
    t = np.linspace(0, 1, h)[:, None, None]
    img = Image.fromarray(np.broadcast_to(a + (b - a) * t, (h, w, 3)).astype(np.uint8))
    halo = Image.new("L", (w, h), 0)
    ImageDraw.Draw(halo).ellipse((w * 0.1, h * 0.25, w * 0.9, h * 0.65), fill=70)
    halo = halo.filter(ImageFilter.GaussianBlur(w // 6))
    return Image.composite(Image.new("RGB", (w, h), (255, 255, 255)), img, halo)


def lignes(d, texte, f, largeur):
    mots, out, cur = texte.split(), [], ""
    for m in mots:
        essai = f"{cur} {m}".strip()
        if d.textlength(essai, font=f) <= largeur or not cur:
            cur = essai
        else:
            out.append(cur)
            cur = m
    return out + [cur] if cur else out


def bloc(d, texte, f, cx, y, largeur, couleur, interligne=1.15):
    """Texte centré sur plusieurs lignes à partir de y ; renvoie le y du bas."""
    for ligne in lignes(d, texte, f, largeur):
        d.text((cx, y), ligne, font=f, fill=couleur, anchor="ma")
        y += round(f.size * interligne)
    return y


def carte(kind, textes, sous=None, titre=None, tampon=None, vignette=False, couleurs=(FOND, ACCENT, TEXTE),
          photo=None):
    fd, ac, tx = couleurs
    w, h = (1080, 1080) if vignette else (1080, 1920)
    img = fond(w, h, fd, "#000000")
    if photo:  # photo floutée et assombrie derrière le texte : plus vivant qu'un aplat
        ph = ImageOps.fit(ImageOps.exif_transpose(Image.open(photo)).convert("RGB"), (w, h)).filter(ImageFilter.GaussianBlur(14))
        img = Image.blend(ph, img, 0.62)
    d = ImageDraw.Draw(img)
    cx, L = w // 2, w - 160
    y = h * (0.40 if vignette else 0.34)
    if kind == "citation":
        d.text((cx, y - 40), "“", font=font(260), fill=ac, anchor="ms")
        y = bloc(d, textes[0], font(86 if not vignette else 72), cx, y + 20, L, tx)
        if sous:
            bloc(d, f"— {sous}", font(44), cx, y + 40, L, ac)
    elif kind == "chiffre":
        y = bloc(d, textes[0].upper(), font(220 if not vignette else 180), cx, y - 40, L, ac, 1.0)
        if sous:
            bloc(d, sous, font(64 if not vignette else 54), cx, y + 30, L, tx)
    elif kind == "liste":
        if titre:
            y = bloc(d, titre.upper(), font(70), cx, y - 120, L, ac) + 50
        f = font(76 if not vignette else 62)
        for t in textes:
            r = f.size // 2
            d.ellipse((120, y + 8, 120 + 2 * r, y + 8 + 2 * r), outline=ac, width=8)
            d.line((120 + r * 0.55, y + 8 + r * 0.55, 120 + r * 1.45, y + 8 + r * 1.45), fill=ac, width=8)
            d.line((120 + r * 1.45, y + 8 + r * 0.55, 120 + r * 0.55, y + 8 + r * 1.45), fill=ac, width=8)
            d.text((120 + 2 * r + 40, y), t, font=f, fill=tx)
            y += round(f.size * 1.7)
    elif kind == "lieu":
        r = 70
        d.ellipse((cx - r, y - 3 * r, cx + r, y - r), fill=ac)  # épingle
        d.polygon([(cx - r * 0.75, y - 1.5 * r), (cx + r * 0.75, y - 1.5 * r), (cx, y - 0.1 * r)], fill=ac)
        d.ellipse((cx - r * 0.38, y - 2.38 * r, cx + r * 0.38, y - 1.62 * r), fill=fd)
        y = bloc(d, textes[0].upper(), font(130 if not vignette else 110), cx, y + 30, L, tx, 1.05)
        if sous:
            y = bloc(d, sous, font(60), cx, y + 10, L, ac)
        if tampon:
            st = Image.new("RGBA", (620, 220), (0, 0, 0, 0))
            sd = ImageDraw.Draw(st)
            sd.rounded_rectangle((10, 10, 610, 210), radius=30, outline=ac, width=14)
            sd.text((310, 112), tampon, font=font(110), fill=ac, anchor="mm")
            st = st.rotate(-12, expand=True, resample=Image.BICUBIC)
            img.paste(st, (cx - st.width // 2, round(y + 60)), st)
    elif kind == "trajet":
        a, b = textes[0], textes[1]
        y0, y1 = h * 0.62, h * 0.30
        xa, xb = w * 0.22, w * 0.78
        pts = [(xa + (xb - xa) * t, y0 + (y1 - y0) * t - math.sin(math.pi * t) * h * 0.12) for t in
               [i / 60 for i in range(61)]]
        for i in range(0, 60, 4):  # pointillés
            d.line(pts[i:i + 3], fill=tx, width=10)
        for (x, yy), nom in (((xa, y0), a), ((xb, y1), b)):
            d.ellipse((x - 34, yy - 34, x + 34, yy + 34), fill=ac)
            d.text((x, yy + 60), nom.upper(), font=font(84), fill=tx, anchor="ma")
        xm, ym = pts[30]
        d.polygon([(xm + 46, ym), (xm - 30, ym - 30), (xm - 14, ym), (xm - 30, ym + 30)], fill=ac)  # avion
        if titre:
            bloc(d, titre.upper(), font(64), cx, h * 0.12, L, ac)
    else:
        raise SystemExit(f"type inconnu : {kind}")
    return img


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("type", choices=["citation", "chiffre", "liste", "lieu", "trajet"])
    ap.add_argument("textes", nargs="+")
    ap.add_argument("--sous")
    ap.add_argument("--titre")
    ap.add_argument("--tampon", help="lieu : mot tamponné en biais (SIGNÉ, OUVERT…)")
    ap.add_argument("--vignette", action="store_true", help="carré 1080×1080, à poser en vignette (broll vignette)")
    ap.add_argument("--fond", default=FOND)
    ap.add_argument("--accent", default=ACCENT)
    ap.add_argument("--photo", help="photo floutée en fond (ex. celle trouvée par illustrer.py)")
    ap.add_argument("--sortie", required=True)
    a = ap.parse_args(argv)
    if a.type == "trajet" and len(a.textes) < 2:
        raise SystemExit("trajet : deux lieux (départ arrivée)")
    img = carte(a.type, a.textes, a.sous, a.titre, a.tampon, a.vignette, (a.fond, a.accent, TEXTE), a.photo)
    Path(a.sortie).parent.mkdir(parents=True, exist_ok=True)
    img.save(a.sortie)
    print(f"{a.type} : {a.sortie} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()
