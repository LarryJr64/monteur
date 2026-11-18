"""Illustrations libres : sources sans clé (Commons, NASA), tailles, durées, crédits, pas d'écrasement. Réseau simulé."""
import io
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import illustrer  # noqa: E402
import plans  # noqa: E402


def _commons(titre, lic, duree, der=("480p.vp9.webm", "1080p.vp9.webm"), w=1920, h=1080):
    return {"title": titre, "index": 1, "videoinfo": [{
        "duration": duree, "thumburl": f"https://t/{titre}.jpg", "descriptionurl": f"https://c/{titre}",
        "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": "<a>Jean</a>"},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0"},
                        "ObjectName": {"value": titre}},
        "derivatives": [{"transcodekey": k, "src": f"https://u/{titre}.{k}", "width": w if "1080" in k else 854,
                         "height": h if "1080" in k else 480} for k in der]}]}


def test_wikimedia_video_licence_taille_duree(monkeypatch):
    pages = {str(i): p for i, p in enumerate([
        _commons("ok", "CC BY 4.0", 12.0), _commons("sa", "CC BY-SA 4.0", 12.0), _commons("long", "CC0", 400.0),
        _commons("petit", "CC0", 10.0, der=("480p.vp9.webm",))])}
    urls = []
    monkeypatch.setattr(illustrer, "get_json", lambda u, h=None: urls.append(u) or {"query": {"pages": pages}})
    cs = illustrer.candidats_idee({"id": "x", "type": "video", "requetes": ["ocean waves"]}, {})
    videos = [c for c in cs if c["source"] == "wikimedia" and c["kind"] == "video"]
    assert [c["titre"] for c in videos] == ["ok"]  # ni BY-SA, ni > 60 s, ni sans 720p/1080p
    assert videos[0]["url"].endswith("1080p.vp9.webm") and videos[0]["auteur"] == "Jean"
    assert any("filetype%3Avideo" in u and "videoinfo" in u for u in urls)
    assert any("iiurlheight=1920" in u for u in urls)  # photos Commons en hauteur 1920
    assert illustrer._mini_commons("https://upload.wikimedia.org/wikipedia/commons/7/7b/A.jpg?x=1") == \
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/7/7b/A.jpg/330px-A.jpg"
    assert illustrer._mini_commons("https://thumb.wikimedia.org/wikipedia/commons/thumb/e/eb/B.jpg/1280px-B.jpg") == \
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/e/eb/B.jpg/330px-B.jpg"


def test_wikimedia_photo_petite_original_en_taille_standard(monkeypatch):
    page = {"title": "File:A.jpg", "imageinfo": [{
        "thumburl": "https://upload.wikimedia.org/wikipedia/commons/7/7b/A.jpg?utm_content=thumbnail_unscaled",
        "width": 1184, "height": 760, "thumbwidth": 1184, "thumbheight": 760,
        "extmetadata": {"LicenseShortName": {"value": "CC0"}}}]}
    monkeypatch.setattr(illustrer, "get_json", lambda u, h=None: {"query": {"pages": {"1": page}}})
    (c,) = illustrer.wikimedia("a", "photo")
    assert c["url"] == "https://thumb.wikimedia.org/wikipedia/commons/thumb/7/7b/A.jpg/960px-A.jpg"


