"""Mots du transcript → timeline de sortie → sous-titres .ass (brûlés) / .srt (export).

Aussi le recalage des coupes sur les mots, partagé par render.py et export_xml.py
pour que vidéo, XML et SRT aient exactement les mêmes temps.
"""
import argparse
import math
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
        # morceaux de pacing (`piece`) : bords déjà posés sur les mots et l'énergie, parfois dans un mot (vraie pause)
        start, end = (s["start"], s["end"]) if "piece" in s else (snap(s["start"], words), snap(s["end"], words))
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
        tr = load_transcript(root, s["source"]) if not s.get("muet") else None  # segment muet : rien à sous-titrer
        keys = {norm(k) for k in s.get("mots_cles") or [] if norm(k)}
        # pacing découpe un segment en morceaux (`seg` = segment d'origine) : un seul emoji par segment d'origine
        group = groups.setdefault(s.get("seg", i), [s.get("emoji"), []])
        premier = True
        for w in (tr or {}).get("words", []):
            mid = (w["start"] + w["end"]) / 2
            # mot qui finit au tout début du segment (ou commence à sa toute fin) : à peine entendu, pas affiché
            if s["start"] <= mid < s["end"] and w["end"] > s["start"] + 0.03 and w["start"] < s["end"] - 0.03:
                a = s["offset"] + max(0.0, w["start"] - s["start"])
                b = s["offset"] + min(s["end"], w["end"]) - s["start"]
                res.append({"w": w["w"].strip(), "start": a, "end": max(b, a + 0.05)})
                if premier and s.get("seg", i) != segments[i - 1].get("seg", i - 1) if i else False:
                    res[-1]["coupe"] = True  # 1er mot après une vraie coupe (pas un morceau du même segment)
                premier = False
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
        virgule = bool(re.search(r"[,;:]$", text))
        text = re.sub(r"[,;:.…]+$", "", text)  # garde ? et ! qui portent le ton
        text = re.sub(r"\s+([?!])", r" \1", text)
        # « l' idée », « l 'hormone » (découpage Whisper), « abonne -toi »
        extra = {k: w[k] for k in ("cle", "emoji", "coupe") if w.get(k)}
        if out and (out[-1]["w"].endswith("'") or re.match(r"'|-\w", text)):
            prev = out.pop()
            w = dict(w, start=prev["start"])
            text = prev["w"] + text
            extra = {**{k: prev[k] for k in ("cle", "emoji") if prev.get(k)}, **extra}
        out.append({"w": text, "start": w["start"], "end": w["end"], "fin": fin, "virgule": virgule, **extra})
    return out


# mots outils : jamais seuls en fin de bloc (« je | pense », « de | la vie ») quand le bloc suivant peut les prendre
OUTILS = set("je tu il elle on nous vous ils elles en de du des le la les un une à au aux et ou qui que ne se ce "
             "sa son ses mon ma mes ton ta tes leur pour par dans sur avec mais donc car si y plus très me te lui leurs nos vos notre votre".split())


def _decoupe(ph, k, per_block, max_car):
    """Meilleure découpe de la phrase en k blocs (ou None) : blocs égaux, de préférence après une virgule, jamais un
    mot outil en fin de bloc ; chaque bloc ≤ per_block mots et ≤ max_car caractères (sauf mot seul)."""
    n, cible = len(ph), len(ph) / k
    best = {(0, 0): (0.0, [])}
    for j in range(1, k + 1):
        for i in range(j, n + 1):
            for d in range(j - 1, i):
                if (d, j - 1) not in best or i - d > per_block or                         (max_car and i - d > 1 and len(" ".join(w["w"] for w in ph[d:i])) > max_car):
                    continue
                last = ph[i - 1]
                c = best[(d, j - 1)][0] + (i - d - cible) ** 2
                if i < n:
                    c += 10 * (norm(last["w"]) in OUTILS) - 5 * last.get("virgule", False)
                if (i, j) not in best or c < best[(i, j)][0]:
                    best[(i, j)] = (c, best[(d, j - 1)][1] + [ph[d:i]])
    return best.get((n, k), (0, None))[1]


