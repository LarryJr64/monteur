import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import captions  # noqa: E402


def W(*items):
    return [{"w": w, "start": a, "end": b} for w, a, b in items]


WORDS = W(("Salut", 0.0, 0.3), ("euh", 0.35, 0.5), ("tout", 0.55, 0.7), ("le", 0.72, 0.8), ("monde.", 0.82, 1.1),
          ("Aujourd’hui", 1.15, 1.6), ("l'", 1.62, 1.7), ("idée", 1.7, 2.0), ("c'est", 2.0, 2.2),
          ("simple", 3.0, 3.4), ("?", 3.4, 3.4))


def test_blocs_filler_ponctuation_pause():
    blocks = [[w["w"] for w in b["words"]] for b in captions.make_blocks(WORDS, 3)]
    assert blocks == [["Salut", "tout"], ["le", "monde"], ["Aujourd'hui", "l'idée", "c'est"], ["simple"]]  # phrase répartie 2+2
    for b in captions.make_blocks(WORDS, 3):
        assert b["end"] > b["start"]


def test_question_garde_le_point():
    blocks = captions.make_blocks(W(("tu", 0, 0.2), ("viens", 0.2, 0.5), ("?", 0.5, 0.5)), 3)
    assert blocks[0]["words"][-1]["w"] == "viens"
    blocks = captions.make_blocks(W(("tu", 0, 0.2), ("viens ?", 0.2, 0.5)), 3)
    assert blocks[0]["words"][-1]["w"] == "viens ?"


def test_ass_pop_karaoke():
    ass = captions.build_ass(WORDS, {"animation": "pop", "majuscules": True}, 1080, 1920,
                             [{"text": "Titre", "at": 0, "duration": 1}])
    assert "PlayResY: 1920" in ass and "SALUT TOUT" in ass and r"\fscx80" in ass and "TITRE" in ass
    assert "&H0000E6FF" in ass  # #FFE600 en BGR ASS
    kar = captions.build_ass(WORDS, {"animation": "karaoke", "majuscules": False}, 1080, 1920)
    dial = [l for l in kar.splitlines() if l.startswith("Dialogue")]
    assert len(dial) == 8  # un événement par mot affiché
    assert r"\c&H00E6FF&}Salut" in dial[0]
    sans = captions.build_ass(WORDS, {"animation": "aucune"}, 1080, 1920)
    assert r"\fscx80" not in sans


def test_repli_police():
    assert captions.font_name({"police": "Police Qui N Existe Pas"})[0] == "Montserrat ExtraBold"
    assert captions.font_name({"police": "Anton"}) == ("Anton", False)


def test_timeline_recale(monkeypatch):
    segs = [{"source": "a.mp4", "start": 10.0, "end": 11.0, "offset": 0.0},
            {"source": "a.mp4", "start": 20.0, "end": 21.0, "offset": 1.0}]
    tr = {"words": W(("un", 10.1, 10.4), ("hors", 15, 15.3), ("deux", 20.2, 20.5))}
    monkeypatch.setattr(captions, "load_transcript", lambda root, src: tr)
    words = captions.timeline_words(segs, ".")
    assert [(w["w"], round(w["start"], 2)) for w in words] == [("un", 0.1), ("deux", 1.2)]


def test_find_transcript_par_source(tmp_path):
    """Deux fichiers a.mp4 (mes-videos/ et rushs/) : on retrouve le bon transcript via `source`."""
    from common import find_transcript
    tdir = tmp_path / ".monteur" / "transcripts"
    tdir.mkdir(parents=True)
    (tdir / "a.json").write_text(json.dumps({"source": "mes-videos/a.mp4", "words": []}), encoding="utf-8")
    (tdir / "rushs_a.json").write_text(json.dumps({"source": "rushs/a.mp4", "words": [1]}), encoding="utf-8")
    assert find_transcript(tmp_path, "rushs/a.mp4")["words"] == [1]
    assert find_transcript(tmp_path, "mes-videos/a.mp4")["words"] == []
    assert find_transcript(tmp_path, (tmp_path / "rushs" / "a.mp4").resolve())["words"] == [1]
    assert find_transcript(tmp_path, "broll/a.mp4") is None


