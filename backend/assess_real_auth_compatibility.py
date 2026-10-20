"""Compare measured baseline with candidate reference models; never patch source."""
import json
from pathlib import Path
from check_auth_compatibility_spec import verify_specification
from real_requests_smoke import ARTIFACT, compatibility_spec


def assess(measurement):
    if (measurement['status'] != 'real_requests_smoke_passed'
            or measurement['artifact_sha256'] != ARTIFACT
            or measurement['source_executed'] is not True):
        raise ValueError('Expected the pinned real-source observation')
    references = verify_specification(compatibility_spec())
    measured = {r['id']: r for r in measurement['result']['compatibility_matrix']}
    if len(measured) != references['case_count']:
        raise ValueError('Incomplete observation')
    for row in references['cases']:
        if measured[row['id']]['observed'] != row['legacy_reference']:
            raise ValueError('Measured baseline differs from reference')
    return dict(status='compatibility_assessed', baseline_cases_observed=len(measured),
                unconditional_utf8_changes_existing_headers=[r['id'] for r in references['cases'] if r['unconditional_utf8_changes_existing_header']],
                per_field_fallback_mixed_encoding_cases=[r['id'] for r in references['cases'] if r['fallback_differs_from_uniform_utf8_for_string_pair']],
                decision='retain_default_latin1_and_opaque_bytes',
                proposed_fallback_approved=False, candidate_patch_executed=False,
                candidate_comparison_basis='backend reference models, not patched Requests execution',
                next_supported_direction='explicit caller-selected UTF-8 bytes when the target server encoding is known; no automatic fallback',
                production_repository_executor_enabled=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--measurement', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = assess(json.loads(args.measurement.read_text()))
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
