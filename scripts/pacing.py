"""Rythme « comme le créateur » : edl.json → edl.rendu.json (ce qui est vraiment rendu).

- bords de segments recalés sur les mots (pré-roll `coupe_avant_mot_s`, jamais de fin de mot coupée) ;
- silences internes compressés à la respiration du créateur (dans la phrase / en fin de phrase) ;
- hésitations (« euh », mots `filler`) retirées si elles sont isolées par des pauses ;
- raccords entre segments faits de vrai silence (pas zéro) ;
- b-roll calé sur son mot, borné à sa phrase ; b-roll / titres / bruitages recalés sur les mêmes mots.
Appelé en premier par render.py : `normalize_edl(edl, root, profile)`.
"""
import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, find_transcript  # noqa: E402

DEFAULTS = {"respiration_intra_s": 0.18, "respiration_phrases_s": 0.38, "coupe_avant_mot_s": 0.08}
FILLERS = {"euh", "euhh", "heu", "e", "eh", "hum", "hm", "hmm", "mh", "mm", "mmh", "bah", "ben", "hein"}  # « e » : « euh » vu par Parakeet
SENTENCE_END = re.compile(r"[.!?…]['\"»)]*$")
TAIL_MIN = 0.06      # traîne mini après le dernier mot gardé ; l'énergie du son (Envelope) élargit si le mot finit plus tard
PRE_MIN = 0.03
RACCORD_ZOOM = 1.12  # masquer_raccords : échelle alternée à chaque raccord interne
MIN_CUT = 0.10       # ne pas créer un raccord pour gagner moins de 3 images
FILLER_PAUSE = 0.10  # une hésitation ne se coupe proprement qu'entourée de pauses ≥ 100 ms
SILENCE_MIN = 0.06   # pause réelle (énergie) la plus courte qu'on entend
JUMP_FRAMES = 3      # un jump cut retire au moins 3 images, sinon le saut passe pour un accroc
BROLL_EARLY_FRAMES = 2
BROLL_MIN = 0.4  # un plan d'illustration plus court ne se perçoit pas
TENIR_MIN = 1.2  # un insert à lire (logo, citation) sous 1,2 s ne se lit pas
BROLL_TAIL = 0.15
KEEP_NOTE = re.compile(r"gard|keep|euh", re.I)


def _rythme(profile):
    r = dict(DEFAULTS)
    ry = (profile or {}).get("rythme") or {}
    r.update({k: v for k, v in ry.items() if k in DEFAULTS and v is not None})
    moy = ry.get("duree_moyenne_plan_s")
    # au-delà, un morceau est recoupé en jump cut (rythme des plans du créateur) ; sans profil : jamais
    r["plan_max_s"] = ry.get("plan_max_s") or (round(1.6 * moy, 3) if moy else None)
    return r


def _norm(text):
    return re.sub(r"[^\w']", "", text.replace("’", "'").lower())


def is_filler(w):
    return bool(w.get("filler")) or _norm(w["w"]) in FILLERS


def sentence_end(w):
    return bool(SENTENCE_END.search(w["w"].strip()))


def _pads(allowed, pre):
    """Respiration `allowed` partagée entre traîne après le mot (≥ TAIL_MIN) et pré-roll avant le suivant."""
    pre = min(pre, max(PRE_MIN, allowed - TAIL_MIN))
    return max(TAIL_MIN, allowed - pre), pre