def test_trait_union_colle():
    words = W(("Abonne", 0.0, 0.3), ("-toi", 0.3, 0.5), ("vite.", 0.5, 0.8), ("l", 0.9, 1.0), ("'hormone", 1.0, 1.4))
    assert [w["w"] for w in captions.clean_words(words)] == ["Abonne-toi", "vite", "l'hormone"]


def test_hesitations_gardees_jamais_affichees(monkeypatch):
    """Hésitations gardées au son (note « garde » ou non isolées) : ni sous-titre ni hôte d'emoji."""
    words = W(("e", 0.0, 0.1), ("eh", 0.1, 0.2), ("Alors", 0.2, 0.5), ("hm", 0.5, 0.6), ("mm", 0.6, 0.7),
              ("[euh]", 0.7, 0.9), ("bon.", 0.9, 1.2))
    words[0]["filler"] = True
    segs = [{"source": "a.mp4", "start": 0.0, "end": 1.3, "offset": 0.0, "emoji": "☕", "note": "garde"}]
    monkeypatch.setattr(captions, "load_transcript", lambda root, src: {"words": words})
    tw = captions.timeline_words(segs, ".")
    assert [w["w"] for w in tw if w.get("emoji")] == ["Alors"]
    shown = [w["w"] for b in captions.make_blocks(tw, 3) for w in b["words"]]
    assert shown == ["Alors", "bon"]
    ass = captions.build_ass(tw, {"animation": "pop"}, 1080, 1920)
    assert not any(f" {x.upper()} " in f" {ass} " for x in ("E", "EH", "HM", "MM", "[EUH]", "(EUH)"))


def test_v2_mot_cle_emoji_zone_sure_visage(monkeypatch):
    words = W(("la", 0.0, 0.2), ("pire", 0.2, 0.5), ("erreur", 0.5, 0.9), ("possible", 1.0, 1.4))
    segs = [{"source": "a.mp4", "start": 0.0, "end": 0.95, "offset": 0.0, "seg": 0, "mots_cles": ["pire", "erreur"],
             "emoji": "☕"},
            {"source": "a.mp4", "start": 0.95, "end": 1.5, "offset": 0.95, "seg": 0, "mots_cles": ["pire", "erreur"],
             "emoji": "☕"}]  # deux morceaux pacing du même segment : un seul emoji
    monkeypatch.setattr(captions, "load_transcript", lambda root, src: {"words": words})
    tw = captions.timeline_words(segs, ".")
    assert [w["w"] for w in tw if w.get("cle")] == ["pire", "erreur"]
    assert [w["w"] for w in tw if w.get("emoji")] == ["pire"]  # sur le 1er mot-clé
    st = {"animation": "pop", "zone_sure": True, "couleur_mot_cle": "#FDE047", "position_y_pct": 0.9}
    ass = captions.build_ass(tw, st, 1080, 1920)
    dial = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert dial[0].count(r"\fscx115") == 1 and r"\c&H47E0FD&" in dial[0]  # max 1 mot-clé par bloc, 115 %
    y = int(dial[0].split(r"\pos(")[1].split(",")[1].split(")")[0])
    assert y < 0.80 * 1920  # zone sûre : jamais dans les 20 % du bas
    x = int(dial[0].split(r"\pos(")[1].split(",")[0])
    assert x < 540  # marge droite élargie (boutons de l'appli)
    # visage de 55 % à 75 % de la hauteur : le bloc passe au-dessus
    face = lambda a, b: (0.55 * 1920, 0.75 * 1920)  # noqa: E731
    blocks, *_ = captions.layout(tw, {**st, "position_y_pct": 0.7}, 1080, 1920, face)
    for b in blocks:
        assert b["y"] + b["hh"] <= 0.55 * 1920 or b["y"] - b["hh"] >= 0.75 * 1920
    # gros plan (visage de 15 % à 78 %) : ni dessous ni au-dessus possible → en bas de la zone sûre, jamais le front
    big = lambda a, b: (0.15 * 1920, 0.78 * 1920)  # noqa: E731
    blocks, *_ = captions.layout(tw, {**st, "position_y_pct": 0.7}, 1080, 1920, big)
    for b in blocks:
        assert b["y"] > 0.7 * 1920 and b["y"] + b["hh"] <= 0.80 * 1920 + 1
    em = captions.emoji_overlays(tw, st, 1080, 1920)
    plain, *_ = captions.layout(tw, st, 1080, 1920)
    assert len(em) == 1 and em[0]["emoji"] == "☕" and em[0]["y"] < plain[0]["y"] - plain[0]["hh"]  # au-dessus
    assert em[0]["size"] > 120


