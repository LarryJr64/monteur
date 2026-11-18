import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import doctor  # noqa: E402


def _ok_env(monkeypatch):
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(doctor, "paquets_manquants", lambda: [])
    monkeypatch.setattr(doctor, "telecharger_modele", lambda nom: (True, "/cache/" + nom))
    monkeypatch.setattr(doctor, "telecharger_visage", lambda: (True, "/cache/yunet.onnx"))
    monkeypatch.setattr(doctor, "deja_la", lambda nom: True)


def test_cree_les_dossiers_et_compte(tmp_path):
    (tmp_path / "rushs").mkdir()
    (tmp_path / "rushs" / "a.mp4").write_bytes(b"x")
    (tmp_path / "rushs" / ".DS_Store").write_bytes(b"x")
    crees, contenu = doctor.preparer_dossiers(tmp_path)
    assert "rushs" not in crees and "mes-videos" in crees
    assert all((tmp_path / d).is_dir() for d in doctor.DOSSIERS)
    assert contenu["rushs"] == ("rushs", 1) and contenu["sorties"] == ("sorties", 0)


def test_tout_pret(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    assert doctor.main(["--dossier", str(tmp_path)]) == 0
    assert "Tout est prêt" in capsys.readouterr().out


def test_ffmpeg_absent(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(doctor, "installer_ffmpeg", lambda: (_ for _ in ()).throw(AssertionError))
    assert doctor.main(["--dossier", str(tmp_path)]) == 1  # sans --installer : on n'installe rien
    out = capsys.readouterr().out
    assert "winget install Gyan.FFmpeg" in out and "--installer" in out


def test_installe_ffmpeg_avec_accord(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    etat = {"ff": None}
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: etat["ff"])
    monkeypatch.setattr(doctor, "installer_ffmpeg", lambda: etat.update(ff=("/x/ffmpeg", "/x/ffprobe")) or "winget")
    assert doctor.main(["--dossier", str(tmp_path), "--installer"]) == 0
    assert "installé : winget" in capsys.readouterr().out


def test_ffmpeg_statique_pas_encore_vu_par_common(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(doctor, "installer_ffmpeg", lambda: "static")
    assert doctor.main(["--dossier", str(tmp_path), "--installer"]) == 1
    assert ".cache" in capsys.readouterr().out


def test_copier_binaires(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    exe = doctor.EXE
    for n in ("ffmpeg", "ffprobe"):
        (src / (n + exe)).write_bytes(b"bin")
    assert doctor.copier_binaires([src / ("ffmpeg" + exe), src / ("ffprobe" + exe)], tmp_path / "bin")
    assert (tmp_path / "bin" / ("ffprobe" + exe)).read_bytes() == b"bin"
    assert not doctor.copier_binaires([src / ("ffmpeg" + exe)], tmp_path / "bin2")


def test_paquet_manquant_sans_accord_n_installe_pas(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    monkeypatch.setattr(doctor, "paquets_manquants", lambda: ["faster-whisper"])
    appels = []
    monkeypatch.setattr(doctor, "installer", lambda p: appels.append(p))
    assert doctor.main(["--dossier", str(tmp_path)]) == 1
    assert appels == []
    out = capsys.readouterr().out
    assert "--installer" in out and "faster-whisper" in out


def test_installer_avec_accord(monkeypatch, tmp_path):
    _ok_env(monkeypatch)
    etat = {"manque": ["numpy"]}
    monkeypatch.setattr(doctor, "paquets_manquants", lambda: list(etat["manque"]))
    monkeypatch.setattr(doctor, "installer", lambda p: etat.update(manque=[]) or True)
    assert doctor.main(["--dossier", str(tmp_path), "--installer"]) == 0


def test_echec_modele(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    monkeypatch.setattr(doctor, "deja_la", lambda nom: nom == "visage")  # modèle de transcription absent
    monkeypatch.setattr(doctor, "telecharger_modele", lambda nom: (False, "pas de réseau"))
    assert doctor.main(["--dossier", str(tmp_path), "--installer", "--modele", "small"]) == 1
    assert "pas de réseau" in capsys.readouterr().out


def test_modele_present_pas_charge(monkeypatch, tmp_path):
    """Modèle déjà sur le disque : la vérification ne le charge pas (~750 Mo de mémoire pour rien)."""
    _ok_env(monkeypatch)
    monkeypatch.setattr(doctor, "telecharger_modele", lambda nom: (_ for _ in ()).throw(AssertionError))
    assert doctor.main(["--dossier", str(tmp_path)]) == 0


def test_echec_modele_visage(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    monkeypatch.setattr(doctor, "telecharger_visage", lambda: (False, "hors ligne"))
    assert doctor.main(["--dossier", str(tmp_path)]) == 1
    assert "modèle de visages non téléchargé : hors ligne" in capsys.readouterr().out


def test_sans_modele(monkeypatch, tmp_path):
    _ok_env(monkeypatch)
    monkeypatch.setattr(doctor, "telecharger_modele", lambda nom: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(doctor, "telecharger_visage", lambda: (_ for _ in ()).throw(AssertionError))
    assert doctor.main(["--dossier", str(tmp_path), "--sans-modele"]) == 0


def test_vraie_machine_ne_plante_pas(tmp_path):
    # vrai ffmpeg/paquets de la machine, sans réseau
    assert doctor.main(["--dossier", str(tmp_path), "--sans-modele"]) in (0, 1)


def test_parakeet_par_defaut_repli_whisper(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    appels = []

    def tel(nom):
        appels.append(nom)
        return (False, "hub injoignable") if nom == doctor.PARAKEET else (True, "/cache/" + nom)
    monkeypatch.setattr(doctor, "telecharger_modele", tel)
    monkeypatch.setattr(doctor, "deja_la", lambda nom: nom == "visage")  # transcription pas encore téléchargée
    assert doctor.main(["--dossier", str(tmp_path)]) == 1  # sans --installer : rien téléchargé, pas de repli
    assert appels == []
    assert doctor.main(["--dossier", str(tmp_path), "--installer"]) == 0
    assert appels == [doctor.PARAKEET, doctor.REPLI]
    assert "repli Whisper" in capsys.readouterr().out


def test_onnx_asr_dans_les_paquets():
    assert "onnx_asr" in doctor.PAQUETS and "cv2" in doctor.PAQUETS


def test_rnnoise_absent(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    monkeypatch.setattr(doctor, "RNNN", tmp_path / "absent.rnnn")
    assert doctor.main(["--dossier", str(tmp_path)]) == 1
    assert "débruitage absent" in capsys.readouterr().out


def test_sans_installer_ne_telecharge_rien(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    monkeypatch.setattr(doctor, "deja_la", lambda nom: False)
    monkeypatch.setattr(doctor, "telecharger_modele", lambda nom: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(doctor, "telecharger_visage", lambda: (_ for _ in ()).throw(AssertionError))
    assert doctor.main(["--dossier", str(tmp_path)]) == 1
    assert capsys.readouterr().out.count("relancer avec --installer") >= 2


def test_illustrations_libres_non_bloquant(monkeypatch, tmp_path, capsys):
    _ok_env(monkeypatch)
    for n in ("PEXELS_API_KEY", "PIXABAY_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(doctor, "CLES_ILLUSTRER", tmp_path / "absent.json")
    assert doctor.main(["--dossier", str(tmp_path)]) == 0  # sans clé : prêt quand même
    out = capsys.readouterr().out
    assert "Wikimedia, NASA, Openverse" in out and "clé gratuite" in out
    (tmp_path / "cles.json").write_text('{"pexels": "x"}', encoding="utf-8")
    monkeypatch.setattr(doctor, "CLES_ILLUSTRER", tmp_path / "cles.json")
    assert "+ Pexels" in doctor.etat_illustrations() and "clé gratuite" not in doctor.etat_illustrations()


def test_ses_propres_dossiers_pas_de_dossiers_vides_en_plus(tmp_path):
    (tmp_path / "A-Roll").mkdir()
    (tmp_path / "A-Roll" / "a.mov").write_bytes(b"x")
    (tmp_path / "B-Roll").mkdir()
    crees, contenu = doctor.preparer_dossiers(tmp_path)
    assert crees == [] and not (tmp_path / "rushs").exists()
    assert contenu["rushs"] == ("A-Roll", 1) and contenu["broll"] == ("B-Roll", 0)  # ses noms, comme illustrer.py