class Envelope:
    """Niveau du son par tranches de 10 ms d'un média (dB) : la transcription date les mots à ~80 ms près
    (grille Parakeet) ; l'énergie dit où la voix commence et s'arrête vraiment. Sert uniquement à ÉLARGIR une coupe
    qui mangerait une attaque ou une fin de mot — jamais à la resserrer."""
    STEP = 0.01

    def __init__(self, path):
        import subprocess
        import numpy as np
        from common import ffmpeg
        raw = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le",
                              "-"], capture_output=True).stdout
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
        n = len(x) // 160
        if n < 10:
            raise ValueError("pas de son")
        rms = np.sqrt((x[:n * 160].reshape(n, 160) ** 2).mean(axis=1)) + 1e-6
        self.db = 20 * np.log10(rms)
        self.np = np

    def _floor(self, t):
        i0, i1 = max(0, int((t - 1.5) / self.STEP)), int((t + 1.5) / self.STEP)
        seg = self.db[i0:i1]
        return float(self.np.percentile(seg, 10)) if len(seg) else -90.0

    def onset(self, t_word, lo):
        """Début réel de la voix d'un mot daté `t_word` : dernière tranche calme avant le mot (≥ `lo`)."""
        thr = self._floor(t_word) + 9
        i = int((t_word + 0.04) / self.STEP)
        i_lo = max(int(lo / self.STEP), int((t_word - 0.08) / self.STEP), 0)  # au-delà : souffle, pas l'attaque
        while i > i_lo and i - 1 < len(self.db) and self.db[i - 1] > thr:
            i -= 1
        return i * self.STEP

    def offset(self, t_word, hi):
        """Fin réelle de la voix d'un mot fini à `t_word` : première tranche calme après (≤ `hi`)."""
        thr = self._floor(t_word) + 9
        i = int((t_word - 0.04) / self.STEP)
        hi = min(hi, t_word + 0.10)  # hi peut être infini (dernier mot du fichier) ; au-delà : souffle, bruit
        i_hi = min(int(hi / self.STEP), len(self.db))
        while i < i_hi and self.db[i] > thr:
            i += 1
        return i * self.STEP

    def silences(self, a, b):
        """Vraies pauses dans [a, b] : plages sous plancher + 9 dB d'au moins SILENCE_MIN. Parakeet colle les mots
        (écarts ~0,02 s) et range les pauses dans les mots : seule l'énergie les voit."""
        t = (a + b) / 2
        thr = self._floor(t) + 9
        around = self.db[max(0, int((t - 1.5) / self.STEP)):int((t + 1.5) / self.STEP)]
        if not len(around) or float(self.np.percentile(around, 90)) < thr + 6:
            return []  # niveau plat (musique, bruit, son de test) : aucune voix nette au-dessus du fond
        i0, i1 = max(0, int(round(a / self.STEP))), min(int(round(b / self.STEP)), len(self.db))
        out, k = [], None
        for i in range(i0, i1 + 1):
            calm = i < i1 and self.db[i] <= thr
            if calm and k is None:
                k = i
            elif not calm and k is not None:
                if (i - k) * self.STEP >= SILENCE_MIN - 1e-9:
                    out.append((k * self.STEP, i * self.STEP))
                k = None
        return out

    def creux(self, a, b):
        """Instant le plus calme de [a, b] s'il est net (≥ 6 dB sous le niveau en b), sinon b."""
        i0, i1 = max(0, int(round(a / self.STEP))), min(int(round(b / self.STEP)), len(self.db))
        if i1 <= i0:
            return b
        i = i0 + int(self.np.argmin(self.db[i0:i1]))
        return i * self.STEP if self.db[i] <= self.level(b) - 6 else b

    def level(self, t):
        i = int(t / self.STEP)
        return float(self.db[max(0, i - 2):i + 3].mean()) if i < len(self.db) else -90.0


def _longest(sils):
    return max(sils, key=lambda s: s[1] - s[0], default=None)


def _seg_words(seg, words):
    """Mots du segment (milieu dans [start, end[, même règle que captions.timeline_words)."""
    return [w for w in words if seg["start"] <= (w["start"] + w["end"]) / 2 < seg["end"]]


