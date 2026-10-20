"""Repeatable trusted acceptance suite; no provider calls or repository execution."""
import json
from pathlib import Path
import unittest

SUITES=['test_mvp_app','test_report_job_service','test_real_worker_ownership',
        'test_managed_probe_jobs','test_patch_validator','test_safe_patching','test_clone_safety',
        'test_source_artifact','test_artifact_materializer','test_real_requests_smoke',
        'test_real_auth_assessment','test_itsdangerous_exercise','test_dependency_and_provenance']


def verify(destination):
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in SUITES)
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    report=dict(status='passed' if result.wasSuccessful() else 'failed',tests_run=result.testsRun,
                skipped=[dict(test=str(test),reason=reason) for test,reason in result.skipped],
                failed_tests=[str(test) for test,_ in result.failures],error_tests=[str(test) for test,_ in result.errors],
                suites=SUITES,provider_calls=0,repository_code_executed=False,
                scope='Trusted controller tests and saved-evidence checks; fresh Docker/profile runs are separate.')
    destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return result.wasSuccessful()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,default=Path(__file__).with_name('local_mvp_acceptance_results.json'))
    raise SystemExit(0 if verify(parser.parse_args().report) else 1)
