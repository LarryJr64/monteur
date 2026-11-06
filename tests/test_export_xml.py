import subprocess
import sys
from fractions import Fraction
from xml.etree import ElementTree as ET

import pytest

from test_render import SCRIPTS, make_workdir


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    d = make_workdir(tmp_path_factory.mktemp("xml"))
    r = subprocess.run([sys.executable, str(SCRIPTS / "export_xml.py"), ".monteur/edl.json"], cwd=d,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return d


def secs(t):
    return Fraction(t.rstrip("s")) if t != "0s" else Fraction(0)


def test_fcpxml(exported):
    import captions, json
    d = exported
    rendu = d / ".monteur/edl.rendu.json"  # écrit par pacing s'il est là : ce sont les coupes exportées
    edl = json.loads((rendu if rendu.exists() else d / ".monteur/edl.json").read_text(encoding="utf-8"))
    segs = captions.snapped_segments(edl, d)
    root = ET.parse(d / "sorties/test.fcpxml").getroot()
    assert root.get("version") == "1.9"
    fmt = root.find("resources/format[@id='r0']")
    assert (fmt.get("width"), fmt.get("height"), fmt.get("frameDuration")) == ("1080", "1920", "1/30s")
    for rep in root.iter("media-rep"):
        assert rep.get("src").startswith("file:///")
    spine = root.find(".//sequence/spine")
    clips = spine.findall("asset-clip")
    assert len(clips) == len(segs)
    t = Fraction(0)
    for c, s in zip(clips, segs):
        assert secs(c.get("offset")) == t  # clips bout à bout
        t += secs(c.get("duration"))
        assert abs(secs(c.get("start")) - Fraction(s["start"])) < Fraction(1, 30)
    assert t == secs(root.find(".//sequence").get("duration"))
    assert len(spine.findall("asset-clip/asset-clip[@lane='1']")) == 1


def test_xmeml(exported):
    root = ET.parse(exported / "sorties/test.xml").getroot()
    assert root.get("version") == "4"
    tracks = root.findall(".//media/video/track")
    assert len(tracks) == 2 and len(tracks[1].findall("clipitem")) == 1
    items = tracks[0].findall("clipitem")
    end = 0
    for ci in items:
        assert int(ci.find("start").text) == end
        assert int(ci.find("end").text) - end == int(ci.find("out").text) - int(ci.find("in").text)
        end = int(ci.find("end").text)
    assert end == int(root.find("sequence/duration").text)
    assert root.find(".//pathurl").text.startswith("file://localhost/")
    voix, musique, sfx = root.findall(".//media/audio/track")
    assert len(voix.findall("clipitem")) == len(items) - 1  # carre.mp4 est muet
    assert musique.findall("clipitem") and not sfx.findall("clipitem")  # musique bouclée, pas de bruitage
    assert musique.find(".//effect/effectid").text == "audiolevels"
    assert int(musique.findall("clipitem")[-1].find("end").text) == end


def test_srt(exported):
    srt = (exported / "sorties/test.srt").read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,") and "-->" in srt
    assert "euh" not in srt.lower().split()


def test_v2_rendu_musique_sfx_images_cles(tmp_path):
    """edl v2 : coupes de edl.rendu.json, musique + bruitage sur les pistes 2/3, zooms en images-clés."""
    import json
    from test_render import make_workdir_v2
    d, _ = make_workdir_v2(tmp_path)
    r = subprocess.run([sys.executable, str(SCRIPTS / "export_xml.py"), ".monteur/edl_v2.json", "--profil",
                        "ma-da/profile_v2.json"], cwd=d, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    rendu = json.loads((d / ".monteur/edl.rendu.json").read_text(encoding="utf-8"))         if (d / ".monteur/edl.rendu.json").exists() else json.loads((d / ".monteur/edl_v2.json").read_text(encoding="utf-8"))
    root = ET.parse(d / "sorties/v2.fcpxml").getroot()
    spine = root.find(".//sequence/spine")
    assert len(spine.findall("asset-clip")) == len(rendu["segments"])
    keys = spine.findall("asset-clip/adjust-transform/param[@name='scale']/keyframeAnimation/keyframe")
    assert len(keys) >= 6  # doux, punch, crash, punch, doux : 2 images-clés chacun
    assert {k.get("value") for k in keys} >= {"1.3 1.3"}  # crash
    assert spine.findall("asset-clip/asset-clip[@lane='-1']/adjust-volume")  # musique
    assert spine.findall("asset-clip/asset-clip[@lane='-2']")  # bruitage « impact »
    for a in root.iter("asset"):
        if a.get("hasVideo") == "0":
            assert a.get("format") is None
    xm = ET.parse(d / "sorties/v2.xml").getroot()
    voix, musique, sfx = xm.findall(".//media/audio/track")
    assert musique.findall("clipitem") and len(sfx.findall("clipitem")) == 1