def _cut_plan(seg, words, r, last_seg, env=None, prec=None):
    """Morceaux (start, end, mots, limite dure) d'un segment : fillers isolés retirés, silences compressés.
    Les bords externes ne sont posés qu'ici en pré-roll / traîne ; render arrondit à l'image ensuite.
    `prec` = segment d'avant dans l'edl (son mot gardé n'est jamais repris ici)."""
    inside = _seg_words(seg, words)
    if not inside:
        return [(seg["start"], seg["end"], [], math.inf)], 0
    keep_fillers = bool(KEEP_NOTE.search(str(seg.get("note") or "")))
    kept, dropped = [], 0
    for k, w in enumerate(inside):
        if not keep_fillers and is_filler(w):
            prev = inside[k - 1]["end"] if k else None
            nxt = inside[k + 1]["start"] if k + 1 < len(inside) else None
            if (prev is None or w["start"] - prev >= FILLER_PAUSE) and (nxt is None or nxt - w["end"] >= FILLER_PAUSE):
                dropped += 1
                continue
        kept.append(w)
    if not kept:  # segment fait d'une seule hésitation : on ne rend rien
        return [], dropped
    pre = r["coupe_avant_mot_s"]
    i0 = words.index(kept[0])
    i1 = words.index(kept[-1])
    prev_end = words[i0 - 1]["end"] if i0 > 0 else 0.0
    next_start = words[i1 + 1]["start"] if i1 + 1 < len(words) else math.inf
    allowed_end = r["respiration_phrases_s"] if (sentence_end(kept[-1]) or last_seg) else r["respiration_intra_s"]
    tail_end, _ = _pads(allowed_end, pre)
    # raccord entre segments : la traîne du précédent + ce pré-roll = la respiration entre phrases (pas traîne + pré-roll entier)
    start = max(prev_end, kept[0]["start"] - _pads(r["respiration_phrases_s"], pre)[1], 0.0)
    # traîne : silence réel seulement (jamais sur le mot suivant non gardé), au moins jusqu'à la fin du mot
    end = max(kept[-1]["end"], min(kept[-1]["end"] + tail_end, next_start - 0.01))
    pieces, cur_start, cur = [], start, []
    fixed_start = set()  # morceaux dont le début est posé sur l'énergie : ne pas l'élargir
    for a, b in zip(kept, kept[1:] + [None]):
        cur.append(a)
        if b is None:
            break
        gap = b["start"] - a["end"]
        allowed = r["respiration_phrases_s"] if sentence_end(a) else r["respiration_intra_s"]
        removed = [w for w in inside if a["end"] <= w["start"] and w["end"] <= b["start"]]  # hésitation coupée entre a et b
        sil = _longest(env.silences(max(a["start"], cur_start) + 0.05, b["end"] - 0.05)) if env and not removed else None
        if sil and sil[1] - sil[0] - allowed >= MIN_CUT:
            tail, p = _pads(allowed, pre)
            # son entre les mots (souffle, bruit) : mêmes plafonds qu'Envelope, 0,10 s après a / 0,08 s avant b ;
            # un son dans l'étendue d'un mot est ce mot : gardé
            s0 = min(sil[0], a["end"] + 0.10) if sil[0] <= b["start"] else sil[0]
            s1 = max(sil[1], b["start"] - 0.08) if sil[1] >= a["end"] else sil[1]
            # milieu de chaque mot gardé du bon côté : captions/render rangent un mot au morceau de son milieu
            x = max(s0 + tail, (a["start"] + a["end"]) / 2 + 0.01)
            y = min(s1 - p, (b["start"] + b["end"]) / 2)
            if y - x >= MIN_CUT:
                pieces.append((cur_start, x, cur, x))
                cur_start, cur = y, []
                fixed_start.add(len(pieces))
                continue
        if removed or gap - allowed >= MIN_CUT:
            tail, p = _pads(allowed, pre)
            hi = removed[0]["start"] if removed else b["start"] - p
            lo = removed[-1]["end"] if removed else a["end"] + tail
            pieces.append((cur_start, min(a["end"] + tail, hi), cur, hi))
            cur_start, cur = max(b["start"] - p, lo), []
    pieces.append((cur_start, end, cur, next_start))
    if env is not None:  # élargir les coupes qui mangeraient une attaque / une fin de mot (jamais les resserrer)
        fixed = []
        for k, (a, b, pw, hard) in enumerate(pieces):
            if pw:
                prev_lim = fixed[-1][1] if fixed else prev_end
                i_first = words.index(pw[0])
                lo = max(prev_lim, words[i_first - 1]["end"] if i_first else 0.0)
                if k not in fixed_start:
                    a = min(a, max(lo, env.onset(pw[0]["start"], lo) - 0.02))
                pv = words[i_first - 1] if i_first else None
                if k == 0 and pv and pw[0]["start"] - pv["end"] < 0.03 and not (
                        prec and prec["source"] == seg["source"] and prec["start"] <= (pv["start"] + pv["end"]) / 2 < prec["end"]):
                    # mot d'avant (non gardé) collé : la transcription date mal la frontière (« oui, dans » → on
                    # entendait « …ans ») ; la vraie attaque est au creux d'énergie juste avant
                    a = min(a, env.creux(max(pw[0]["start"] - 0.10, (pv["start"] + pv["end"]) / 2), pw[0]["start"]))
                i_last = words.index(pw[-1])
                nxt = words[i_last + 1]["start"] if i_last + 1 < len(words) else math.inf
                lim = min(hard, nxt)
                b = max(b, min(env.offset(pw[-1]["end"], lim) + 0.02, lim - 0.01))
            fixed.append((a, b, pw, hard))
        pieces = fixed
    return pieces, dropped