def test_animations_v3():
    """Chaque animation produit un .ass valide ; mot par mot cache la suite sans bouger la ligne ;
    le surlignage dessine une boîte sous le mot dit, alignée sur sa largeur."""
    words = [{"w": "Tu", "start": 0.3, "end": 0.45}, {"w": "perds", "start": 0.5, "end": 0.8},
             {"w": "ton", "start": 0.85, "end": 1.0}]
    for anim in captions.ANIMATIONS:
        ass = captions.build_ass(words, {"animation": anim}, 1080, 1920)
        assert ass.count("Dialogue:") >= 1, anim
    mpm = captions.build_ass(words, {"animation": "mot_par_mot"}, 1080, 1920)
    assert r"{\alpha&HFF&}PERDS TON" in mpm and mpm.count(r"\pos(540,") == 3  # même position à chaque mot
    assert r"\move(" in captions.build_ass(words, {"animation": "glisse"}, 1080, 1920)
    assert r"\fad(" in captions.build_ass(words, {"animation": "fondu"}, 1080, 1920)
    sur = captions.build_ass(words, {"animation": "surlignage"}, 1080, 1920)
    boxes = [line for line in sur.splitlines() if ",Boite,," in line]
    assert len(boxes) == 3 and all(line.startswith("Dialogue: 0,") for line in boxes)
    xs = [float(line.split(r"\p1}m ")[1].split()[0]) for line in boxes]
    assert xs[0] < xs[1] < xs[2]  # la boîte avance avec le mot dit
    assert "inconnue" not in captions.build_ass(words, {"animation": "inconnue"}, 1080, 1920)  # repli pop


def test_taille_reelle_par_police():
    """La hauteur des capitales suit taille_pct_hauteur quelle que soit la police (métriques OS/2 lues)."""
    for f in ("Montserrat-ExtraBold.ttf", "Poppins-ExtraBold.ttf", "Anton-Regular.ttf", "BebasNeue-Regular.ttf"):
        cap_r, em_r = captions.font_ratios(captions.FONTS_DIR / f)
        assert 0.3 < cap_r < 0.6 and 0.5 < em_r < 0.9, f
    assert abs(captions.font_ratios(captions.FONTS_DIR / "Montserrat-ExtraBold.ttf")[0] - captions.CAP_RATIO) < 0.01
    assert captions.font_ratios(captions.FONTS_DIR / "absent.ttf") is None


def test_mots_cles_doses_comme_la_da():
    """Claude propose un mot-clé presque partout ; la DA en a sur 1 bloc sur 3 → on en garde 1/3, chiffres d'abord."""
    ws = [{"w": w, "start": i * 0.3, "end": i * 0.3 + 0.25, "cle": i % 2 == 0}
          for i, w in enumerate("un deux 300 quatre cinq six sept huit neuf dix onze douze".split())]
    blocks = captions.make_blocks(ws, 2)
    dosed = captions.dose_keywords(blocks, 1 / 3)
    n = sum(any(w.get("cle") for w in b["words"]) for b in dosed)
    assert n == 2 and len(dosed) == 6
    assert any(w.get("cle") and w["w"] == "300" for b in dosed for w in b["words"])  # le chiffre gardé
    assert captions.dose_keywords(blocks, 0.9) == blocks  # jamais d'ajout


