"""Check synthetic byte expectations using stdlib only, never Requests code."""
import base64
import json
from pathlib import Path


def _field(part, mode):
    if part["kind"] == "bytes":
        return bytes.fromhex(part["hex"])
    value = part["value"]
    if mode == "per_field_fallback":
        try:
            return value.encode("latin1")
        except UnicodeEncodeError:
            return value.encode("utf-8")
    return value.encode(mode)


def _reference(case, mode):
    try:
        raw = _field(case["username"], mode) + b":" + _field(case["password"], mode)
    except UnicodeEncodeError:
        return {"outcome": "UnicodeEncodeError"}
    return {"outcome": "header", "userpass_hex": raw.hex(),
            "authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def verify_specification(spec):
    """Reference models validate the specification, not the library or patch."""
    rows = []
    for case in spec["cases"]:
        legacy = _reference(case, "latin1")
        fallback = _reference(case, "per_field_fallback")
        utf8 = _reference(case, "utf-8")
        both_strings = all(case[field]["kind"] == "str" for field in ("username", "password"))
        fallback_changed_failure = legacy["outcome"] == "UnicodeEncodeError" and fallback["outcome"] == "header"
        rows.append({"id": case["id"], "specified_expectation_matches_reference": legacy == case["legacy_expected_by_static_inference"],
                     "legacy_reference": legacy, "unconditional_utf8_reference": utf8,
                     "per_field_fallback_reference": fallback,
                     "unconditional_utf8_changes_existing_header": legacy["outcome"] == "header" and legacy != utf8,
                     "fallback_changes_exception_to_header": fallback_changed_failure,
                     "fallback_differs_from_uniform_utf8_for_string_pair": both_strings and fallback_changed_failure and fallback != utf8})
    return {"status": "specification_consistent" if all(row["specified_expectation_matches_reference"] for row in rows) else "specification_error",
            "case_count": len(rows), "source_revision": spec["revision"],
            "repository_tests_executed": False, "candidate_patch_executed": False,
            "repository_execution_enabled": False, "patch_approved": False,
            "basis": "Backend-authored stdlib reference models with synthetic credentials; not an observation of Requests runtime behavior",
            "cases": rows}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=Path(__file__).with_name("auth_compatibility_spec.json"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = verify_specification(json.loads(args.spec.read_text(encoding="utf-8")))
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, indent=2))
    raise SystemExit(0 if result["status"] == "specification_consistent" else 1)
