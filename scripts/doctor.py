"""Prépare la machine pour monter : Python, ffmpeg, paquets, modèles (Parakeet + Silero, visages), dossiers.

Usage : python doctor.py [--dossier .] [--installer] [--sans-modele] [--modele parakeet-tdt-0.6b-v3]
`--installer` va d'une machine neuve à « prêt » : installe ffmpeg (winget / brew, sinon copie statique dans
~/.cache/monteur/bin), les paquets Python, télécharge les modèles. Code de sortie 0 si tout est prêt, 1 sinon.
"""
import argparse
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

# module importé -> nom pip (lots A/B/C : numpy + cv2 seulement ; torch/DeepFilterNet restent optionnels).
# onnx-asr (MIT) fait tourner Parakeet ; faster-whisper reste requis : repli de transcription + VAD des « euh ».
PAQUETS = {"faster_whisper": "faster-whisper", "numpy": "numpy", "PIL": "Pillow", "cv2": "opencv-python-headless",
           "onnx_asr": "onnx-asr[cpu,hub]", "onnxruntime": "onnxruntime", "huggingface_hub": "huggingface_hub"}
DOSSIERS = ["mes-videos", "rushs", "broll", "musique", "sorties"]
# ses propres noms (A-Roll, B-Roll, Musique…) comptent pour les nôtres, comme illustrer.py
ALIAS = {"rushs": common.RUSHS, "broll": common.BROLL, "musique": common.MUSIQUE}
PARAKEET = "parakeet-tdt-0.6b-v3"  # = transcribe.PARAKEET (pas importé ici : transcribe a besoin de numpy)
MODELE = PARAKEET
REPLI = "large-v3-turbo"
RNNN = Path(__file__).resolve().parent.parent / "assets" / "rnnoise" / "std.rnnn"  # embarqué (débruitage voix)
CACHE = Path.home() / ".cache" / "monteur"
BIN = CACHE / "bin"  # ffmpeg/ffprobe statiques (repli) : common.ffmpeg() doit aussi chercher ici
YUNET = CACHE / "face_detection_yunet_2023mar.onnx"
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")  # licence MIT
WINGET = ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--silent",
          "--accept-source-agreements", "--accept-package-agreements"]
EXE = ".exe" if os.name == "nt" else ""
CLES_ILLUSTRER = Path.home() / ".monteur" / "cles.json"  # = illustrer.CLES


def check_python():
    return sys.version_info >= (3, 10)


def check_ffmpeg():
    """Chemins de ffmpeg/ffprobe tels que les scripts les trouvent, ou None (common.die lève SystemExit)."""
    try:
        return common.ffmpeg(), common.ffprobe()
    except SystemExit:
        return None


