import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_stroboscope_fusionne_pas_perdu():
    """Flashs de boîte de nuit pris pour des coupes toutes les 0,2 s : fusionnés en plans utilisables, la fin
    trop courte (amorce du plan suivant) écartée."""
    import plans
    assert plans.fusion_courts([0, 0.12, 0.32, 0.52, 0.72, 0.92, 1.12, 1.32, 1.84, 2.68, 2.7], 0.6) == [0, 0.72, 1.32, 2.68]
    assert plans.fusion_courts([0, 0.3], 0.6) == [0, 0.3]  # clip entier trop court : gardé tel quel