def _jump_cuts(pieces, r, fps, env, sans_retrait=False):
    """Jump cuts : un morceau plus long que `plan_max_s` est recoupé (même cadre) à sa plus longue vraie pause,
    sinon à la frontière de mots la plus calme ; au moins JUMP_FRAMES images retirées pour que le saut se voie.
    `sans_retrait` (changement d'échelle à chaque coupe) : si aucune coupe ne retire rien sans toucher la voix,
    coupe quand même à la frontière de mots la plus proche du milieu, sans rien retirer (le cadre change d'échelle).
    Renvoie (morceaux, indices des morceaux qui commencent après un jump cut)."""
    plan_max, out, jumps = r["plan_max_s"], [], set()
    todo = [(*p, False) for p in pieces]
    rem, side = JUMP_FRAMES / fps, 0.3 * plan_max
    while todo:
        a, b, pw, hard, jump = todo.pop(0)
        best = None
        for k in range(1, len(pw) if b - a > plan_max else 0):
            p, n = pw[k - 1], pw[k]
            sil = _longest(env.silences(max(p["start"], a) + 0.05, min(n["end"], b) - 0.05)) if env else None
            if sil:
                c, score = (sil[0] + sil[1]) / 2, (1, sil[1] - sil[0])
                d = max(rem, sil[1] - sil[0] - r["respiration_intra_s"])
            else:  # parole continue : la frontière la plus basse en énergie (sans son : le plus grand écart)
                c = n["start"] if env else (p["end"] + n["start"]) / 2
                score, d = (0, -env.level(c) if env else n["start"] - p["end"]), rem
            x, y = c - d / 2, c + d / 2
            ok = (p["start"] + p["end"]) / 2 < x and y <= (n["start"] + n["end"]) / 2 and x - a >= side and b - y >= side
            if ok and env is not None:  # jamais de voix retirée : la partie enlevée reste sous plancher + 20 dB
                i, j = round(x / env.STEP), round(y / env.STEP)
                ok = float(env.db[i:max(j, i + 1)].max()) <= env._floor(c) + 20
            if ok and (best is None or score > best[0]):
                best = (score, x, y, k)
        if best is None and sans_retrait and b - a > plan_max:
            mid, cands = (a + b) / 2, []
            for k in range(1, len(pw)):
                p, n = pw[k - 1], pw[k]
                x = a + round(((p["end"] + n["start"]) / 2 - a) * fps) / fps  # sur la grille d'images : rien de perdu
                if (p["start"] + p["end"]) / 2 < x <= (n["start"] + n["end"]) / 2 and x - a >= side and b - x >= side:
                    cands.append((abs(x - mid), x, k))
            if cands:
                _, x, k = min(cands)
                todo[0:0] = [(a, x, pw[:k], x, jump), (x, b, pw[k:], hard, True)]
                continue
        if best is None:
            if jump:
                jumps.add(len(out))
            out.append((a, b, pw, hard))
            continue
        _, x, y, k = best
        todo[0:0] = [(a, x, pw[:k], x, jump), (y, b, pw[k:], hard, True)]
    return out, jumps


def _sentence_words(timeline):
    """Indice de la fin de phrase pour chaque mot de la timeline finale."""
    ends, j = [0] * len(timeline), len(timeline) - 1
    for k in range(len(timeline) - 1, -1, -1):
        if sentence_end(timeline[k]) and k < len(timeline) - 1:
            j = k
        ends[k] = j
    return ends