def _quiet(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").returncode == 0
    except OSError:
        return False


def _brew():
    for b in (shutil.which("brew"), "/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if b and Path(b).is_file():
            return b
    return None


def copier_binaires(paths, dest=BIN):
    """Copie ffmpeg et ffprobe dans dest (noms standard) ; renvoie True si les deux y sont."""
    dest.mkdir(parents=True, exist_ok=True)
    for p in paths:
        name = "ffprobe" if "ffprobe" in Path(p).name.lower() else "ffmpeg"
        shutil.copy2(p, dest / (name + EXE))
    return all((dest / (n + EXE)).is_file() for n in ("ffmpeg", "ffprobe"))


def installer_ffmpeg():
    """winget (Windows) / brew (Mac), sinon ffmpeg statique via le paquet pip static-ffmpeg (MIT ; binaires
    ffmpeg GPL) copié dans ~/.cache/monteur/bin. Renvoie la méthode qui a marché, ou None."""
    if sys.platform == "win32" and shutil.which("winget"):
        print("Installation de ffmpeg avec winget (1-2 min)...", flush=True)
        _quiet(WINGET)
        if check_ffmpeg():
            return "winget"
    brew = _brew() if sys.platform == "darwin" else None
    if brew:
        print("Installation de ffmpeg avec Homebrew (quelques minutes)...", flush=True)
        _quiet([brew, "install", "ffmpeg"])
        if check_ffmpeg():
            return "brew"
    print("Téléchargement d'un ffmpeg autonome (~80 Mo)...", flush=True)
    if not installer(["static-ffmpeg"]):
        return None
    code = "from static_ffmpeg import run; print('\\n'.join(run.get_or_fetch_platform_executables_else_raise()))"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace")
    paths = [line.strip() for line in r.stdout.splitlines() if line.strip() and Path(line.strip()).is_file()]
    if r.returncode != 0 or len(paths) < 2 or not copier_binaires(paths):
        return None
    return "static"


def paquets_manquants():
    return [pip for mod, pip in PAQUETS.items() if importlib.util.find_spec(mod) is None]


def installer(paquets):
    """pip install ; Python « géré par le système » (Homebrew, PEP 668) → repli --user --break-system-packages."""
    if not _quiet([sys.executable, "-m", "pip", "--version"]):
        _quiet([sys.executable, "-m", "ensurepip", "--upgrade"])
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *paquets]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 and "externally-managed" in (r.stdout + r.stderr):
        r = subprocess.run(cmd + ["--user", "--break-system-packages"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print((r.stderr or r.stdout)[-800:])
    return r.returncode == 0


def telecharger_modele(nom):
    """Télécharge (ou retrouve en cache) le modèle de transcription : Parakeet + VAD Silero (même fonction que
    transcribe.py, donc mêmes dossiers) ou un modèle Whisper. Renvoie (ok, chemin ou message d'erreur)."""
    try:
        if nom == PARAKEET:
            import transcribe
            transcribe.load_parakeet()
            return True, str(CACHE / PARAKEET)
        return True, common.whisper_model_path(nom)
    except Exception as e:  # réseau, disque plein, nom inconnu...
        return False, str(e)


def deja_la(nom):
    """Le modèle est-il déjà sur le disque ? Sans --installer on ne télécharge rien (accord de l'utilisateur)."""
    if nom == "visage":
        return YUNET.is_file() and YUNET.stat().st_size > 100_000
    if nom == PARAKEET:
        return all((CACHE / d / f).is_file() for d, f in ((PARAKEET, "encoder-model.int8.onnx"),
                                                          (PARAKEET, "decoder_joint-model.int8.onnx"),
                                                          ("silero-vad", "silero_vad.onnx")))
    if (CACHE / nom / "model.bin").is_file():
        return True
    try:
        from faster_whisper import download_model
        download_model(nom, local_files_only=True)
        return True
    except Exception:  # absent du cache Hugging Face (ou faster-whisper absent)
        return False


def telecharger_visage():
    """Modèle de visages YuNet (~230 ko, MIT) dans ~/.cache/monteur, vérifié par OpenCV. (ok, chemin ou erreur)."""
    try:
        if not (YUNET.is_file() and YUNET.stat().st_size > 100_000):
            CACHE.mkdir(parents=True, exist_ok=True)
            data = urllib.request.urlopen(YUNET_URL, timeout=60).read()
            if len(data) < 100_000:  # page d'erreur ou pointeur LFS
                return False, "fichier reçu trop petit"
            YUNET.write_bytes(data)
        import cv2
        cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320))
        return True, str(YUNET)
    except Exception as e:  # réseau, OpenCV trop ancien, fichier corrompu
        return False, str(e)


def check_animations(installer_ok):
    """Playwright + un navigateur (Edge/Chrome déjà là, sinon Chromium de Playwright, ~150 Mo)."""
    if importlib.util.find_spec("playwright") is None:
        if not (installer_ok and installer(["playwright"])):
            return False, "Playwright absent -> relancer avec --installer (sinon cartes fixes)"
    code = ("from playwright.sync_api import sync_playwright as s\n"
            "with s() as p:\n"
            "    for c in ('msedge','chrome',None):\n"
            "        try:\n"
            "            (p.chromium.launch(channel=c) if c else p.chromium.launch()).close(); print(c or 'chromium'); break\n"
            "        except Exception: pass\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    nav = (r.stdout or "").strip()
    if not nav and installer_ok:
        _quiet([sys.executable, "-m", "playwright", "install", "chromium"])
        nav = (subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout or "").strip()
    return (True, f"prêtes ({nav})") if nav else (False, "aucun navigateur -> relancer avec --installer")


def etat_illustrations():
    """Illustrations libres (illustrer.py) : sans clé Wikimedia, NASA, Openverse ; une clé ajoute Pexels/Pixabay.
    Lu ici sans importer illustrer (il a besoin de Pillow, peut-être pas encore installé)."""
    try:
        d = json.loads(CLES_ILLUSTRER.read_text(encoding="utf-8")) if CLES_ILLUSTRER.is_file() else {}
    except (OSError, ValueError):
        d = {}
    avec = [n.capitalize() for n in ("pexels", "pixabay") if d.get(n) or os.environ.get(f"{n.upper()}_API_KEY")]
    return "illustrations libres : Wikimedia, NASA, Openverse (sans clé)" + (
        f" + {', '.join(avec)}" if avec else
        " ; vidéos verticales en plus avec une clé gratuite (illustrer.py cle pexels <clé>, pexels.com/api)")


def preparer_dossiers(racine):
    """Crée les dossiers manquants ; renvoie (créés, {dossier: (son vrai nom, nombre de fichiers)})."""
    racine = Path(racine)
    crees, contenu = [], {}
    # il a déjà ses propres dossiers (A-Roll, B-Roll, Export…) : on n'en ajoute pas d'autres, vides
    siens = racine.is_dir() and any(f.is_dir() and not f.name.startswith(".") and f.name not in DOSSIERS + ["ma-da"]
                                    for f in racine.iterdir())
    for d in DOSSIERS:
        p = common.dossier(racine, ALIAS.get(d, (d,)))
        if not p.exists() and not siens:
            p.mkdir(parents=True)
            crees.append(d)
        contenu[d] = p.name, sum(1 for f in p.iterdir() if f.is_file() and not f.name.startswith(".")) if p.is_dir() else 0
    return crees, contenu


def main(argv=None):
    ap = argparse.ArgumentParser(description="Vérifie et prépare l'installation du monteur.")
    ap.add_argument("--dossier", default=".", help="dossier de travail de l'utilisateur (défaut : dossier courant)")
    ap.add_argument("--installer", action="store_true",
                    help="installe tout ce qui manque : ffmpeg, paquets Python, modèles (avec accord de l'utilisateur)")
    ap.add_argument("--sans-modele", action="store_true", help="ne pas télécharger les modèles")
    ap.add_argument("--modele", default=MODELE, help=f"modèle de transcription (défaut : {MODELE} ; ou un modèle "
                    "Whisper : large-v3-turbo, small)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ok = True
    lignes = []

    def ligne(bon, texte):
        lignes.append(("[OK] " if bon else "[!!] ") + texte)

    v = ".".join(map(str, sys.version_info[:3]))
    if check_python():
        ligne(True, f"Python {v}")
    else:
        ok = False
        ligne(False, f"Python {v} trop ancien : il faut 3.10 ou plus (python.org)")

    ff = check_ffmpeg()
    methode = installer_ffmpeg() if not ff and a.installer else None
    ff = ff or (check_ffmpeg() if methode else None)
    if ff:
        ligne(True, f"ffmpeg : {ff[0]}" + (f" (installé : {methode})" if methode else ""))
    elif methode == "static":
        ok = False
        ligne(False, f"ffmpeg installé dans {BIN} mais pas encore trouvé par les scripts : "
                     "mets à jour le module monteur (ou ajoute ce dossier au PATH)")
    else:
        ok = False
        ligne(False, ("installation automatique de ffmpeg impossible (réseau ?). " if a.installer else
                      "ffmpeg absent -> relancer avec --installer. ")
              + "À la main : Windows « winget install Gyan.FFmpeg », Mac « brew install ffmpeg », "
                "puis fermer et rouvrir Claude")

    manquants = paquets_manquants()
    if manquants and a.installer:
        print(f"Installation de : {', '.join(manquants)} ...", flush=True)
        installer(manquants)
        importlib.invalidate_caches()
        manquants = paquets_manquants()
    if manquants:
        ok = False
        ligne(False, f"paquets Python manquants : {', '.join(manquants)} -> relancer avec --installer")
    else:
        ligne(True, "paquets Python : " + ", ".join(PAQUETS.values()))

    if a.sans_modele:
        ligne(True, "modèles : vérification ignorée (--sans-modele)")
    else:
        besoin = "onnx-asr[cpu,hub]" if a.modele == PARAKEET else "faster-whisper"
        if besoin in manquants:
            ok = False
            ligne(False, f"modèle de transcription : impossible sans {besoin}")
        else:
            taille = "~670 Mo" if a.modele == PARAKEET else "~1,6 Go"
            if deja_la(a.modele):  # déjà sur le disque : ne pas le charger (~750 Mo de mémoire pour rien)
                bon, info = True, a.modele
            elif a.installer:
                print(f"Modèle de transcription {a.modele} ({taille} à télécharger, une seule fois) ...", flush=True)
                bon, info = telecharger_modele(a.modele)
            else:
                bon, info = False, f"à télécharger ({taille}) -> relancer avec --installer"
            if bon:
                ligne(True, f"modèle de transcription {a.modele} prêt (+ détecteur de parole Silero)"
                      if a.modele == PARAKEET else f"modèle Whisper {a.modele} prêt")
            elif a.installer and a.modele == PARAKEET and "faster-whisper" not in manquants:
                print(f"Parakeet indisponible ({info}) : repli Whisper {REPLI} (~1,6 Go) ...", flush=True)
                bon, info2 = telecharger_modele(REPLI)
                ligne(bon, f"Parakeet non téléchargé ({info}) : repli Whisper {REPLI} prêt (un peu plus lent)" if bon
                      else f"modèle de transcription non téléchargé : {info} / {info2}")
            else:
                ligne(False, f"modèle de transcription non téléchargé : {info}")
            ok = ok and bon
        if "opencv-python-headless" in manquants:
            ok = False
            ligne(False, "modèle de visages : impossible sans opencv")
        else:
            bon, info = telecharger_visage() if a.installer or deja_la("visage") else                 (False, "à télécharger (~230 ko) -> relancer avec --installer")
            ok = ok and bon
            ligne(bon, "modèle de visages (zooms et recadrage sur le visage) prêt" if bon
                  else f"modèle de visages non téléchargé : {info}")

    if RNNN.is_file() and RNNN.stat().st_size > 100_000:
        ligne(True, "débruitage de la voix (RNNoise) prêt")
    else:
        ok = False
        ligne(False, f"fichier du débruitage absent ({RNNN}) : réinstalle le module monteur (/plugin)")

    # animations (anim.py) : facultatives — sans elles, cartes fixes (carte.py) ; donc pas bloquant pour `ok`
    bon, info = check_animations(a.installer)
    ligne(bon, f"animations (cartons, citations, logos animés) : {info}")

    ligne(True, etat_illustrations())  # facultatives : jamais bloquant

    r = common.ram_libre_go()
    if r is not None:
        ligne(True, f"mémoire libre : {r:.1f} Go" + (" → mode économe (un calcul à la fois, montage plus lent ; "
              "fermer des applications accélère)" if common.ECONOME else ""))

    crees, contenu = preparer_dossiers(a.dossier)
    if crees:
        ligne(True, "dossiers créés : " + ", ".join(crees))
    ligne(True, "dossiers : " + ", ".join(f"{nom}/ ({n})" for nom, n in contenu.values()))
    if 0 < contenu["mes-videos"][1] < 5:
        ligne(True, f"mes-videos/ : {contenu['mes-videos'][1]} vidéo(s) ; 5 à 10 donnent une DA plus fidèle")

    print("\n".join(["", "Vérification du monteur :", *lignes, ""]))
    print("Tout est prêt." if ok else "Pas encore prêt : corrige les lignes [!!] ci-dessus "
          "(le plus simple : python doctor.py --installer).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