def make_blocks(words, per_block=3, max_car=None):
    """Blocs d'au plus `per_block` mots, jamais à travers une pause > 0,4 s, une fin de phrase ni une coupe qui
    ouvre une nouvelle phrase (majuscule au 1er mot d'un segment). Chaque phrase est répartie en blocs égaux
    (« Nul n'est prophète | en son pays », pas « Nul n'est prophète | en son | pays »), coupés de préférence après
    une virgule, jamais sur un mot outil seul en fin de bloc (_decoupe).
    `max_car` : aucun bloc plus long (espaces comprises) ; on ajoute des blocs, toujours égaux, jusqu'à tenir."""
    phrases, cur = [], []
    for w in clean_words(words):
        if cur and (w["start"] - cur[-1]["end"] > PAUSE_MAX or cur[-1]["fin"]
                    or (w.get("coupe") and w["w"][:1].isupper())):
            phrases.append(cur)
            cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    blocks = []
    for ph in phrases:
        k = math.ceil(len(ph) / per_block)
        while k < len(ph) and not _decoupe(ph, k, per_block, max_car):
            k += 1
        best = _decoupe(ph, k, per_block, max_car)
        # un bloc de plus plutôt qu'un mot outil en fin de bloc (la pénalité seule cède quand max_car serre)
        for k2 in range(k + 1, min(k + 2, len(ph)) + 1):
            if not any(norm(b[-1]["w"]) in OUTILS for b in best[:-1]):
                break
            alt = _decoupe(ph, k2, per_block, max_car)
            if alt and sum(norm(b[-1]["w"]) in OUTILS for b in alt[:-1]) < sum(norm(b[-1]["w"]) in OUTILS for b in best[:-1]):
                best = alt
        blocks += best
    res = []
    for i, b in enumerate(blocks):
        end = b[-1]["end"] + 0.25
        if i + 1 < len(blocks):
            nxt = blocks[i + 1][0]["start"]
            end = nxt if nxt - b[-1]["end"] < 0.5 else min(end, nxt)  # pas de clignotement entre blocs proches
        res.append({"start": b[0]["start"], "end": end, "words": b})
    return res