def test_carton_plein_ecran_et_mot_en_couleur():
    import captions
    ass = captions.build_ass([], {"couleur_mot_cle": "#F2A516"}, 1080, 1920,
                             [{"text": "Ta plus belle *réussite* ?", "at": 0, "duration": 3, "fond": "#0E2233"}])
    assert "m 0 0 l 1080 0 1080 1920 0 1920" in ass and "Dialogue: 5," in ass  # fond par-dessus les sous-titres
    assert r"\1c&H16A5F2&" in ass and r"\pos(540,960)" in ass  # mot-clé orange, texte centré


def test_bloc_coupe_sur_nouvelle_phrase():
    """« pu percer | Parce que » : une coupe suivie d'une majuscule ouvre un nouveau bloc."""
    ws = [{"w": "pu", "start": 0.0, "end": 0.2}, {"w": "percer", "start": 0.2, "end": 0.5},
          {"w": "Parce", "start": 0.5, "end": 0.7, "coupe": True}, {"w": "que", "start": 0.7, "end": 0.8},
          {"w": "Maurice", "start": 0.8, "end": 1.1}]
    assert [[w["w"] for w in b["words"]] for b in captions.make_blocks(ws, 3)] == [["pu", "percer"], ["Parce", "que", "Maurice"]]


def test_sous_titres_jamais_sur_un_insert_et_profil_tolerant():
    """Le bloc commencé avant un insert s'arrête à son entrée ; « ombre » en texte et couleur null ne plantent pas."""
    ws = [{"w": "pour", "start": 0.0, "end": 0.3}, {"w": "te", "start": 0.3, "end": 0.5},
          {"w": "dire", "start": 0.5, "end": 0.9}, {"w": "après", "start": 2.0, "end": 2.4}]
    ass = captions.build_ass(ws, {"ombre": "douce", "couleur_mot_actif": None, "animation": "aucune"}, 1080, 1920,
                             caches=[(0.4, 1.8)])
    dial = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert all(not (l.split(",")[1] < "0:00:01.80" and l.split(",")[2] > "0:00:00.40") for l in dial), dial
    assert any("APRÈS" in l.upper() for l in dial)


def test_police_graisse_interlettrage_ombre_douce_flou():
    """Graisse choisie parmi les fichiers embarqués ; interlettrage → Spacing ASS et largeur mesurée ; ombre douce =
    copie floutée au calque 0 sans couleur ; `flou` entre flou → net ; max_caracteres coupe les blocs trop longs."""
    assert captions.font_name({"police": "Montserrat"})[0] == "Montserrat ExtraBold"  # défaut inchangé (gras)
    assert captions.font_name({"police": "Montserrat", "graisse": 600})[0] == "Montserrat SemiBold"
    assert captions.font_name({"police": "Inter", "graisse": 800})[0] == "Inter Tight SemiBold"  # seule graisse dispo
    ws = [{"w": w, "start": i * 0.3, "end": i * 0.3 + 0.25, "cle": w == "marché"}
          for i, w in enumerate("je suis allé au marché acheter des pommes".split())]
    st = {"police": "Inter Tight", "graisse": 600, "interlettrage_pct": -2.5, "ombre_flou": 9, "max_caracteres": 20,
          "mots_par_bloc": 4, "animation": "flou", "majuscules": False, "epaisseur_contour": 0}
    ass = captions.build_ass(ws, st, 1080, 1920)
    style = next(l for l in ass.splitlines() if l.startswith("Style: Mots")).split(",")
    assert style[1] == "Inter Tight SemiBold" and float(style[13]) < 0 and float(style[17]) == 0  # Spacing<0, pas d'ombre dure
    dial = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    nets, ombres = [l for l in dial if l.startswith("Dialogue: 1,")], [l for l in dial if l.startswith("Dialogue: 0,")]
    assert len(nets) == len(ombres) == 3
    assert r"\blur6\fscx105" in nets[0] and r"\t(0,120,\blur0" in nets[0]
    assert r"\blur9.0" in ombres[0] and r"\blur0" not in ombres[0] and r"\c&H" not in ombres[1]
    assert all(len(" ".join(w["w"] for w in b["words"])) <= 20 for b in captions.make_blocks(ws, 4, 20))
    serre = captions.layout(ws, st, 1080, 1920)[0][0]["widths"]
    large = captions.layout(ws, {**st, "interlettrage_pct": 0}, 1080, 1920)[0][0]["widths"]
    assert all(a < b for a, b in zip(serre, large))
    sans = captions.build_ass(ws, {"animation": "fondu"}, 1080, 1920)  # profil d'avant : rien ne change
    assert "Dialogue: 0," not in sans and ",100,100,0.0,0," in sans


