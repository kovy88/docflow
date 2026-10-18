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
from app.extractor import Extractor, OpenAIExtractor, ToolCallExtractor  # noqa: E402
from app.pdf import extract_pages  # noqa: E402
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
    parser.add_argument("--resume", type=Path, help="continue a previous evals/<run>.json")
    args = parser.parse_args()

    extractor = make_extractor(args.provider, args.model)
    labels = json.loads((DATASET / "labels.json").read_text())
    if args.only:
        labels = [x for x in labels if args.only in x["file"]]
    labels = labels[: args.limit]

    EVALS.mkdir(exist_ok=True)
    if args.resume:
        path = args.resume
        run = json.loads(path.read_text())
    else:
        slug = re.sub(r"[^a-z0-9.]+", "-", extractor.model.lower()).strip("-")
        path = EVALS / f"{datetime.now():%Y%m%d-%H%M}-{slug}.json"
        run = {
            "model": extractor.model,
            "provider": args.provider,
            "started": datetime.now().isoformat(),
            "documents": [],
        }
    done = {d["file"] for d in run["documents"]}

    for i, label in enumerate(labels, 1):
        if label["file"] in done:
            continue
        template = load_templates()[label["template_id"]]
        pages = extract_pages((DATASET / label["file"]).read_bytes())
        started = time.perf_counter()
        try:
            result = run_extraction(pages, template, extractor)
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
    report = markdown(run["summary"], f"{run['model']} ({run['provider']})")
    path.with_suffix(".md").write_text(report)
    print("\n" + report)


if __name__ == "__main__":
    main()
