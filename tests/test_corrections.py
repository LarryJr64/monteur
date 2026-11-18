import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import corrections  # noqa: E402


def test_journal_et_repetitions(tmp_path):
    corrections.ajoute(tmp_path, "zooms.par_minute", "-", "trop de zooms", video="reel-1")
    corrections.ajoute(tmp_path, "zooms.par_minute", "−", "encore trop de zooms", video="reel-1")  # même vidéo
    assert corrections.repetitions(corrections.lire(tmp_path)) == []  # une seule vidéo : pas encore une habitude
    corrections.ajoute(tmp_path, "Zooms.par_minute", "-", "moins de zooms", video="reel-2", projet="autre")
    corrections.ajoute(tmp_path, "zooms.par_minute", "+", "plus de zooms", video="reel-3")  # autre sens : à part
    corrections.ajoute(tmp_path, "audio.musique_db_sous_voix", "-", "musique trop forte", video="reel-1")
    corrections.ajoute(tmp_path, "audio.musique_db_sous_voix", "-", "musique trop forte", "toujours", video="reel-2")
    (tmp_path / "ma-da/corrections.jsonl").open("a", encoding="utf-8").write("ligne abîmée\n")
    entries = corrections.lire(tmp_path)
    assert len(entries) == 6 and entries[0]["projet"] == tmp_path.name and entries[1]["sens"] == "-"
    reps = corrections.repetitions(entries)
    # musique : déjà une règle durable → ne plus la proposer
    assert reps == [("zooms.par_minute", "-", 2, ["trop de zooms", "encore trop de zooms", "moins de zooms"])]


def test_cli(tmp_path):
    def run(*a):
        r = subprocess.run([sys.executable, str(ROOT / "scripts/corrections.py"), *a, "--root", str(tmp_path)],
                           capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, r.stderr
        return r.stdout
    run("ajoute", "--categorie", "broll.part_pct", "--sens", "+", "--texte", "plus de b-roll", "--video", "a")
    assert "Aucune correction répétée" in run("bilan")
    run("ajoute", "--categorie", "broll.part_pct", "--sens", "+", "--texte", "pas assez d'images", "--video", "b")
    out = run("bilan")
    assert "À PROPOSER : broll.part_pct + sur 2 vidéos" in out and "pas assez d'images" in out
    e = json.loads((tmp_path / "ma-da/corrections.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert e["portee"] == "video" and e["source"] == "corriger" and e["video"] == "a"
