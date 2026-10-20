"""One explicitly authorized Gemini request, then an exact approved-artifact gate."""
import json
from pathlib import Path
from ai_service import generate_patch_with_ai
from patch_validator import prepare_patch_copy, safe_file
from source_artifact import build_source_artifact
from itsdangerous_exercise import BASE, TARGET, ORIGINAL, BROKEN, execute
from isolation_probe import DockerProbe


def check(inputs,docker):
    source=inputs/'source'
    names=sorted(r['path'] for r in json.loads((inputs/'provenance.json').read_text())['files'])
    if build_source_artifact(source,names).sha256!=BASE:raise ValueError('Upstream input changed')
    broken_owner=fixed_owner=None
    try:
        broken_owner,validation=prepare_patch_copy(source,[dict(file=TARGET,original_code=ORIGINAL,replacement_code=BROKEN)],[TARGET])
        if broken_owner is None:raise ValueError('Learning fixture failed')
        broken=Path(broken_owner.name)/'repository'
        issue=('This is an explicitly seeded learning defect, not a real upstream issue. '
               'base64_encode currently returns a constant deliberate-learning-bug instead of URL-safe Base64. '
               'Restore the original behavior with the minimal one-line replacement: return base64.urlsafe_b64encode(string).rstrip(b"="). '
               'Do not change any other code. Preserve bytes and text handling. Return one exact snippet change.')
        # Exactly one request; no retries or extra investigation calls here.
        proposal=generate_patch_with_ai(issue,[dict(file=TARGET,code=safe_file(broken,TARGET).read_text(encoding='utf-8'))])
        fixed_owner,validation=prepare_patch_copy(broken,proposal['changes'],[TARGET])
        report=dict(status='proposal_not_executed',provider_request_count=1,proposal=proposal,validation=validation,
                    scope='Seeded learning exercise; expected repair was supplied in the prompt. This checks integration, not autonomous bug discovery.')
        if fixed_owner is None:return report
        candidate=build_source_artifact(Path(fixed_owner.name)/'repository',names)
        report['candidate_artifact_sha256']=candidate.sha256
        if candidate.sha256!=BASE:
            report['reason']='Candidate differs from the exact already-approved restoration; no candidate source was executed.'
            return report
        result=execute(DockerProbe(docker),candidate)
        report['test_result']=result
        report['status']='gemini_learning_repair_verified' if result['exit_code']==0 and result['passed']==8 and result['failed']==0 else 'candidate_tests_failed'
        return report
    finally:
        if fixed_owner:fixed_owner.cleanup()
        if broken_owner:broken_owner.cleanup()


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,required=True);p.add_argument('--docker',required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args()
    try:result=check(a.inputs,a.docker)
    except Exception as error:
        # Never echo provider exception text or credentials.
        result=dict(status='integration_check_failed',error_type=type(error).__name__,provider_request_count_maximum=1)
    a.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('proposal','test_result','validation')},indent=2))
