from copy import deepcopy

from app.templates import load_templates
from scripts.export_demo import cached_result_matches_template


def test_demo_cache_reuses_only_matching_template():
    template = load_templates()["lease"]
    stored = {"template": template.model_dump()}
    assert cached_result_matches_template(stored, template)

    # The public lease demo used to keep 11 fields after the live template grew to 12.
    previous = deepcopy(stored)
    previous["template"]["fields"] = [
        field for field in previous["template"]["fields"] if field["key"] != "total_payment"
    ]
    assert not cached_result_matches_template(previous, template)


def test_demo_cache_invalidates_changed_instructions_or_rules():
    template = load_templates()["lease"]
    for key, value in [("description", "Starší pokyny"), ("rules", [])]:
        stored = {"template": template.model_dump()}
        stored["template"][key] = value
        assert not cached_result_matches_template(stored, template)
    assert not cached_result_matches_template({}, template)
