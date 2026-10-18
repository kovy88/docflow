"""Precompute extractions for the public read-only demo (frontend with NEXT_PUBLIC_DEMO=1).

    uv run python scripts/export_demo.py --model qwen3.6:35b-a3b

Runs the real pipeline over every dataset document and writes frontend/public/demo/data.json
plus copies of the PDFs. Results are saved after each document; rerunning skips finished
ones (use --fresh to start over)."""

import argparse
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.extractor import ExtractionOutput, FieldAnswer, ToolCallExtractor  # noqa: E402
from app.pdf import read_pdf  # noqa: E402
from app.pipeline import run_extraction  # noqa: E402
from app.templates import load_templates  # noqa: E402

DATASET = ROOT / "dataset"
EVALS = ROOT / "evals"
OUT = ROOT.parent / "frontend" / "public" / "demo"


class Replay:
    """Extractor that returns a stored extraction's model answers, so checks can be re-run
    without calling the model again."""

    def __init__(self, extraction: dict):
        self.model = extraction["model"]
        self.extraction = extraction

    def extract(self, pages, template):
        answers = {
            f["key"]: FieldAnswer(
                value=f["raw"], page=f["page"], quote=f["quote"], confidence=f["confidence"]
            )
            for f in self.extraction["fields"]
        }
        return ExtractionOutput(answers=answers, usage=self.extraction["usage"])


def eval_runs() -> list[dict]:
    runs = []
    for path in sorted(EVALS.glob("*.json"), reverse=True):
        run = json.loads(path.read_text())
        if "summary" in run:
            keep = ("model", "provider", "started", "finished", "summary")
            meta = {"id": path.stem, "prompt_version": run.get("prompt_version", 1)}
            runs.append(meta | {k: run.get(k) for k in keep})
    return runs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=settings.ollama_model)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument(
        "--regrade",
        action="store_true",
        help="re-run checks on stored model answers (after a change to grounding/rules)",
    )
    args = parser.parse_args()

    (OUT / "pdfs").mkdir(parents=True, exist_ok=True)
    path = OUT / "data.json"
    data = json.loads(path.read_text()) if path.exists() and not args.fresh else {}
    done = {e["document_id"] for es in data.get("extractions", {}).values() for e in es}
    labels = json.loads((DATASET / "labels.json").read_text())
    extractor = ToolCallExtractor(settings.ollama_base_url, args.model, settings.llm_timeout_s)

    documents, extractions = [], data.get("extractions", {})
    for doc_id, label in enumerate(labels, 1):
        pdf_bytes = (DATASET / label["file"]).read_bytes()
        shutil.copyfile(DATASET / label["file"], OUT / "pdfs" / f"{doc_id}.pdf")
        pdf = read_pdf(pdf_bytes)
        documents.append(
            {
                "id": doc_id,
                "filename": label["file"],
                "page_count": len(pdf.pages),
                "has_text": any(pdf.pages),
                "ocr_pages": pdf.ocr_pages,
            }
        )
        if doc_id in done and args.regrade:
            stored = extractions[str(doc_id)][0]
            replay = Replay(stored)
            template = load_templates()[label["template_id"]]
            result = run_extraction(pdf.pages, template, replay, pdf.ocr_pages)
            stored |= {k: result[k] for k in ("status", "fields", "issues")}
            continue
        if doc_id in done:
            continue
        template = load_templates()[label["template_id"]]
        started = time.perf_counter()
        result = run_extraction(pdf.pages, template, extractor, pdf.ocr_pages)
        result["usage"] = result["usage"] | {"latency_s": round(time.perf_counter() - started, 1)}
        extractions[str(doc_id)] = [
            {
                "id": doc_id,
                "document_id": doc_id,
                "template_id": template.id,
                "template": template.model_dump(),
                "model": extractor.model,
                "created_at": datetime.now(UTC).isoformat(),
                "reviewed_at": None,
                **result,
            }
        ]
        print(
            f"[{doc_id}/{len(labels)}] {label['file']}: {result['status']} ({result['usage']['latency_s']} s)",
            flush=True,
        )
        data["extractions"] = extractions
        path.write_text(json.dumps(data, ensure_ascii=False))

    data |= {
        "documents": documents,
        "extractions": extractions,
        "templates": [t.model_dump() | {"builtin": True} for t in load_templates().values()],
        "demo": [
            {"file": x["file"], "template_id": x["template_id"], "description": x["description"]}
            for x in labels
        ],
        "evals": eval_runs(),
        "model": args.model,
    }
    path.write_text(json.dumps(data, ensure_ascii=False))
    print(f"Wrote {path} ({path.stat().st_size // 1024} kB)")


if __name__ == "__main__":
    main()