def normalize_edl(edl, root, profile):
    """edl (plan de Claude) → edl rendu. Écrit `.monteur/edl.rendu.json` et renvoie le dict.
    Segments sortis : mêmes champs + `start`/`end` recalés, `seg` (indice du segment d'origine), `piece` (0, 1…),
    durées multiples exactes de 1/fps. b-roll / titres / sfx en temps final, recalés sur les mêmes mots."""
    root = Path(root)
    fmt = edl.get("format") or (profile or {}).get("format") or {"fps": 30}
    fps = fmt.get("fps", 30)
    r = _rythme(profile)
    zp = (profile or {}).get("zooms") or {}
    # DA qui change d'échelle à chaque coupe dans une même prise (profil appris) ; l'edl peut trancher
    masquer = edl.get("masquer_raccords", bool(zp.get("coupes_echelle")))
    z_raccord = zp.get("intensite") if (zp.get("intensite") or 0) > 1.05 else RACCORD_ZOOM
    cache = {}

    def words_of(src):
        if src not in cache:
            t = find_transcript(root, src)
            cache[src] = sorted((t or {}).get("words", []), key=lambda w: w["start"])
        return cache[src]

    envs = {}

    def env_of(src):
        if src not in envs:
            try:
                envs[src] = Envelope(root / src) if (root / src).is_file() else None
            except Exception:  # noqa: BLE001 — pas de son lisible : coupes à la transcription seule
                envs[src] = None
        return envs[src]

    segs_in = edl.get("segments") or []
    out_segs, old_off, timeline = [], [], []  # timeline : mots gardés en temps final
    off_in, off_out, dropped = 0.0, 0.0, 0
    plan = []  # par segment d'origine : liste de (src_start, src_end, out_offset)
    for i, s in enumerate(segs_in):
        old_off.append(off_in)
        off_in += max(0.0, s["end"] - s["start"])
        ws = words_of(s["source"])
        pieces, d = _cut_plan(s, ws, r, i == len(segs_in) - 1, env_of(s["source"]), segs_in[i - 1] if i else None)
        jumps = set()
        if r["plan_max_s"] and edl.get("mode") != "voix_off" and not s.get("muet"):  # voix off / muet : rien à voir
            pieces, jumps = _jump_cuts(pieces, r, fps, env_of(s["source"]), sans_retrait=masquer)
        dropped += d
        plan.append([])
        doux = s.get("zoom_type") == "doux"
        frames_of = []
        for a, b, pw, hard in pieces:
            frames = max(1, math.ceil((b - a) * fps - 1e-6))
            if a + frames / fps > hard:  # l'image entamée déborderait sur le mot suivant non gardé
                frames = max(1, math.floor((b - a) * fps + 1e-6))
            frames_of.append(frames)
        done, kept_total = 0, sum(frames_of)
        for k, ((a, b, pw, hard), frames) in enumerate(zip(pieces, frames_of)):
            piece = dict(s, start=round(a, 6), end=round(a + frames / fps, 6), seg=i, piece=k)
            if doux and s.get("zoom", 1.0) > 1.0:
                # poussée lente continue à travers les raccords internes : échelle de début/fin de ce morceau
                z = s["zoom"] - 1.0
                piece["zoom_from"] = round(1.0 + z * done / kept_total, 5)
                piece["zoom_to"] = round(1.0 + z * (done + frames) / kept_total, 5)
            elif masquer and out_segs and (k or ("zoom" not in s and out_segs[-1]["source"] == s["source"])):
                # raccord dans la même prise (pause retirée, jump cut, segment suivant du même rush) : la tête saute ;
                # changer d'échelle à chaque raccord le rend voulu (coupe sèche, pas un zoom animé : « raccord »)
                prev = out_segs[-1].get("zoom") or 1.0
                piece["zoom"] = 1.0 if prev > 1.01 else z_raccord
                piece["zoom_type"] = "aucun" if prev > 1.01 else "raccord"
            elif k in jumps and out_segs and (out_segs[-1].get("zoom") or 1.0) > 1.0 \
                    and out_segs[-1].get("zoom_type") != "aucun":
                # jump cut : même échelle que le plan d'avant (la tête saute, le cadre ne bouge pas, pas de zoom ajouté)
                piece["zoom"], piece["zoom_type"] = out_segs[-1]["zoom"], "cut"
            elif k and "zoom" in piece:  # un punch-in ne vaut que pour le premier morceau
                piece["zoom"] = 1.0
                if "zoom_type" in piece:
                    piece["zoom_type"] = "aucun"
            if k:
                piece.pop("emoji", None)  # un seul emoji par segment d'origine
            if s.get("mots_cles") and pw:  # sans transcript, on garde tel quel
                said = {_norm(w["w"]) for w in pw}
                mk = [m for m in s["mots_cles"] if _norm(m) in said]
                if mk:
                    piece["mots_cles"] = mk
                else:
                    piece.pop("mots_cles")
            done += frames
            out_segs.append(piece)
            plan[i].append((piece["start"], piece["end"], off_out))
            for w in pw:
                timeline.append(dict(w, start=off_out + max(w["start"], piece["start"]) - piece["start"],
                                     end=off_out + min(w["end"], piece["end"]) - piece["start"], seg=i))
            off_out += frames / fps
    total = off_out
    if zp.get("fin") and len(out_segs) >= 2 and out_segs[-1]["source"] == out_segs[-2]["source"]:
        # la DA finit sur un plan plus large : dernier morceau à l'échelle de base, celui d'avant serré
        out_segs[-1].update(zoom=1.0, zoom_type="aucun")
        if (out_segs[-2].get("zoom") or 1.0) <= 1.01:
            out_segs[-2].update(zoom=z_raccord, zoom_type="raccord")

    def remap(t):
        """Temps final de l'ancien plan → temps final rendu (même instant du rush)."""
        if not segs_in:
            return t
        i = max((k for k in range(len(segs_in)) if old_off[k] <= t), default=0)
        src_t = segs_in[i]["start"] + t - old_off[i]
        ps = plan[i] or next((plan[k] for k in range(i + 1, len(plan)) if plan[k]), [])
        for a, b, o in ps:
            if src_t < a:
                return o
            if src_t < b:
                return o + src_t - a
        return (ps[-1][2] + ps[-1][1] - ps[-1][0]) if ps else total

    def src_to_final(src, t):
        """Instant `t` du fichier `src` (la voix off) → temps final rendu ; partie coupée → morceau suivant."""
        best = None
        for i, sg in enumerate(segs_in):
            if Path(sg["source"]).as_posix() != Path(src).as_posix():
                continue
            for a, b, o in plan[i]:
                if a <= t < b:
                    return o + t - a
                if t < a and (best is None or o < best):
                    best = o
        return best if best is not None else total

    if edl.get("mode") == "voix_off":
        res = dict(edl, segments=out_segs, broll=_voix_off_plans(edl, root, timeline, total, fps, src_to_final, remap))
        for key in ("titles", "sfx", "transitions"):
            if edl.get(key):
                res[key] = [dict(x, at=round(remap(x["at"]), 3)) for x in edl[key]]
        res["pacing"] = {"duree_avant_s": round(off_in, 3), "duree_apres_s": round(total, 3),
                         "segments_avant": len(segs_in), "morceaux": len(out_segs), "hesitations_coupees": dropped,
                         "rythme": r, "mode": "voix_off"}
        wd = root / ".monteur"
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "edl.rendu.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        return res

    sent_end = _sentence_words(timeline)
    protected = [(p[2], p[2] + p[1] - p[0]) for i, s in enumerate(segs_in) if re.search(r"hook|cta", str(s.get("note") or ""), re.I)
                 for p in plan[i]]
    hook_fin = max([p[2] + p[1] - p[0] for i, s in enumerate(segs_in) if re.search(r"hook", str(s.get("note") or ""), re.I)
                    for p in plan[i]] or [0.0])
    bcfg = (profile or {}).get("broll") or {}
    moy = bcfg.get("duree_moy_s")
    lo, hi = (moy * 0.5, moy * 1.5) if moy else (1.2, 4.0)
    brolls = []
    index = load_plans(root)
    prev_edl = None  # (fin dans la timeline de l'edl, gardé ?) du b-roll d'avant : plans enchaînés

    def apres_zone(t, mot_t):
        """Jamais avant la fin d'une zone protégée (hook/CTA) qui finit juste avant le mot."""
        return max([t] + [pe for ps, pe in protected if pe <= mot_t + 1e-6])

    for b in edl.get("broll") or []:
        suit = bool(prev_edl and prev_edl[1] and not b.get("mot") and abs(b["at"] - prev_edl[0]) < 0.05)
        prev_edl = (b["at"] + b.get("duration", 0.0), False)
        fin_plan, fin_phrase = None, math.inf
        if b.get("plan"):  # plan choisi dans plans.md (ses clips découpés et décrits) : source + bornes du plan
            pl = index.get(str(b["plan"]).upper())
            if not pl:
                print(f"plan {b['plan']} absent de .monteur/plans.json : ignoré")
                continue
            b = dict(b, source=b.get("source") or pl["source"],
                     source_start=round(pl["start"] + float(b.get("decalage", 0.0)), 3))
            fin_plan = pl["end"]
        at = remap(b["at"])
        if b.get("carton"):  # carton d'ouverture animé (anim.py question) : à son temps exact, fait pour couvrir le hook
            brolls.append(dict(b, at=round(at, 3), duration=round(min(b["duration"], total - at), 3)))
            continue
        k = _anchor(timeline, at, b.get("mot"))
        if k is not None:
            w = timeline[k]
            start = apres_zone(max(0.0, w["start"] - BROLL_EARLY_FRAMES / fps), w["start"])  # sur le mot, jamais en retard
            last = sent_end[k]
            limit = timeline[last + 1]["start"] if last + 1 < len(timeline) else total
            end = min(timeline[last]["end"] + BROLL_TAIL, limit)
            dur = min(max(end - start, lo), hi)
            dur = min(dur, limit - start, total - start)  # jamais dans la phrase suivante
            if b.get("tenir") or b.get("deborde"):  # insert à lire (carte, vignette) ou débord voulu (phrase
                dur = min(b.get("duration", dur), total - start)  # suivante sur le même sujet) : sa durée
            else:  # fin de sa phrase (+ la respiration avant la suivante), plans enchaînés après lui compris
                fin_phrase = limit
        else:
            # durée donnée : la sienne (plancher technique BROLL_MIN) ; sinon celle de la DA
            start = at
            dur = min(max(b["duration"], BROLL_MIN) if "duration" in b else lo, hi, total - at)
        if dur <= 0:
            continue
        if fin_plan is not None:  # jamais au-delà de la fin du plan (on verrait la coupe suivante du clip)
            dur = min(dur, fin_plan - b["source_start"])
        # raccourci pour s'arrêter avant le hook/CTA plutôt que perdu
        avant = min([dur] + [ps - start for ps, pe in protected if ps > start + 0.05])
        zones = protected
        if b.get("tenir") and avant < TENIR_MIN and start >= hook_fin:
            # insert à lire (logo, citation) juste avant le CTA : garde 1,2 s quitte à mordre le début du CTA,
            # comme un monteur (logo puis « donc voilà… ») ; jamais sur le hook
            dur = min(b.get("duration", TENIR_MIN), TENIR_MIN, total - start)
            zones = [(ps, pe) for ps, pe in protected if pe <= hook_fin]
        else:
            dur = avant
        if bcfg.get("jamais_sur_hook", True) and any(start < pe and start + dur > ps for ps, pe in zones):
            print(f"b-roll {b.get('source')} retiré : tombe sur le hook ou le CTA (déplace-le plus tôt)")
            continue
        if dur < BROLL_MIN:  # enchaîner des plans de 0,4-0,7 s est un effet de monteur ; en dessous on ne voit rien
            print(f"b-roll {b.get('source')} retiré : {dur:.2f} s à l'écran (< {BROLL_MIN} s ; plan ou place trop court)")
            continue
        dispo = (fin_plan if fin_plan is not None else _duree_source(root, b)) - b.get("source_start", 0.0)
        brolls.append(dict(b, at=round(start, 3), duration=round(dur, 3), _suit=suit, _dispo=dispo, _fin=fin_phrase,
                           _mot_t=timeline[k]["start"] if k is not None else start))
        prev_edl = (prev_edl[0], True)
    mus = edl.get("music") or {}
    if brolls and mus.get("source") and (root / mus["source"]).exists():  # entrées de b-roll sur les temps de la musique
        import audio
        temps = audio.beats(root / mus["source"], total + 1, float(mus.get("debut_s", 0)))
        for b in brolls:
            if not b.get("carton") and not b.get("_suit"):
                t = apres_zone(audio.cale_sur_temps(b["at"], temps), b.get("_mot_t", b["at"]))
                b["duration"], b["at"] = round(b["duration"] + b["at"] - t, 3), round(t, 3)
    fin_prec = math.inf
    for k, b in enumerate(brolls):
        fin = b.pop("_fin", math.inf)
        if b.pop("_suit", False) and k:  # plan enchaîné sans mot : part à la fin réelle du précédent (après calage)
            b["at"] = round(brolls[k - 1]["at"] + brolls[k - 1]["duration"], 3)
            fin = fin_prec if not b.get("deborde") else math.inf  # il illustre la phrase du plan qu'il suit
        fin_prec = fin
        # jamais plus long que ce que le plan (ou le fichier) a d'images, ni au-delà de la fin (de sa phrase)
        b["duration"] = round(max(0.0, min(b["duration"], b.pop("_dispo", math.inf), total - b["at"], fin - b["at"])), 3)
        b.pop("_mot_t", None)
    for b in brolls:
        if b["duration"] < BROLL_MIN and not b.get("carton"):
            print(f"b-roll {b.get('source')} retiré : {b['duration']:.2f} s avant la fin de sa phrase (< {BROLL_MIN} s)")
    brolls = [b for b in brolls if b["duration"] >= BROLL_MIN or b.get("carton")]
    res = dict(edl, segments=out_segs, broll=brolls)
    for key in ("titles", "sfx", "transitions"):
        if edl.get(key):
            res[key] = []
            for x in edl[key]:
                at = remap(x["at"])
                y = dict(x, at=round(at, 3))
                if "duration" in x:
                    y["duration"] = round(max(0.0, min(x["duration"], total - at)), 3)
                res[key].append(y)
    # carton d'ouverture : sonorisé comme celui de la référence (appris), sauf si l'edl y pose déjà ses bruitages
    appris = ((profile or {}).get("audio") or {}).get("sfx_carton")
    carton = next((b for b in brolls if b.get("carton")), None)
    if appris and carton and not any(x["at"] < carton["at"] + carton["duration"] for x in res.get("sfx") or []):
        import audio
        res["sfx"] = audio.carton_sfx(carton, appris, fps) + (res.get("sfx") or [])
    res["pacing"] = {"duree_avant_s": round(off_in, 3), "duree_apres_s": round(total, 3),
                     "segments_avant": len(segs_in), "morceaux": len(out_segs), "hesitations_coupees": dropped,
                     "rythme": r}
    wd = root / ".monteur"
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "edl.rendu.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def _duree_source(root, b):
    """Durée du fichier du b-roll (inf si inconnue : photo, fichier absent)."""
    f = Path(root) / b.get("source", "")
    if not f.is_file() or f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        return math.inf
    from common import probe
    return probe(f).get("duration") or math.inf


