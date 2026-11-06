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
TAIL_MIN = 0.10      # toujours ≥ 100 ms après le dernier mot gardé (fins Whisper souvent trop tôt)
PRE_MIN = 0.03
MIN_CUT = 0.10       # ne pas créer un raccord pour gagner moins de 3 images
FILLER_PAUSE = 0.10  # une hésitation ne se coupe proprement qu'entourée de pauses ≥ 100 ms
BROLL_EARLY_FRAMES = 2
BROLL_TAIL = 0.15
KEEP_NOTE = re.compile(r"gard|keep|euh", re.I)


def _rythme(profile):
    r = dict(DEFAULTS)
    r.update({k: v for k, v in ((profile or {}).get("rythme") or {}).items() if k in DEFAULTS and v is not None})
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
        i_lo = max(int(lo / self.STEP), int((t_word - 0.2) / self.STEP), 0)
        while i > i_lo and i - 1 < len(self.db) and self.db[i - 1] > thr:
            i -= 1
        return i * self.STEP

    def offset(self, t_word, hi):
        """Fin réelle de la voix d'un mot fini à `t_word` : première tranche calme après (≤ `hi`)."""
        thr = self._floor(t_word) + 9
        i = int((t_word - 0.04) / self.STEP)
        hi = min(hi, t_word + 0.25)  # hi peut être infini (dernier mot du fichier)
        i_hi = min(int(hi / self.STEP), len(self.db))
        while i < i_hi and self.db[i] > thr:
            i += 1
        return i * self.STEP


def _seg_words(seg, words):
    """Mots du segment (milieu dans [start, end[, même règle que captions.timeline_words)."""
    return [w for w in words if seg["start"] <= (w["start"] + w["end"]) / 2 < seg["end"]]


def _cut_plan(seg, words, r, last_seg, env=None):
    """Morceaux (start, end, mots, limite dure) d'un segment : fillers isolés retirés, silences compressés.
    Les bords externes ne sont posés qu'ici en pré-roll / traîne ; render arrondit à l'image ensuite."""
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
    start = max(prev_end, kept[0]["start"] - pre, 0.0)
    # traîne : silence réel seulement (jamais sur le mot suivant non gardé), au moins jusqu'à la fin du mot
    end = max(kept[-1]["end"], min(kept[-1]["end"] + tail_end, next_start - 0.01))
    pieces, cur_start, cur = [], start, []
    for a, b in zip(kept, kept[1:] + [None]):
        cur.append(a)
        if b is None:
            break
        gap = b["start"] - a["end"]
        allowed = r["respiration_phrases_s"] if sentence_end(a) else r["respiration_intra_s"]
        removed = [w for w in inside if a["end"] <= w["start"] and w["end"] <= b["start"]]  # hésitation coupée entre a et b
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
                a = min(a, max(lo, env.onset(pw[0]["start"], lo) - 0.02))
                i_last = words.index(pw[-1])
                nxt = words[i_last + 1]["start"] if i_last + 1 < len(words) else math.inf
                lim = min(hard, nxt)
                b = max(b, min(env.offset(pw[-1]["end"], lim) + 0.02, lim - 0.01))
            fixed.append((a, b, pw, hard))
        pieces = fixed
    return pieces, dropped


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
        pieces, d = _cut_plan(s, ws, r, i == len(segs_in) - 1, env_of(s["source"]))
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
                timeline.append(dict(w, start=off_out + w["start"] - piece["start"],
                                     end=off_out + min(w["end"], piece["end"]) - piece["start"], seg=i))
            off_out += frames / fps
    total = off_out

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
        for key in ("titles", "sfx"):
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
    bcfg = (profile or {}).get("broll") or {}
    moy = bcfg.get("duree_moy_s")
    lo, hi = (moy * 0.5, moy * 1.5) if moy else (1.2, 4.0)
    brolls = []
    for b in edl.get("broll") or []:
        at = remap(b["at"])
        k = _anchor(timeline, at, b.get("mot"))
        if k is not None:
            w = timeline[k]
            start = max(0.0, w["start"] - BROLL_EARLY_FRAMES / fps)  # sur le mot, jamais en retard
            last = sent_end[k]
            limit = timeline[last + 1]["start"] if last + 1 < len(timeline) else total
            end = min(timeline[last]["end"] + BROLL_TAIL, limit)
            dur = min(max(end - start, lo), hi)
            dur = min(dur, limit - start, total - start)  # jamais dans la phrase suivante
        else:
            start, dur = at, min(max(b.get("duration", lo), lo), hi, total - at)
        if dur <= 0:
            continue
        if bcfg.get("jamais_sur_hook", True) and any(start < pe and start + dur > ps for ps, pe in protected):
            continue
        brolls.append(dict(b, at=round(start, 3), duration=round(dur, 3)))
    res = dict(edl, segments=out_segs, broll=brolls)
    for key in ("titles", "sfx"):
        if edl.get(key):
            res[key] = []
            for x in edl[key]:
                at = remap(x["at"])
                y = dict(x, at=round(at, 3))
                if "duration" in x:
                    y["duration"] = round(max(0.0, min(x["duration"], total - at)), 3)
                res[key].append(y)
    res["pacing"] = {"duree_avant_s": round(off_in, 3), "duree_apres_s": round(total, 3),
                     "segments_avant": len(segs_in), "morceaux": len(out_segs), "hesitations_coupees": dropped,
                     "rythme": r}
    wd = root / ".monteur"
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "edl.rendu.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


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