def test_nasa_domaine_public_sans_tiers(monkeypatch):
    def faux(u, h=None):
        if "search" in u:
            return {"collection": {"items": [
                {"data": [{"nasa_id": "A1", "title": "Launch", "photographer": "Rocket Lab"}], "links": []},
                {"data": [{"nasa_id": "B2", "title": "Earth view"}], "links": [{"href": "https://m/b.jpg", "render": "image"}]}]}}
        if "asset/B2" in u:
            return {"collection": {"items": [{"href": "http://x/B2~orig.mp4"}, {"href": "http://x/B2~mobile.mp4"},
                                             {"href": "http://x/B2~medium.mp4"}, {"href": "http://x/metadata.json"}]}}
        if u.endswith("metadata.json"):
            return {"QuickTime:Duration": "0:00:30", "QuickTime:ImageWidth": 1920, "QuickTime:ImageHeight": 1080}
        raise AssertionError(u)  # A1 (filmé par un tiers) n'est même pas ouvert
    monkeypatch.setattr(illustrer, "get_json", faux)
    (c,) = illustrer.nasa("earth", "video")
    assert c["url"] == "https://x/B2~medium.mp4" and c["duree"] == 30 and c["licence"].startswith("Domaine public")
    assert illustrer._duree("12.50 s") == 12.5


def test_pixabay_jamais_4k(monkeypatch):
    v = {"user": "u", "pageURL": "p", "duration": 9, "videos": {
        "large": {"url": "L", "width": 3840, "height": 2160}, "medium": {"url": "M", "width": 1920, "height": 1080},
        "small": {"url": "S", "width": 1280, "height": 720}}}
    monkeypatch.setattr(illustrer, "get_json", lambda u, h=None: {"hits": [v]})
    assert illustrer.pixabay("q", "video", "k")[0]["url"] == "M"


def test_vertical_d_abord_et_duree_min(monkeypatch):
    def src(q, kind, key):
        return [{"kind": "video", "url": "a", "w": 1920, "h": 1080, "duree": 8},
                {"kind": "video", "url": "b", "w": 1080, "h": 1920, "duree": 8.4},
                {"kind": "video", "url": "a2", "w": 1920, "h": 1080, "duree": 8.0},  # même clip qu'a (même durée)
                {"kind": "video", "url": "c", "w": 1080, "h": 1920, "duree": 2}] if kind == "video" else []
    for nom in ("pexels", "pixabay", "wikimedia", "nasa", "openverse"):
        monkeypatch.setattr(illustrer, nom, src)
    cs = illustrer.candidats_idee({"id": "x", "type": "video", "duree_min": 5, "requetes": ["q"]}, {"pexels": "k"})
    assert [c["url"] for c in cs] == ["b", "a"]
    assert illustrer.legende(dict(cs[0], repere="x-1", source="pexels")) == "x-1 ▶ 8,4 s ▯ pexels · ?"


def test_cache_24h(monkeypatch, tmp_path):
    monkeypatch.setattr(illustrer, "CACHE", tmp_path)
    appels = []

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(illustrer.urllib.request, "urlopen", lambda req, timeout: appels.append(1) or R(b'{"a": 1}'))
    assert illustrer.get_json("https://x/?q=1") == {"a": 1} == illustrer.get_json("https://x/?q=1")
    assert len(appels) == 1


