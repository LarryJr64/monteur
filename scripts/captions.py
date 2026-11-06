"""Mots du transcript → timeline de sortie → sous-titres .ass (brûlés) / .srt (export).

Aussi le recalage des coupes sur les mots, partagé par render.py et export_xml.py
pour que vidéo, XML et SRT aient exactement les mêmes temps.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, find_transcript  # noqa: E402
from pacing import is_filler  # noqa: E402  (même liste d'hésitations que le rythme : « e », « hm », « [euh] »…)

FONTS_DIR = Path(__file__).parent / "fonts"
SENTENCE_END = re.compile(r"[.!?…]$")
PAUSE_MAX = 0.4
SNAP_TOL = 0.08

# taille_pct_hauteur = hauteur des CAPITALES / hauteur image (ce qu'on voit et ce que measure.py mesure).
# libass dimensionne la police par sa hauteur de ligne : capitales ≈ 0,45 × Fontsize (mesuré, Montserrat ExtraBold).
# shortcut: ratio unique pour toutes les polices (Poppins/Arial varient de ±15 %), à calibrer par police si besoin.
CAP_RATIO = 0.45  # repli (police système dont on n'a pas le fichier) ; sinon font_ratios() lit la police


def font_ratios(path):
    """(capitales / Fontsize, em / Fontsize) tels que libass les dessine : il ramène usWinAscent + usWinDescent
    (table OS/2) à Fontsize (vérifié au pixel sur les 4 polices embarquées). None si la table est illisible."""
    import struct
    try:
        data = Path(path).read_bytes()
        tables = {data[12 + 16 * i:16 + 16 * i]: struct.unpack(">I", data[20 + 16 * i:24 + 16 * i])[0]
                  for i in range(struct.unpack(">H", data[4:6])[0])}
        upm = struct.unpack(">H", data[tables[b"head"] + 18:tables[b"head"] + 20])[0]
        o = tables[b"OS/2"]
        win = sum(struct.unpack(">HH", data[o + 74:o + 78]))
        cap = struct.unpack(">h", data[o + 88:o + 90])[0] if struct.unpack(">H", data[o:o + 2])[0] >= 2 else 0
        return (cap / win if cap > 0 else None), upm / win
    except (OSError, KeyError, struct.error):
        return None
DEFAULT_ST = {"actif": True, "police": "Montserrat", "gras": True, "taille_pct_hauteur": 0.03,
              "couleur": "#FFFFFF", "couleur_mot_actif": "#FFE600", "contour": "#000000",
              "epaisseur_contour": 6, "position_y_pct": 0.70, "mots_par_bloc": 3,
              "majuscules": True, "animation": "pop", "couleur_mot_cle": "#FDE047", "zone_sure": False}


# ---------- transcripts et timeline ----------

def root_of(edl_path):
    """Dossier de travail : parent de .monteur/ (les chemins de l'edl y sont relatifs)."""
    p = Path(edl_path).resolve().parent
    return p.parent if p.name == ".monteur" else p


def load_transcript(root, source):
    return find_transcript(root, source)


def snap(t, words):
    """Ramène une coupe tombant dans un mot sur la frontière la plus proche de ce mot.
    Les coupes à moins de 80 ms à l'intérieur d'un mot (imprécision Whisper) sont tolérées, sauf si ce bord du
    mot touche le mot voisin (parole continue : on entendrait un bout de syllabe, « et » avant « dis-moi »)."""
    for i, w in enumerate(words):
        if not w["start"] < t < w["end"]:
            continue
        nearest = w["start"] if t - w["start"] < w["end"] - t else w["end"]
        if w["start"] + SNAP_TOL < t < w["end"] - SNAP_TOL:
            return nearest
        touches = (nearest == w["end"] and i + 1 < len(words) and words[i + 1]["start"] - w["end"] < 0.05) or                   (nearest == w["start"] and i > 0 and w["start"] - words[i - 1]["end"] < 0.05)
        return nearest if touches else t
    return t


def snapped_segments(edl, root):
    """Segments de l'edl recalés sur les mots, avec leur position `offset` dans la vidéo finale.
    Les durées sont arrondies à l'image pour que tous les modules partagent la même grille."""
    fps = edl["format"]["fps"]
    out, offset = [], 0.0
    for i, s in enumerate(edl["segments"]):
        tr = load_transcript(root, s["source"])
        words = tr["words"] if tr else []
        start, end = snap(s["start"], words), snap(s["end"], words)
        if end - start < 1 / fps:
            start, end = s["start"], s["end"]
        frames = max(1, round((end - start) * fps))
        seg = dict(s, start=start, end=start + frames / fps, frames=frames, offset=offset, index=i)
        out.append(seg)
        offset += frames / fps
    return out


def norm(text):
    return re.sub(r"[^\w']", "", text.lower().replace("’", "'"))


def is_keyword(word, keys):
    """« erreur » surligne aussi « l'erreur » ; comparaison sans ponctuation ni casse."""
    n = norm(word)
    return bool(n) and any(n == k or n.endswith("'" + k) for k in keys)


def timeline_words(segments, root):
    """Mots des segments recalés sur la timeline finale (temps de sortie).
    v2 : `cle` = mot de `mots_cles` du segment ; `emoji` du segment posé sur son 1er mot-clé (sinon 1er mot)."""
    res, groups = [], {}
    for i, s in enumerate(segments):
        tr = load_transcript(root, s["source"])
        keys = {norm(k) for k in s.get("mots_cles") or [] if norm(k)}
        # pacing découpe un segment en morceaux (`seg` = segment d'origine) : un seul emoji par segment d'origine
        group = groups.setdefault(s.get("seg", i), [s.get("emoji"), []])
        for w in (tr or {}).get("words", []):
            mid = (w["start"] + w["end"]) / 2
            if s["start"] <= mid < s["end"]:
                a = s["offset"] + max(0.0, w["start"] - s["start"])
                b = s["offset"] + min(s["end"], w["end"]) - s["start"]
                res.append({"w": w["w"].strip(), "start": a, "end": max(b, a + 0.05)})
                if is_filler(w):  # hésitation gardée au son (note « garde », non isolée) : jamais à l'écran
                    res[-1]["filler"] = True
                if keys and is_keyword(w["w"], keys):
                    res[-1]["cle"] = True
                group[1].append(res[-1])
    for emoji, ws in groups.values():
        if emoji and ws:
            host = next((w for w in ws if w.get("cle")), None) or next(
                (w for w in ws if not w.get("filler")), ws[0])
            host["emoji"] = emoji
    return res


# ---------- blocs ----------

def clean_words(words):
    """Retire les hésitations, colle les élisions (« l' » + « idée »), enlève virgules et points."""
    out = []
    for w in words:
        text = w["w"].replace("’", "'").strip()
        if w.get("filler") or is_filler(w) or not re.search(r"\w", text):
            if out and re.search(r"[.!?…]$", text):  # ponctuation isolée : marque quand même la fin de phrase
                out[-1]["fin"] = True
            continue
        fin = bool(SENTENCE_END.search(text))
        text = re.sub(r"[,;:.…]+$", "", text)  # garde ? et ! qui portent le ton
        text = re.sub(r"\s+([?!])", r" \1", text)
        # « l' idée », « l 'hormone » (découpage Whisper), « abonne -toi »
        extra = {k: w[k] for k in ("cle", "emoji") if w.get(k)}
        if out and (out[-1]["w"].endswith("'") or re.match(r"'|-\w", text)):
            prev = out.pop()
            w = dict(w, start=prev["start"])
            text = prev["w"] + text
            extra = {**{k: prev[k] for k in ("cle", "emoji") if prev.get(k)}, **extra}
        out.append({"w": text, "start": w["start"], "end": w["end"], "fin": fin, **extra})
    return out


def make_blocks(words, per_block=3):
    """Groupes de `per_block` mots, jamais à travers une pause > 0,4 s ni une fin de phrase."""
    blocks, cur = [], []
    for w in clean_words(words):
        if cur and (len(cur) >= per_block or w["start"] - cur[-1]["end"] > PAUSE_MAX or cur[-1]["fin"]):
            blocks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        blocks.append(cur)
    res = []
    for i, b in enumerate(blocks):
        end = b[-1]["end"] + 0.25
        if i + 1 < len(blocks):
            nxt = blocks[i + 1][0]["start"]
            end = nxt if nxt - b[-1]["end"] < 0.5 else min(end, nxt)  # pas de clignotement entre blocs proches
        res.append({"start": b[0]["start"], "end": end, "words": b})
    return res


# ---------- ASS ----------

def ass_color(hexa, alpha=0):
    h = hexa.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_time(t):
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def esc(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


# polices embarquées (OFL, voir fonts/OFL-*.txt) : déjà grasses, pas de faux gras
BUNDLED = {"montserrat": "Montserrat ExtraBold", "poppins": "Poppins ExtraBold", "anton": "Anton", "bebas neue": "Bebas Neue"}
WIN_FILES = {"arial black": "ariblk", "impact": "impact", "arial": "arial"}


def _installed(name):
    """Police présente sur le système ? (heuristique sur les noms de fichiers)"""
    key = WIN_FILES.get(name.lower(), re.sub(r"\W", "", name.lower()))
    dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts",
            Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path("/usr/share/fonts"), Path.home() / ".fonts"]
    return any(re.sub(r"\W", "", f.stem.lower()).startswith(key)
               for d in dirs if d.is_dir() for f in d.rglob("*") if f.suffix.lower() in (".ttf", ".otf"))


def font_name(st):
    """(nom ASS, gras ?) : police embarquée si possible, sinon système, sinon repli embarqué puis Arial."""
    want = st.get("police") or "Montserrat"
    if want.lower() in BUNDLED:
        return BUNDLED[want.lower()], False
    if _installed(want):
        return want, bool(st.get("gras", True))
    if (FONTS_DIR / "Montserrat-ExtraBold.ttf").exists():
        print(f"Police « {want} » absente : repli sur Montserrat ExtraBold (embarquée).")
        return "Montserrat ExtraBold", False
    return "Arial", bool(st.get("gras", True))


FONT_FILES = {"Montserrat ExtraBold": "Montserrat-ExtraBold.ttf", "Poppins ExtraBold": "Poppins-ExtraBold.ttf",
              "Anton": "Anton-Regular.ttf", "Bebas Neue": "BebasNeue-Regular.ttf"}
KW_SCALE = 115   # mot-clé à 115 % (teardown : 110-120 %)
POP_MS = 100     # pop 80 → 100 % en 100 ms (teardown : 80-120 ms)
ENTREE_MS = 120  # glissé / fondu / rebond : 4 images à 30 i/s
ANIMATIONS = ("pop", "rebond", "glisse", "fondu", "karaoke", "mot_par_mot", "surlignage", "aucune")
LINE_H = 1.1     # hauteur d'une ligne libass ≈ 1,1 × Fontsize (mesuré sur les rendus)


def layout(words, st, width, height, face_at=None):
    """Blocs de sous-titres placés : x, y (centre, px), demi-hauteur, mot-clé (≤ 1 par bloc), emoji.
    `face_at(a, b)` → (haut, bas) du visage en px dans l'image de sortie entre a et b, ou None :
    un bloc qui couvrirait le visage passe dessous (dans la zone sûre), sinon dessus.
    Zone sûre (`zone_sure`) : rien sous 80 % de la hauteur, marge droite élargie (boutons TikTok/Reels)."""
    from PIL import ImageFont
    st = {**DEFAULT_ST, **(st or {})}
    font, _ = font_name(st)
    ttf = FONTS_DIR / FONT_FILES.get(font, "Montserrat-ExtraBold.ttf")  # police système : mesurée comme Montserrat
    cap_r, em_r = font_ratios(ttf) or (None, 0.64)
    if font not in FONT_FILES:
        cap_r = None
    size = round(st["taille_pct_hauteur"] * height / (cap_r or CAP_RATIO))
    em = size * em_r
    safe = bool(st.get("zone_sure"))
    ml = width // 12
    mr = round(width * 0.15) if safe else ml
    pil = ImageFont.truetype(str(ttf), max(1, round(em)))  # Pillow : taille = em ; libass : Fontsize = ligne « Win »
    bottom = (0.80 if safe else 0.94) * height
    y0 = st["position_y_pct"] * height
    out = []
    for blk in make_blocks(words, int(st["mots_par_bloc"])):
        ws = blk["words"]
        toks = [w["w"].upper() if st["majuscules"] else w["w"] for w in ws]
        kw = next((i for i, w in enumerate(ws) if w.get("cle")), None)
        widths = [pil.getlength(t) * (KW_SCALE / 100 if i == kw else 1) for i, t in enumerate(toks)]
        space = pil.getlength(" ")
        text_w = sum(widths) + space * (len(toks) - 1)
        lines = max(1, -(-int(text_w * 1.05) // (width - ml - mr)))
        hh = lines * size * LINE_H / 2
        y = min(y0, bottom - hh)
        box = face_at(blk["start"], blk["end"]) if face_at else None
        if box and y - hh < box[1] and y + hh > box[0]:
            gap = 0.015 * height
            if box[1] + gap + 2 * hh <= bottom:
                y = box[1] + gap + hh
            elif box[0] > 0.45 * height and box[0] - gap - 2 * hh >= 0.08 * height:
                y = box[0] - gap - hh  # visage bas dans l'image : au-dessus, il reste de la place
            else:
                # gros plan (visage plein cadre) : le plus bas possible dans la zone sûre — sur le menton ou le
                # cou au pire, jamais sur le front et les yeux (vu en vrai sur un punch-in de hook)
                y = bottom - hh
        out.append(dict(blk, toks=toks, kw=kw, x=(ml + width - mr) / 2, y=y, hh=hh, lines=lines,
                        widths=widths, space=space, em=em,
                        emoji=next((w["emoji"] for w in ws if w.get("emoji")), None), face=box))
    return out, size, ml, mr


def emoji_overlays(words, st, width, height, face_at=None):
    """Emojis à incruster (images) : un par bloc qui en porte, au-dessus du bloc (dessous s'il toucherait le visage)."""
    st = {**DEFAULT_ST, **(st or {})}
    blocks, _, _, _ = layout(words, st, width, height, face_at)
    px = round(3.0 * st["taille_pct_hauteur"] * height)  # ≈ 3 × hauteur des capitales (~170 px en 1080x1920)
    res = []
    for b in blocks:
        if not b["emoji"]:
            continue
        y = b["y"] - b["hh"] - 0.55 * px
        f = b["face"]
        core = (f[0], f[0] + 0.75 * (f[1] - f[0])) if f else None  # yeux → bouche : le menton peut être couvert
        if (core and y - px / 2 < core[1] and y + px / 2 > core[0]) or y - px / 2 < 0.05 * height:
            y = b["y"] + b["hh"] + 0.55 * px
        res.append({"emoji": b["emoji"], "start": b["start"], "end": b["end"], "x": round(b["x"]), "y": round(y),
                    "size": px})
    return res


def build_ass(words, st, width, height, titles=(), face_at=None):
    st = {**DEFAULT_ST, **(st or {})}
    blocks, size, ml, mr = layout(words, st, width, height, face_at)
    font, gras = font_name(st)
    bold = -1 if gras else 0
    outline = st["epaisseur_contour"] * height / 1920
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Mots,{font},{size},{ass_color(st['couleur'])},{ass_color(st['couleur_mot_actif'])},{ass_color(st['contour'])},&H80000000,{bold},0,0,0,100,100,0,0,1,{outline:.1f},{outline / 2:.1f},5,{ml},{mr},0,1
Style: Boite,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Titre,{font},{round(size * 1.35)},{ass_color(st['couleur'])},{ass_color(st['couleur_mot_actif'])},{ass_color(st['contour'])},&H80000000,{bold},0,0,0,100,100,0,0,1,{outline * 1.3:.1f},{outline / 2:.1f},5,{width // 14},{width // 14},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    pop = f"\\fscx80\\fscy80\\t(0,{POP_MS},\\fscx100\\fscy100)"
    active, normal, kwc = (f"&H{ass_color(st.get(k) or DEFAULT_ST[k])[4:]}&"
                           for k in ("couleur_mot_actif", "couleur", "couleur_mot_cle"))
    boxc = f"&H{ass_color(st.get('couleur_surlignage') or st['couleur_mot_actif'])[4:]}&"
    full = "\\fscx100\\fscy100"
    lines = []

    def ev(a, b, style, text):
        if b > a:
            lines.append(f"Dialogue: 1,{ass_time(a)},{ass_time(b)},{style},,0,0,0,,{text}")  # calque 0 = boîtes

    def scale_tags(scale, start_pct):
        s0 = round(scale * start_pct / 100)
        return f"\\fscx{s0}\\fscy{s0}" + (f"\\t(0,{POP_MS},\\fscx{scale}\\fscy{scale})" if s0 != scale else "")

    def line(blk, active_i, pop_pct, word_pop, entree="", cache_apres=None, actif=True):
        """Texte d'un événement : le mot-clé (couleur + 115 %) et le mot actif (karaoké) sont balisés, le reste
        hérite de l'état de la ligne (`pop_pct` < 100 → toute la ligne « pop »). `entree` remplace \\pos
        (glissé, fondu, rebond) ; `cache_apres` = index du dernier mot visible (mot par mot : la suite est
        transparente, la ligne ne bouge pas) ; `actif` False = mot actif sans couleur (surlignage : c'est la boîte)."""
        base = scale_tags(100, pop_pct) if pop_pct < 100 else ""
        out = []
        for j, t in enumerate(blk["toks"]):
            if cache_apres is not None and j == cache_apres + 1:
                out.append("{\\alpha&HFF&}" + esc(t))
            elif j == blk["kw"]:
                start = pop_pct if pop_pct < 100 else word_pop if j == active_i else 100
                out.append(f"{{\\c{kwc}{scale_tags(KW_SCALE, start)}}}{esc(t)}{{\\c{normal}{base or full}}}")
            elif j == active_i and actif:
                tags = scale_tags(100, word_pop) if word_pop < 100 and pop_pct == 100 else ""
                out.append(f"{{\\c{active}{tags}}}{esc(t)}{{\\c{normal}{full if tags else ''}}}")
            else:
                out.append(esc(t))
        pos = entree or "\\pos({:.0f},{:.0f})".format(blk["x"], blk["y"])  # pas de \ dans un f-string (Python < 3.12)
        return "{" + pos + base + "}" + " ".join(out)

    def entree(blk, anim):
        x, y = round(blk["x"]), round(blk["y"])
        if anim == "glisse":
            return f"\\move({x},{y + round(0.02 * height)},{x},{y},0,{ENTREE_MS})\\fad({ENTREE_MS},0)"
        if anim == "fondu":
            return f"\\pos({x},{y})\\fad({ENTREE_MS},0)"
        if anim == "rebond":
            return (f"\\pos({x},{y})\\fscx70\\fscy70\\t(0,90,\\fscx112\\fscy112)"
                    f"\\t(90,{ENTREE_MS + 50},\\fscx100\\fscy100)")
        return ""

    def boite(blk, i, a, b):
        """Surlignage : rectangle arrondi sous le mot i (calque 0, le texte est au calque 1)."""
        w, sp = blk["widths"], blk["space"]
        left = blk["x"] - (sum(w) + sp * (len(w) - 1)) / 2 + sum(w[:i]) + sp * i
        cap = st["taille_pct_hauteur"] * height  # capitales centrées sur y (mesuré au pixel) ; le contour noir
        pad, hb = 0.3 * cap + outline, 0.5 * cap + 0.3 * cap + outline  # du texte mange ~1 contour de marge
        x0, x1, y0, y1 = left - pad, left + w[i] + pad, blk["y"] - hb, blk["y"] + hb
        r = min(0.25 * cap, (y1 - y0) / 2)
        d = (f"m {x0 + r:.0f} {y0:.0f} l {x1 - r:.0f} {y0:.0f} b {x1:.0f} {y0:.0f} {x1:.0f} {y0:.0f} {x1:.0f} "
             f"{y0 + r:.0f} l {x1:.0f} {y1 - r:.0f} b {x1:.0f} {y1:.0f} {x1:.0f} {y1:.0f} {x1 - r:.0f} {y1:.0f} "
             f"l {x0 + r:.0f} {y1:.0f} b {x0:.0f} {y1:.0f} {x0:.0f} {y1:.0f} {x0:.0f} {y1 - r:.0f} "
             f"l {x0:.0f} {y0 + r:.0f} b {x0:.0f} {y0:.0f} {x0:.0f} {y0:.0f} {x0 + r:.0f} {y0:.0f}")
        if b > a:
            lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Boite,,0,0,0,,"
                         f"{{\\pos(0,0)\\an7\\bord0\\shad0\\c{boxc}\\p1}}{d}{{\\p0}}")

    anim = st["animation"] if st["animation"] in ANIMATIONS else "pop"  # valeur inconnue : le défaut du marché
    par_mot = anim in ("karaoke", "mot_par_mot", "surlignage")
    for blk in blocks:
        if par_mot:
            ws = blk["words"]
            box = anim == "surlignage" and blk["lines"] == 1  # sur 2 lignes la position est incertaine : karaoké
            for i, w in enumerate(ws):
                a = blk["start"] if i == 0 else w["start"]
                b = ws[i + 1]["start"] if i + 1 < len(ws) else blk["end"]
                if anim == "mot_par_mot":
                    ev(a, b, "Mots", line(blk, None, 80 if i == 0 else 100, 100, cache_apres=i))
                else:
                    if box:
                        boite(blk, i, a, b)
                    # pas de pop avec la boîte : elle est dessinée à 100 % et décrocherait du texte pendant 100 ms
                    ev(a, b, "Mots", line(blk, i, 80 if i == 0 and not box else 100, 88 if not box else 100,
                                          actif=not box))
        else:
            ev(blk["start"], blk["end"], "Mots",
               line(blk, None, 80 if anim == "pop" else 100, 100, entree=entree(blk, anim)))
    for t in titles:
        txt = esc(t["text"].upper() if st["majuscules"] else t["text"])
        ev(t["at"], t["at"] + t["duration"], "Titre", f"{{\\pos({width // 2},{round(0.2 * height)}){pop}}}{txt}")
    return head + "\n".join(lines) + "\n"


# ---------- SRT ----------

def build_srt(words, per_block=3):
    def t(x):
        ms = max(0, round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    out = [f"{i}\n{t(b['start'])} --> {t(b['end'])}\n{' '.join(w['w'] for w in b['words'])}\n"
           for i, b in enumerate(make_blocks(words, per_block), 1)]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Construit le fichier .ass des sous-titres depuis un edl.json.")
    ap.add_argument("edl")
    ap.add_argument("--profil", help="ma-da/profile.json")
    ap.add_argument("-o", "--sortie", default=".monteur/captions.ass")
    a = ap.parse_args()
    edl_path = Path(a.edl)
    if not edl_path.exists():
        die(f"edl introuvable : {edl_path}")
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    root = root_of(edl_path)
    prof_path = Path(a.profil) if a.profil else root / "ma-da" / "profile.json"
    st = json.loads(prof_path.read_text(encoding="utf-8")).get("sous_titres") if prof_path.exists() else None
    words = timeline_words(snapped_segments(edl, root), root)
    f = edl["format"]
    Path(a.sortie).write_text(build_ass(words, st, f["width"], f["height"], edl.get("titles") or []), encoding="utf-8")
    print(f"Sous-titres : {a.sortie} ({len(words)} mots)")


if __name__ == "__main__":
    main()
