"""Public fictional examples; the lease walkthrough is not part of measured evaluations."""

import json
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEASE_FILE = "najem_pilot.pdf"


@cache
def load_demo_labels() -> list[dict]:
    path = ROOT / "dataset/labels.json"
    labels = json.loads(path.read_text()) if path.exists() else []
    if (ROOT / "samples" / LEASE_FILE).exists():
        labels.append(
            {
                "file": LEASE_FILE,
                "template_id": "lease",
                "description": "Nájem bytu, doba určitá; fiktivní ukázka mimo měřenou evaluaci",
            }
        )
    return labels


def demo_path(filename: str) -> Path:
    if filename not in {x["file"] for x in load_demo_labels()}:
        raise ValueError("Unknown demo document")
    return ROOT / ("samples" if filename == LEASE_FILE else "dataset") / filename
