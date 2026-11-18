"""Page de review : frise lue depuis le rendu et le .srt, vidéo servie par morceaux, notes enregistrées."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import review  # noqa: E402


def test_srt_et_frise(tmp_path):
    (tmp_path / "sorties").mkdir()
    v = tmp_path / "sorties" / "r.mp4"
    v.write_bytes(b"0123456789")
    v.with_suffix(".srt").write_text("1\n00:00:01,500 --> 00:00:02,000\nbonjour\ntoi\n\n", encoding="utf-8")
    (tmp_path / ".monteur").mkdir()
    (tmp_path / ".monteur" / "edl.rendu.json").write_text(json.dumps({
        "output": "sorties/r.mp4", "segments": [{"source": "a.mov", "start": 10, "end": 12}, {"source": "a.mov", "start": 20, "end": 21}],
        "broll": [{"source": "broll/x.mp4", "at": 2.5, "duration": 0.5, "mot": "club"}]}), encoding="utf-8")
    f = review.frise(v, tmp_path)
    assert [(p["piste"], p["de"], p["a"]) for p in f] == [("rush", 0, 2), ("rush", 2, 3), ("plan", 2.5, 3.0), ("sous-titre", 1.5, 2.0)]
    assert f[-1]["texte"] == "bonjour toi" and "club" in f[2]["texte"]


def test_serveur_range_et_notes(tmp_path):
    v = tmp_path / "r.mp4"
    v.write_bytes(bytes(range(100)))
    notes = tmp_path / "n.json"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), review.serveur(v, notes, b"<html>"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    u = f"http://127.0.0.1:{srv.server_address[1]}"
    r = urllib.request.urlopen(urllib.request.Request(u + "/video", headers={"Range": "bytes=10-19"}))
    assert r.status == 206 and r.read() == bytes(range(10, 20))
    body = json.dumps([{"t": 1.2, "texte": "trop long"}]).encode()
    j = {"Content-Type": "application/json"}
    urllib.request.urlopen(urllib.request.Request(u + "/notes", data=body, method="POST", headers=j))
    assert json.loads(notes.read_text(encoding="utf-8"))[0]["texte"] == "trop long"
    for data, hd, code in [(b'{"x":1}', j, 400), (b'[{"t":"1","texte":"x"}]', j, 400),
                           (body, {"Content-Type": "text/plain"}, 403),            # requête « simple » d'un autre site
                           (body, dict(j, Origin="https://site-malveillant.example"), 403)]:
        try:
            urllib.request.urlopen(urllib.request.Request(u + "/notes", data=data, method="POST", headers=hd))
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == code
    assert json.loads(notes.read_text(encoding="utf-8"))[0]["texte"] == "trop long"
    srv.shutdown()


def test_js_ne_ferme_pas_le_script():
    assert "</script>" not in review.js([{"texte": "</script><script>alert(1)</script>"}])
    assert json.loads(review.js({"a": "<&>"})) == {"a": "<&>"}


def test_partition_texte(tmp_path, capsys):
    (tmp_path / "sorties").mkdir()
    v = tmp_path / "sorties" / "r.mp4"
    v.write_bytes(b"0")
    v.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nun deux\n\n", encoding="utf-8")
    tr = tmp_path / ".monteur" / "transcripts"
    tr.mkdir(parents=True)
    (tr / "a.json").write_text(json.dumps({"source": "a.mov", "words": [
        {"w": "un", "start": 10.0, "end": 10.3}, {"w": "deux", "start": 10.8, "end": 11.0}]}), encoding="utf-8")
    (tmp_path / ".monteur" / "edl.rendu.json").write_text(json.dumps({
        "output": "sorties/r.mp4", "segments": [{"source": "a.mov", "start": 9.9, "end": 11.1}]}), encoding="utf-8")
    review.main([str(v), "--root", str(tmp_path), "--partition"])
    out = capsys.readouterr().out
    assert "0.50 s sans parole" in out and "un deux" in out