def load_plans(root):
    """Index des plans des rushs (scripts/plans.py) : {"P12": {"source", "start", "end", ...}} ou {}."""
    f = Path(root) / ".monteur" / "plans.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("plans", {})
    except (OSError, ValueError):
        return {}


def _voix_off_plans(edl, root, timeline, total, fps, src_to_final, remap):
    """Mode voix off : les plans des rushs couvrent TOUTE la vidéo, bout à bout. Chaque plan commence sur son
    mot (`voix_t` = instant dans le fichier de la voix, + `mot`), finit au début du suivant ; le premier part de 0.
    `plan` (ex. "P12") = plan de l'index plans.json : source et bornes du plan ; on n'en déborde jamais si
    possible (sinon la dernière image est tenue)."""
    index = load_plans(root)
    voix = (edl.get("segments") or [{}])[0].get("source")
    items = []
    for b in edl.get("broll") or []:
        b = dict(b)
        pl = index.get(str(b.get("plan", "")).upper()) if b.get("plan") else None
        if b.get("plan") and not pl:
            print(f"plan {b['plan']} absent de .monteur/plans.json : ignoré")
            continue
        if pl:
            b.setdefault("source", pl["source"])
            b["source_start"] = pl["start"] + float(b.get("decalage", 0.0))
            b["_fin_plan"], b["_debut_plan"] = pl["end"], pl["start"]
            if pl.get("scene"):
                b["scene"] = pl["scene"]
        if "source" not in b:
            continue
        at = src_to_final(b.get("voix_source", voix), b["voix_t"]) if "voix_t" in b else remap(b.get("at", 0.0))
        k = _anchor(timeline, at, b.get("mot")) if b.get("mot") else None
        start = max(0.0, timeline[k]["start"] - BROLL_EARLY_FRAMES / fps) if k is not None else at
        items.append((round(start * fps) / fps, b))
    items.sort(key=lambda x: x[0])
    out = []
    for j, (start, b) in enumerate(items):
        if j == 0:
            start = 0.0  # jamais de noir au début
        end = items[j + 1][0] if j + 1 < len(items) else total
        if end - start < 2 / fps:  # deux plans sur le même mot : le second gagne
            continue
        if out and abs(out[-1]["at"] + out[-1]["duration"] - start) > 1e-6:
            out[-1]["duration"] = round(start - out[-1]["at"], 6)  # le précédent va jusqu'à celui-ci
        dur = end - start
        fin = b.pop("_fin_plan", None)
        debut = b.pop("_debut_plan", b.get("source_start", 0.0))
        ss = b.get("source_start", 0.0)
        if fin is not None and ss + dur > fin:  # recule dans le plan plutôt que d'en sortir
            ss = max(debut, fin - dur)
            if ss + dur > fin + 1e-6:
                b["plan_trop_court"] = True
        out.append(dict(b, at=round(start, 6), duration=round(dur, 6), source_start=round(ss, 6)))
    if out:
        out[-1]["duration"] = round(total - out[-1]["at"], 6)
    return out


