from app.evaluation import markdown, score_document, summarize, values_match


def test_values_match():
    assert values_match("number", 12100.0, 12100.004)
    assert not values_match("number", 12100.0, 12010.0)
    assert values_match("string", "Modrá Kancelář s.r.o.", "  modrá kancelář s.r.o. ")
    assert values_match("string", "27489612", "2748 9612")
    assert values_match(
        "string",
        "rekonstrukce kancelářských prostor ve 2. nadzemním podlaží",
        "dílo spočívající v rekonstrukce kancelářských prostor ve 2. nadzemním podlaží",
    )
    assert values_match("date", None, None)
    assert not values_match("date", None, "2026-01-01")
    assert not values_match("string", "CZK", None)


LABEL = {
    "file": "x.pdf",
    "template_id": "invoice",
    "expected": {"total_amount": 100.0, "currency": "CZK", "bank_account": None},
    "expected_issues": ["sum_mismatch"],
}


def result(total, issues, grounded=True, confidence=0.95):
    return {
        "status": "needs_review",
        "fields": [
            {
                "key": "total_amount",
                "type": "number",
                "value": total,
                "raw": str(total),
                "grounded": grounded,
                "confidence": confidence,
            },
            {
                "key": "currency",
                "type": "string",
                "value": "CZK",
                "raw": "CZK",
                "grounded": True,
                "confidence": 0.99,
            },
            {
                "key": "bank_account",
                "type": "string",
                "value": None,
                "raw": None,
                "grounded": False,
                "confidence": 0,
            },
        ],
        "issues": [{"code": c, "severity": "error", "fields": [], "message": ""} for c in issues],
    }


def test_score_and_summarize():
    good = score_document(LABEL, result(100.0, ["sum_mismatch"])) | {
        "latency_s": 2.0,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    bad = score_document(LABEL, result(90.0, ["date_order"], grounded=False, confidence=0.5)) | {
        "latency_s": 4.0
    }
    failed = {"file": "y.pdf", "template_id": "invoice", "error": "boom", "latency_s": 1.0}

    s = summarize([good, bad, failed])
    assert s["failed_documents"] == 1
    assert s["field_accuracy"] == round(5 / 6, 4)
    assert s["documents_fully_correct"] == 1
    assert s["rule_detection"] == {"tp": 1, "fp": 1, "fn": 1, "precision": 0.5, "recall": 0.5}
    assert s["signals"]["accuracy_ungrounded"] == 0.0
    assert s["signals"]["accuracy_low_confidence"] == 0.0
    assert s["latency_s"]["median"] == 3.0
    assert s["errors"] == [
        {
            "file": "x.pdf",
            "key": "total_amount",
            "expected": 100.0,
            "predicted": 90.0,
            "raw": "90.0",
        }
    ]
    assert "Přesnost polí" in markdown(s, "test")