def test_choisir_libres_credits_sans_ecraser(monkeypatch, tmp_path, capsys):
    (tmp_path / "B-Roll" / "libres").mkdir(parents=True)
    (tmp_path / "B-Roll" / "libres" / "dubai-2.webm").write_bytes(b"le sien")
    wd = tmp_path / ".monteur"
    wd.mkdir()
    (wd / "plans.json").write_text(json.dumps({"plans": {"P1": {"source": "B-Roll/fete.mp4"}}}), encoding="utf-8")
    cands = [{"repere": "dubai-2", "idee": "dubai", "kind": "video", "url": "https://u/v.1080p.vp9.webm",
              "source": "wikimedia", "auteur": "Jean", "licence": "CC BY 4.0", "titre": "Dubai night",
              "licence_url": "https://creativecommons.org/licenses/by/4.0", "page": "https://c/p", "w": 1920,
              "h": 1080, "mot": "Dubaï", "requete": "Dubai skyline night"},
             {"repere": "mer-1", "idee": "mer", "kind": "video", "url": "https://p/v.mp4", "source": "pexels",
              "auteur": "Ana", "licence": "Pexels (libre, sans attribution)", "w": 1080, "h": 1920, "mot": "mer",
              "requete": "sea"}]
    (wd / "candidats.json").write_text(json.dumps(cands), encoding="utf-8")
    monkeypatch.setattr(illustrer, "telecharger", lambda u, d, h=None: d.write_bytes(b"neuf") or d)
    illustrer.choisir(["dubai-2", "mer-1"], tmp_path)
    lib = tmp_path / "B-Roll" / "libres"
    assert (lib / "dubai-2.webm").read_bytes() == b"le sien" and (lib / "dubai-2_2.webm").read_bytes() == b"neuf"
    credits = (wd / "credits.txt").read_text(encoding="utf-8")
    assert "« Dubai night » par Jean, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0)" in credits
    assert "Ana via Pexels" in credits
    assert "recadré et raccourci" in (lib / "CREDITS.md").read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert '"libre": true' in out and '"zoom_center": [0.5, 0.5]' in out
    assert "plans.py B-Roll/fete.mp4 B-Roll/libres/dubai-2_2.webm B-Roll/libres/mer-1.mp4" in out
    desc = (wd / "descriptions.txt").read_text(encoding="utf-8")
    assert "B-Roll/libres/mer-1.mp4: illustration libre, sea" in desc
    illustrer.choisir(["mer-1"], tmp_path)  # relance : pas de crédit en double
    assert (wd / "credits.txt").read_text(encoding="utf-8").count("Ana via Pexels") == 1


def test_choisir_repere_inconnu_ne_telecharge_rien(monkeypatch, tmp_path):
    (tmp_path / ".monteur").mkdir()
    (tmp_path / ".monteur" / "candidats.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(illustrer, "telecharger", lambda *a: (_ for _ in ()).throw(AssertionError))
    try:
        illustrer.choisir(["zz-1"], tmp_path)
    except SystemExit:
        pass
    assert not (tmp_path / "broll").exists()


def test_cle_n_ecrit_pas_les_variables_d_environnement(monkeypatch, tmp_path):
    monkeypatch.setattr(illustrer, "CLES", tmp_path / "cles.json")
    monkeypatch.setenv("PIXABAY_API_KEY", "secret-env")
    illustrer.main(["cle", "pexels", "abc"])
    assert json.loads((tmp_path / "cles.json").read_text(encoding="utf-8")) == {"pexels": "abc"}


def test_planche_et_bande_pexels(monkeypatch, tmp_path):
    def faux(u, d, h=None):
        Image.new("RGB", (90, 160), (200, 0, 0)).save(d, "JPEG")
        return d
    monkeypatch.setattr(illustrer, "telecharger", faux)
    c = {"kind": "video", "vignettes": ["a", "b", "c"], "w": 1080, "h": 1920, "duree": 8.4, "repere": "dubai-2",
         "source": "pexels", "licence": "Pexels", "miniature_locale": "dubai-2.jpg"}
    illustrer.miniature(c, tmp_path / "dubai-2.jpg")
    assert Image.open(tmp_path / "dubai-2.jpg").size == (270, 160)  # début / milieu / fin côte à côte
    (p,) = illustrer.planche([c], tmp_path, tmp_path)
    assert p.is_file()


def test_plans_description_par_fichier(tmp_path):
    p = {"P1": {"source": "broll/a.mp4", "start": 0, "end": 2, "duree": 2, "mouvement": "fixe", "nettete": "net",
                "description": ""},
         "P2": {"source": "B-Roll/libres/mer-1.mp4", "start": 0, "end": 3, "duree": 3, "mouvement": "lent",
                "nettete": "net", "description": ""}}
    (tmp_path / "descriptions.txt").write_text("B-Roll/libres/mer-1.mp4: illustration libre, sea\nP1: fête\n",
                                               encoding="utf-8")
    assert plans.descriptions(tmp_path, p) == 0
    assert p["P2"]["description"] == "illustration libre, sea" and p["P1"]["description"] == "fête"