def _anchor(timeline, at, mot):
    """Mot d'ancrage du b-roll : `mot` le plus proche de `at` s'il existe, sinon le premier mot commençant à/après `at`."""
    if not timeline:
        return None
    if mot:
        want = _norm(mot)
        hits = [k for k, w in enumerate(timeline) if _norm(w["w"]) == want] or \
               [k for k, w in enumerate(timeline) if want and _norm(w["w"]).startswith(want)]
        if hits:
            return min(hits, key=lambda k: abs(timeline[k]["start"] - at))
    return next((k for k, w in enumerate(timeline) if w["end"] > at), len(timeline) - 1)


def measure_breathing(transcript, cuts=None):
    """Respiration d'une vidéo DÉJÀ montée (référence DA) : médianes des pauses entre mots.
    - respiration_intra_s : pauses ≥ 60 ms à l'intérieur d'une phrase (les mots collés ne sont pas des pauses) ;
    - respiration_phrases_s : écarts après une fin de phrase ;
    - coupe_avant_mot_s : si `cuts` (temps des changements de plan) est fourni, médiane de (début du mot qui suit
      la coupe − coupe), sinon défaut.
    Valeur par défaut quand moins de 3 mesures."""
    words = [w for w in (transcript or {}).get("words", []) if not is_filler(w)]
    intra, phr = [], []
    for a, b in zip(words, words[1:]):
        g = b["start"] - a["end"]
        if sentence_end(a):
            if 0.05 <= g <= 1.5:
                phr.append(g)
        elif 0.06 <= g <= 1.0:
            intra.append(g)
    pre = []
    for c in cuts or []:
        nxt = next((w["start"] for w in words if w["start"] >= c - 0.01), None)
        if nxt is not None and nxt - c <= 0.4:
            pre.append(max(0.0, nxt - c))

    def med(xs, key, lo, hi):
        return round(min(max(statistics.median(xs), lo), hi), 3) if len(xs) >= 3 else DEFAULTS[key]
    return {"respiration_intra_s": med(intra, "respiration_intra_s", 0.06, 0.5),
            "respiration_phrases_s": med(phr, "respiration_phrases_s", 0.12, 1.0),
            "coupe_avant_mot_s": med(pre, "coupe_avant_mot_s", 0.0, 0.25)}


def main():
    ap = argparse.ArgumentParser(description="Recale l'edl sur les mots et le rythme du créateur → .monteur/edl.rendu.json")
    ap.add_argument("edl")
    ap.add_argument("--profil", help="ma-da/profile.json (défaut : ma-da/profile.json du dossier de travail)")
    a = ap.parse_args()
    p = Path(a.edl)
    if not p.is_file():
        die(f"edl introuvable : {p}")
    root = p.resolve().parent
    root = root.parent if root.name == ".monteur" else root
    prof = Path(a.profil) if a.profil else root / "ma-da" / "profile.json"
    profile = json.loads(prof.read_text(encoding="utf-8")) if prof.is_file() else {}
    res = normalize_edl(json.loads(p.read_text(encoding="utf-8")), root, profile)
    s = res["pacing"]
    print(f"Rythme : {s['segments_avant']} segments → {s['morceaux']} morceaux, "
          f"{s['duree_avant_s']:.2f} s → {s['duree_apres_s']:.2f} s, {s['hesitations_coupees']} hésitation(s) coupée(s), "
          f"{len(res['broll'])} b-roll → {root / '.monteur' / 'edl.rendu.json'}")


if __name__ == "__main__":
    main()
