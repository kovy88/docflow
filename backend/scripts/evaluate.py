"""Run the extraction pipeline over the labelled dataset and score it.

    uv run python scripts/evaluate.py                      # local model from .env
    uv run python scripts/evaluate.py --model qwen3.6:35b-a3b --limit 5
    uv run python scripts/evaluate.py --provider openai --model gpt-5.4-mini

Results are written after every document to evals/<run>.json (so a long local run can be
inspected or resumed) and a Markdown summary to evals/<run>.md.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.evaluation import markdown, score_document, summarize  # noqa: E402
from app.extractor import (  # noqa: E402
    PROMPT_VERSION,
    Extractor,
    OpenAIExtractor,
    ToolCallExtractor,
)
from app.pdf import read_pdf  # noqa: E402
from app.pipeline import run_extraction  # noqa: E402
from app.templates import load_templates  # noqa: E402

DATASET = ROOT / "dataset"
EVALS = ROOT / "evals"


def make_extractor(provider: str, model: str | None) -> Extractor:
    if provider == "openai":
        if not settings.openai_api_key:
            sys.exit("OPENAI_API_KEY is not set")
        return OpenAIExtractor(
            settings.openai_api_key, model or settings.openai_model, settings.llm_timeout_s
        )
    return ToolCallExtractor(
        settings.ollama_base_url, model or settings.ollama_model, settings.llm_timeout_s
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--provider", choices=["ollama", "openai"], default=settings.llm_provider)
    parser.add_argument("--model")
    parser.add_argument("--limit", type=int, help="only the first N documents")
    parser.add_argument("--only", help="substring filter on file names, e.g. 'invoice'")
    parser.add_argument("--difficulty", choices=["standard", "hard", "scan", "real"])
    parser.add_argument(
        "--dataset", type=Path, default=DATASET, help="folder with labels.json and the PDFs"
    )
    parser.add_argument("--out", type=Path, default=EVALS, help="folder for the results")
    parser.add_argument(
        "--resume", type=Path, help="continue evals/<run>.json; failed documents are retried"
    )
    args = parser.parse_args()

    extractor = make_extractor(args.provider, args.model)
    labels = json.loads((args.dataset / "labels.json").read_text())
    if args.only:
        labels = [x for x in labels if args.only in x["file"]]
    if args.difficulty:
        labels = [x for x in labels if x.get("difficulty", "standard") == args.difficulty]
    labels = labels[: args.limit]

    args.out.mkdir(exist_ok=True)
    if args.resume:
        path = args.resume
        run = json.loads(path.read_text())
        if run.get("prompt_version", 1) != PROMPT_VERSION:
            sys.exit("Run used a different prompt version; start a new run instead of resuming.")
    else:
        slug = re.sub(r"[^a-z0-9.]+", "-", extractor.model.lower()).strip("-")
        path = args.out / f"{datetime.now():%Y%m%d-%H%M}-{slug}-p{PROMPT_VERSION}.json"
        run = {
            "model": extractor.model,
            "provider": args.provider,
            "prompt_version": PROMPT_VERSION,
            "started": datetime.now().isoformat(),
            "documents": [],
        }
    # On resume, failed documents are run again.
    run["documents"] = [d for d in run["documents"] if not d.get("error")]
    done = {d["file"] for d in run["documents"]}

    for i, label in enumerate(labels, 1):
        if label["file"] in done:
            continue
        template = load_templates()[label["template_id"]]
        started = time.perf_counter()  # includes OCR of scanned documents
        pdf = read_pdf((args.dataset / label["file"]).read_bytes())
        try:
            result = run_extraction(pdf.pages, template, extractor, pdf.ocr_pages)
            doc = score_document(label, result) | {"usage": result.get("usage", {})}
        except Exception as exc:  # keep going; a failed document is a result too
            doc = {"file": label["file"], "template_id": label["template_id"], "error": repr(exc)}
        doc["latency_s"] = round(time.perf_counter() - started, 1)
        run["documents"].append(doc)
        correct = sum(r["correct"] for r in doc.get("fields", []))
        status = (
            doc.get("error")
            or f"{correct}/{len(doc['fields'])} fields, rules {doc['rules']['predicted']}"
        )
        print(f"[{i}/{len(labels)}] {label['file']}: {status} ({doc['latency_s']} s)", flush=True)
        path.write_text(json.dumps(run, ensure_ascii=False, indent=1, default=str))

    run["summary"] = summarize(run["documents"])
    run["finished"] = datetime.now().isoformat()
    path.write_text(json.dumps(run, ensure_ascii=False, indent=1, default=str))
    title = f"{run['model']} ({run['provider']}, prompt v{run.get('prompt_version', 1)})"
    report = markdown(run["summary"], title)
    path.with_suffix(".md").write_text(report)
    print("\n" + report)


if __name__ == "__main__":
    main()