def dose_keywords(blocks, part):
    """Mots-clés colorés sur `part` des blocs, pas plus (la DA mesurée) : si Claude en a proposé trop, on garde en
    priorité les chiffres, puis des blocs répartis régulièrement. Jamais d'ajout (le choix des mots reste à Claude)."""
    with_kw = [i for i, b in enumerate(blocks) if any(w.get("cle") for w in b["words"])]
    want = round(part * len(blocks)) if part else None
    if want is None or len(with_kw) <= want * 1.1 + 0.5:
        return blocks
    chiffres = [i for i in with_kw if any(w.get("cle") and re.search(r"\d", w["w"]) for w in blocks[i]["words"])]
    keep = set(chiffres[:want])
    others = [i for i in with_kw if i not in keep]
    n = want - len(keep)
    if n > 0:
        keep |= {others[round(k * (len(others) - 1) / max(1, n - 1))] for k in range(n)} if n > 1 else {others[len(others) // 2]}
    out = []
    for i, b in enumerate(blocks):
        if i in with_kw and i not in keep:
            b = dict(b, words=[{k: v for k, v in w.items() if k != "cle"} for w in b["words"]])
        out.append(b)
    return out


# ---------- ASS ----------

def ass_color(hexa, alpha=0):
    h = hexa.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_time(t):
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def esc(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


# polices embarquées (OFL, voir fonts/OFL-*.txt) par (famille, graisse) → (nom ASS, fichier) : pas de faux gras
BUNDLED = {("montserrat", 800): ("Montserrat ExtraBold", "Montserrat-ExtraBold.ttf"),
           ("montserrat", 600): ("Montserrat SemiBold", "Montserrat-SemiBold.ttf"),
           ("inter tight", 600): ("Inter Tight SemiBold", "InterTight-SemiBold.ttf"),
           ("poppins", 800): ("Poppins ExtraBold", "Poppins-ExtraBold.ttf"),
           ("anton", 400): ("Anton", "Anton-Regular.ttf"), ("bebas neue", 400): ("Bebas Neue", "BebasNeue-Regular.ttf")}
ALIAS = {"inter": "inter tight"}


def bundled(police):
    """Graisses embarquées de la famille : {graisse: (nom ASS, fichier)} (vide si elle n'est pas embarquée)."""
    fam = ALIAS.get(police.lower(), police.lower())
    return {g: v for (f, g), v in BUNDLED.items() if f == fam}


def graisse(st):
    """`graisse` (400/600/800) du profil ; à défaut 800 si `gras` (le rendu d'avant), sinon 400."""
    try:
        return int(float(st.get("graisse")))
    except (TypeError, ValueError):
        return 800 if st.get("gras", True) else 400
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
    faces = bundled(want)
    if faces:  # la graisse embarquée la plus proche de celle demandée
        return faces[min(faces, key=lambda g: abs(g - graisse(st)))][0], False
    if _installed(want):
        return want, bool(st.get("gras", True))
    if (FONTS_DIR / "Montserrat-ExtraBold.ttf").exists():
        print(f"Police « {want} » absente : repli sur Montserrat ExtraBold (embarquée).")
        return "Montserrat ExtraBold", False
    return "Arial", bool(st.get("gras", True))


FONT_FILES = dict(BUNDLED.values())
KW_SCALE = 115   # mot-clé à 115 % (teardown : 110-120 %)
POP_MS = 100     # pop 80 → 100 % en 100 ms (teardown : 80-120 ms)
ENTREE_MS = 120  # glissé / fondu / rebond : 4 images à 30 i/s
ANIMATIONS = ("pop", "rebond", "glisse", "fondu", "flou", "karaoke", "mot_par_mot", "surlignage", "aucune")
LINE_H = 1.1     # hauteur d'une ligne libass ≈ 1,1 × Fontsize (mesuré sur les rendus)


def metrics(st, height):
    """(police ASS, fichier mesuré, Fontsize, em px, interlettrage px) : `interlettrage_pct` est en % de l'em,
    comme letter-spacing en CSS ; libass l'ajoute en px après chaque lettre (Spacing)."""
    font, _ = font_name(st)
    ttf = FONTS_DIR / FONT_FILES.get(font, "Montserrat-ExtraBold.ttf")  # police système : mesurée comme Montserrat
    cap_r, em_r = font_ratios(ttf) or (None, 0.64)
    if font not in FONT_FILES:
        cap_r = None
    size = round(st["taille_pct_hauteur"] * height / (cap_r or CAP_RATIO))
    em = size * em_r
    return font, ttf, size, em, float(st.get("interlettrage_pct") or 0) / 100 * em


def layout(words, st, width, height, face_at=None):
    """Blocs de sous-titres placés : x, y (centre, px), demi-hauteur, mot-clé (≤ 1 par bloc), emoji.
    `face_at(a, b)` → (haut, bas) du visage en px dans l'image de sortie entre a et b, ou None :
    un bloc qui couvrirait le visage passe dessous (dans la zone sûre), sinon dessus.
    Zone sûre (`zone_sure`) : rien sous 80 % de la hauteur, marge droite élargie (boutons TikTok/Reels)."""
    from PIL import ImageFont
    st = {**DEFAULT_ST, **(st or {})}
    font, ttf, size, em, ls = metrics(st, height)
    safe = bool(st.get("zone_sure"))
    ml = width // 12
    mr = round(width * 0.15) if safe else ml
    pil = ImageFont.truetype(str(ttf), max(1, round(em)))  # Pillow : taille = em ; libass : Fontsize = ligne « Win »
    bottom = (0.80 if safe else 0.94) * height
    y0 = st["position_y_pct"] * height
    out = []
    part = st.get("mots_cles_part_blocs")
    blocks = make_blocks(words, int(st["mots_par_bloc"]), st.get("max_caracteres"))
    if part and part < 0.85:  # au-delà : un mot actif par bloc (karaoké), pas un dosage de mots-clés
        blocks = dose_keywords(blocks, part)
    for blk in blocks:
        ws = blk["words"]
        toks = [w["w"].upper() if st["majuscules"] else w["w"] for w in ws]
        kw = next((i for i, w in enumerate(ws) if w.get("cle")), None)
        widths = [(pil.getlength(t) + ls * len(t)) * (KW_SCALE / 100 if i == kw else 1) for i, t in enumerate(toks)]
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


def clean_style(st):
    """Profil tel que Claude l'a rempli → valeurs utilisables : null = défaut, nombres écrits en texte convertis."""
    st = {**DEFAULT_ST, **{k: v for k, v in (st or {}).items() if v is not None}}
    for k in ("epaisseur_contour", "ombre", "taille_pct_hauteur", "position_y_pct", "ombre_flou", "interlettrage_pct",
              "max_caracteres"):
        if k in st and not isinstance(st[k], (int, float)):
            try:
                st[k] = float(str(st[k]).replace(",", ".").split()[0])
            except (ValueError, IndexError):  # « douce », « oui »… : description, pas une mesure
                st.pop(k) if k not in DEFAULT_ST else st.update({k: DEFAULT_ST[k]})
    return st


LUMA_CLAIRE = 0.6  # luma moyenne derrière un bloc au-delà de laquelle le blanc se lit mal


def mi(w):
    return (w["start"] + w["end"]) / 2


def build_ass(words, st, width, height, titles=(), face_at=None, caches=(), luma_at=None):
    """`caches` = intervalles (début, fin) d'inserts à texte : aucun sous-titre n'y déborde (le bloc commencé
    avant s'arrête à l'entrée de l'insert ; celui dont le mot finit après n'apparaît qu'à sa sortie).
    `luma_at(a, b, haut, bas)` → luma 0-1 de l'image derrière le bloc (parts de la hauteur) : sur image claire,
    l'ombre douce (`ombre_flou`) du bloc est renforcée."""
    st = clean_style(st)
    blocks, size, ml, mr = layout(words, st, width, height, face_at)
    for a, z in caches:
        # un bloc dont le 1er mot finit surtout après l'insert reste (« dans » dit pendant le fondu de sortie)
        blocks = [b for b in blocks if not (a - 0.02 <= b["start"] and mi(b["words"][0]) < z)]
        for b in blocks:
            if b["start"] < a < b["end"]:
                b["end"] = a
                b["words"] = [w for w in b["words"] if w["start"] < a]  # mot par mot / karaoké : rien après
            elif a - 0.02 <= b["start"] < z:  # mot gardé mais insert encore à l'écran (fondu compris) : après lui
                b["start"] = z
    font, gras = font_name(st)
    ls = metrics(st, height)[4]
    bold = -1 if gras else 0
    outline = st["epaisseur_contour"] * height / 1920
    flou = float(st.get("ombre_flou") or 0) * height / 1920  # ombre douce : calque 0 flouté, pas l'ombre ASS dure
    shadow = 0 if flou else st.get("ombre", st["epaisseur_contour"] / 2) * height / 1920
    for blk in blocks if flou and luma_at else ():
        blk["clair"] = (luma_at(blk["start"], blk["end"], (blk["y"] - blk["hh"]) / height,
                                (blk["y"] + blk["hh"]) / height) or 0) > LUMA_CLAIRE
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Mots,{font},{size},{ass_color(st['couleur'])},{ass_color(st['couleur_mot_actif'])},{ass_color(st['contour'])},&H80000000,{bold},0,0,0,100,100,{ls:.1f},0,1,{outline:.1f},{shadow:.1f},5,{ml},{mr},0,1
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

    def ev(a, b, style, text, clair=False):
        if b > a:
            lines.append(f"Dialogue: 1,{ass_time(a)},{ass_time(b)},{style},,0,0,0,,{text}")  # calque 0 = boîtes
            if flou:  # même texte en noir translucide, flouté, un peu plus bas, sous le texte net
                al, fl = ("20", flou * 1.5) if clair else ("50", flou)  # image claire : ombre plus dense et plus large
                lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},{style},,0,0,0,,"
                             f"{{\\bord0\\shad0\\1c&H000000&\\1a&H{al}&\\blur{fl:.1f}}}{ombre_douce(text)}")

    def ombre_douce(text):
        """Copie pour l'ombre : sans couleurs ni flou d'entrée (elle reste floue), décalée de 30 % du flou vers le bas."""
        dy = round(0.3 * flou)
        sh = re.sub(r"\\1?c&H[0-9A-Fa-f]+&|\\blur[\d.]+", "", text)
        return re.sub(r"\\(pos|move)\((\d+),(\d+)(?:,(\d+),(\d+))?",
                      lambda m: f"\\{m[1]}({m[2]},{int(m[3]) + dy}" + (f",{m[4]},{int(m[5]) + dy}" if m[4] else ""), sh)

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
        if anim == "flou":  # flou → net avec micro-zoom
            return f"\\pos({x},{y})\\blur6\\fscx105\\fscy105\\t(0,{ENTREE_MS},\\blur0\\fscx100\\fscy100)"
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
                    ev(a, b, "Mots", line(blk, None, 80 if i == 0 else 100, 100, cache_apres=i), blk.get("clair"))
                else:
                    if box:
                        boite(blk, i, a, b)
                    # pas de pop avec la boîte : elle est dessinée à 100 % et décrocherait du texte pendant 100 ms
                    ev(a, b, "Mots", line(blk, i, 80 if i == 0 and not box else 100, 88 if not box else 100,
                                          actif=not box), blk.get("clair"))
        else:
            ev(blk["start"], blk["end"], "Mots",
               line(blk, None, 80 if anim == "pop" else 100, 100, entree=entree(blk, anim)), blk.get("clair"))
    for t in titles:
        txt = esc(t["text"].upper() if st["majuscules"] else t["text"])
        a_, b_ = t["at"], t["at"] + t["duration"]
        if t.get("fond"):  # carton plein écran (question de l'interview…) : fond uni, texte centré
            fond = ass_color(t["fond"])[4:]
            # calque 5 : par-dessus les sous-titres (le carton les cache)
            lines.append(f"Dialogue: 5,{ass_time(a_)},{ass_time(b_)},Boite,,0,0,0,,{{\\an7\\pos(0,0)\\p1\\bord0\\shad0"
                         f"\\1c&H{fond}&}}m 0 0 l {width} 0 {width} {height} 0 {height}{{\\p0}}")
            y = height // 2
        else:
            # demi-hauteur du bloc titre (lignes estimées) : le titre entier reste dans le cadre
            fs = round(size * 1.35)
            plain = re.sub(r"[*{}]", "", t["text"])
            half = math.ceil(len(plain) * 0.62 * fs / (width * 12 / 14)) * fs * 1.2 / 2
            y = round(max(0.2 * height, 0.05 * height + half))
            f = face_at(a_, b_) if face_at else None
            if f and f[0] - half - 0.02 * height < y < f[1] + half + 0.02 * height:  # jamais sur le visage
                au_dessus = f[0] - 0.03 * height - half
                y = round(au_dessus) if au_dessus - half >= 0.05 * height else round(f[1] + 0.04 * height + half)
        mot = ass_color(t.get("couleur_mot") or st.get("couleur_mot_cle") or DEFAULT_ST["couleur_mot_cle"])[4:]
        txt = re.sub(r"\*(.+?)\*", lambda m: f"{{\\1c&H{mot}&\\fscx125\\fscy125}}{m.group(1)}{{\\r}}", txt)
        lines.append(f"Dialogue: 6,{ass_time(a_)},{ass_time(b_)},Titre,,0,0,0,,{{\\pos({width // 2},{y}){pop}}}{txt}")
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