def test_mot_qui_finit_au_debut_du_segment_pas_affiche(monkeypatch):
    tr = {"words": W(("fin", 9.95, 10.02), ("un", 10.1, 10.4))}
    monkeypatch.setattr(captions, "load_transcript", lambda root, src: tr)
    words = captions.timeline_words([{"source": "a.mp4", "start": 10.0, "end": 11.0, "offset": 0.0}], ".")
    assert [w["w"] for w in words] == ["un"]


def test_blocs_apres_virgule_jamais_mot_outil_en_fin():
    t = lambda ws: [[w["w"] for w in b["words"]] for b in captions.make_blocks(  # noqa: E731
        W(*[(w, 0.3 * i, 0.3 * i + 0.25) for i, w in enumerate(ws)]), 4, 20)]
    # égal (3+3) laisserait « je » seul en fin de bloc
    assert t(["Moi", "aussi", "je", "pense", "ça", "aujourd'hui."]) == \
        [["Moi", "aussi", "je", "pense"], ["ça", "aujourd'hui"]]
    # la virgule l'emporte sur l'égalité des blocs
    assert t(["Oui,", "je", "pense", "que", "oui."]) == [["Oui"], ["je", "pense", "que", "oui"]]


def test_pas_de_mot_outil_en_fin_meme_serre():
    import captions
    ws = [{"w": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(
        "j'ai réalisé des centaines d'événements".split())]
    blocs = captions.make_blocks(ws, per_block=4, max_car=20)
    assert all(captions.norm(b["words"][-1]["w"]) not in captions.OUTILS for b in blocs[:-1])


def test_mot_dit_surtout_apres_l_insert_garde():
    ws = [{"w": "dans", "start": 0.95, "end": 1.2}, {"w": "quinze", "start": 1.2, "end": 1.5}]
    dial = [l for l in captions.build_ass(ws, {"animation": "aucune"}, 1080, 1920, caches=[(0, 1.0)]).splitlines()
            if l.startswith("Dialogue")]
    assert any("dans" in l.lower() for l in dial)
    assert all(l.split(",")[1] >= "0:00:01.00" for l in dial), dial  # affiché seulement une fois l'insert parti


def test_ombre_douce_renforcee_sur_image_claire():
    ws = [{"w": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate("un marché très difficile".split())]
    st = {"ombre_flou": 9, "animation": "aucune", "epaisseur_contour": 0, "mots_par_bloc": 4}
    vu = []
    ombre = lambda luma: next(l for l in captions.build_ass(  # noqa: E731
        ws, st, 1080, 1920, luma_at=lambda a, b, y0, y1: vu.append((y0, y1)) or luma).splitlines()
        if l.startswith("Dialogue: 0,"))
    assert r"\1a&H50&\blur9.0" in ombre(0.3)  # image sombre : ombre d'avant
    assert r"\1a&H20&\blur13.5" in ombre(0.8)  # image claire : plus dense, plus large
    assert all(0 < y0 < y1 < 1 for y0, y1 in vu)
    sans = captions.build_ass(ws, {"animation": "aucune"}, 1080, 1920, luma_at=lambda *a: 1 / 0)  # sans ombre_flou
    assert "Dialogue: 0," not in sans  # rien mesuré, rien changé
