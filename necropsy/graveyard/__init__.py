"""The curated graveyard: the manifest, the committed fixtures, and the loader.

The demo wall is built from committed data and renders with the network
unplugged and no keys set (R-5, P4). `specimens.py` documents exactly what that
committed data is and is not.
"""

from necropsy.graveyard.manifest import Manifest, ManifestEntry, ManifestError
from necropsy.graveyard.manifest import load as load_manifest
from necropsy.graveyard.seed import SeedReport, seed, write_specimen_fixtures

__all__ = [
    "Manifest",
    "ManifestEntry",
    "ManifestError",
    "SeedReport",
    "load_manifest",
    "seed",
    "write_specimen_fixtures",
]
