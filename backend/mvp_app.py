"""Local CodePilot UI and unified reports. Generic repository execution stays off."""
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import shutil
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from analyzer import get_bug_relevant_code
from github_service import clone_repository
from patch_validator import prepare_patch_copy, safe_file, normalize_path
from source_artifact import approved_path, _source_bytes

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT.parent / 'frontend'
BUSY = threading.BoundedSemaphore(1)
JOBS_LOCK = threading.Lock()


@asynccontextmanager
async def lifespan(application):
    yield
    service = getattr(application.state,'report_jobs',None)
    if service is not None:
        import asyncio
        await asyncio.to_thread(service.close)
        del application.state.report_jobs


app = FastAPI(title='CodePilot AI local MVP', version='0.2.0',lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])


@app.middleware('http')
async def local_requests(request: Request, call_next):
    if request.method == 'POST':
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Use the local CodePilot page.'}, status_code=403)
        if request.headers.get('content-type','').split(';')[0] != 'application/json':
            return JSONResponse({'detail':'JSON request required.'},status_code=415)
        # Read with a bound even when Content-Length is missing or inaccurate.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 32768:
                return JSONResponse({'detail':'Request too large.'}, status_code=413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    return response


class Investigation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['reviewed_example','requests_baseline','itsdangerous_exercise','gemini_evidence','live'] = 'reviewed_example'
    repo_url: str = Field(default='', max_length=250)
    issue: str = Field(default='', max_length=6000)
    send_source_to_ai: bool = False


def read_evidence(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def reviewed_report(evidence=None, decision=None):
    fresh = evidence is not None
    evidence = evidence if fresh else read_evidence('real_auth_matrix_results.json')
    decision = decision if decision is not None else read_evidence('real_auth_compatibility_decision.json')
    if evidence['status'] != 'real_requests_smoke_passed' or decision['proposed_fallback_approved'] is not False:
        raise ValueError('Reviewed evidence unavailable')
    return dict(schema_version=1, mode='isolated_baseline_run' if fresh else 'recorded_evidence', status='no_compatible_patch',
                repository='https://github.com/psf/requests', revision=evidence['revision'],
                issue='Basic Authentication with non-Latin-1 credentials',
                summary='The Unicode limitation was reproduced. Automatic UTF-8 fallback is not approved because compatibility is not established.',
                source='Fresh isolated real-source baseline run; no patch was applied.' if fresh else 'Previously recorded isolated real-source run; this action does not rerun tests.',
                files=['src/requests/auth.py'], proposal=None, diff='',
                validation=dict(scope='fresh baseline behavior' if fresh else 'recorded baseline behavior', syntax_valid=None, behavior_tested=True,
                                checks_passed=len(evidence['result']['cases'])+len(evidence['result']['compatibility_matrix']),
                                upstream_suite_tested=False, patch_tested=False),
                evidence=dict(artifact_sha256=evidence['artifact_sha256'], worker_image=evidence['image_id'],
                              baseline=evidence['result']['compatibility_matrix'], decision=decision),
                risks=['No candidate patch was tested.','No server interoperability or upstream test-suite run.'],
                next_steps=['Keep the default encoding unchanged.','Define an explicit server/caller encoding policy before proposing a change.'])


def fresh_requests_report():
    from real_requests_smoke import run
    from assess_real_auth_compatibility import assess
    docker = shutil.which('docker')
    if docker is None:
        candidate = Path('C:/Program Files/Docker/Docker/resources/bin/docker.exe')
        if candidate.is_file():
            docker = str(candidate)
    if docker is None:
        raise HTTPException(503,'Docker is required for a fresh Requests baseline run.')
    # Exact backend-owned source, image and checks only; no caller-controlled path or command.
    evidence = run(ROOT / 'real_requests_smoke_inputs', docker)
    return reviewed_report(evidence, assess(evidence))


def learning_comparison_report():
    from itsdangerous_exercise import run
    docker=shutil.which('docker') or 'C:/Program Files/Docker/Docker/resources/bin/docker.exe'
    result=run(ROOT/'itsdangerous_inputs',docker)
    if result['status']!='learning_comparison_passed':
        raise ValueError('Learning comparison did not match expected outcomes')
    return dict(schema_version=1,mode='seeded_learning_exercise',status='learning_repair_verified',
                repository=result['repository'],revision=result['revision'],
                issue='Deliberately introduced Base64 defect in a temporary learning copy',
                summary='Upstream baseline passed 8 tests twice. The deliberate temporary bug failed 2 tests; the validated repair restored all 8 passes.',
                source='Fresh upstream pytest execution with an explicitly seeded learning bug. This is not an upstream defect or an AI-generated repair.',
                files=['src/itsdangerous/encoding.py','tests/test_itsdangerous/test_encoding.py'],
                proposal=dict(summary='Restore original upstream behavior',changes=result['repair']),
                diff=''.join(row['diff'] for row in result['patch_validation']['validation']),
                validation=dict(scope='8 selected upstream cases; seeded bug and exact restoration',syntax_valid=True,behavior_tested=True,patch_tested=True,
                                checks_passed=result['repaired']['passed'],upstream_suite_tested=False),
                evidence=result,risks=['Only the selected encoding tests ran.','The defect was deliberately seeded for learning.'],
                next_steps=['Review the failing cases and repair diff.','Review another explicit test profile before expanding execution.'])


def recorded_gemini_report():
    from itsdangerous_exercise import BASE, REVISION
    evidence=read_evidence('gemini_learning_repair_results.json')
    tests=evidence['test_result']
    if (evidence['status']!='gemini_learning_repair_verified' or evidence['candidate_artifact_sha256']!=BASE
        or tests['artifact_sha256']!=BASE or tests['passed']!=8 or tests['failed']!=0 or tests['exit_code']!=0):
        raise ValueError('AI repair evidence is inconsistent')
    return dict(schema_version=1,mode='recorded_gemini_evidence',status='recorded_ai_repair_verified',
                repository='https://github.com/pallets/itsdangerous',revision=REVISION,
                issue='Seeded Base64 learning defect; the expected repair was provided to Gemini',
                summary='A recorded live Gemini proposal passed full-file validation and eight selected upstream tests after matching the approved source artifact.',
                source='Recorded provider integration evidence. This action makes no API request and does not rerun tests. The expected repair was supplied in the prompt.',
                files=['src/itsdangerous/encoding.py','tests/test_itsdangerous/test_encoding.py'],proposal=evidence['proposal'],
                diff=''.join(row['diff'] for row in evidence['validation']['validation']),
                validation=dict(scope='recorded AI candidate; 8 selected upstream cases',syntax_valid=True,behavior_tested=True,patch_tested=True,
                                checks_passed=8,upstream_suite_tested=False),
                evidence=evidence,
                risks=['Seeded exercise; this does not demonstrate autonomous bug discovery.',
                       'The AI suggested an incorrect Base64 expectation for hello world; that suggestion was not used in the executed tests.'],
                next_steps=['Review model suggestions independently.','Use only reviewed profiles for repository test execution.'])


def live_report(request):
    if not request.issue.strip():
        raise HTTPException(422,'Describe the issue first.')
    if not request.send_source_to_ai:
        raise HTTPException(422,'Confirm sending selected repository code to Gemini.')
    # Import only when requested: the recorded example works without an API key.
    try:
        from ai_service import generate_patch_with_ai, PatchProposal
    except RuntimeError:
        raise HTTPException(503,'Configure GEMINI_API_KEY in backend/.env to use live analysis.')
    owner, repo = clone_repository(request.repo_url)
    try:
        selected = get_bug_relevant_code(repo, request.issue)
        if not selected:
            raise HTTPException(422,'No suitable Python source files found.')
        for row in selected:
            approved_path(row['file'])
            _source_bytes(row['code'].encode('utf-8'))
        proposal = PatchProposal.model_validate(generate_patch_with_ai(request.issue, selected)).model_dump()
        revision = subprocess.run(['git','-C',str(repo),'rev-parse','HEAD'],capture_output=True,check=True,timeout=10).stdout.decode().strip()
        base = dict(schema_version=1, mode='live_ai', repository=request.repo_url, revision=revision,
                    issue=request.issue, files=[r['file'] for r in selected], summary=proposal['summary'],
                    proposal=proposal, diff='', risks=proposal['risks'],
                    evidence=dict(selected_source_sha256={r['file']:hashlib.sha256(safe_file(repo,r['file']).read_bytes()).hexdigest() for r in selected}),
                    next_steps=proposal['tests_to_add'], source='AI proposal based on selected source; no repository code executed.')
        if not proposal['changes']:
            return dict(**base,status='needs_more_context' if proposal['needs_more_context'] else 'no_change_proposed',
                        validation=dict(scope='no code changes',syntax_valid=None,behavior_tested=False,patch_tested=False))
        working = None
        try:
            working, validation = prepare_patch_copy(repo, proposal['changes'], [r['file'] for r in selected])
            if working is not None:
                pieces = []
                for name in sorted({normalize_path(r['file']) for r in proposal['changes']}):
                    old = safe_file(repo,name).read_text(encoding='utf-8').splitlines(keepends=True)
                    new = safe_file(Path(working.name)/'repository',name).read_text(encoding='utf-8').splitlines(keepends=True)
                    # Preserve valid unified-diff boundaries for missing final newlines.
                    for line in difflib.unified_diff(old,new,fromfile='a/'+name,tofile='b/'+name):
                        pieces.append(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n')
                base['diff'] = ''.join(pieces)
            base['validation'] = dict(scope='full-file exact matches and Python syntax only',
                                      syntax_valid=validation['all_valid'],behavior_tested=False,patch_tested=False,
                                      details=validation, temporary_copy_retained=False)
            base['status'] = 'proposal_validated' if validation['all_valid'] else 'proposal_rejected'
            return base
        finally:
            if working is not None:
                working.cleanup()
    finally:
        owner.cleanup()


@app.get('/api/health')
def health():
    return dict(status='ready',generic_repository_execution_enabled=False, ai_configured=bool(__import__('os').getenv('GEMINI_API_KEY')))


@app.post('/api/reports')
def create_report(request: Investigation):
    if not BUSY.acquire(blocking=False):
        raise HTTPException(429,'An investigation is already running. Try again shortly.')
    try:
        if request.mode == 'reviewed_example':
            return reviewed_report()
        if request.mode == 'requests_baseline':
            return fresh_requests_report()
        if request.mode == 'itsdangerous_exercise':
            return learning_comparison_report()
        if request.mode == 'gemini_evidence':
            return recorded_gemini_report()
        return live_report(request)
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(422,'Repository, AI proposal or saved evidence failed validation.')
    except Exception:
        raise HTTPException(502,'Investigation could not finish. Check Git, network and Gemini configuration, then try again.')
    finally:
        BUSY.release()


@app.get('/')
def index():
    return FileResponse(FRONTEND / 'index.html')


def jobs():
    with JOBS_LOCK:
        if not hasattr(app.state,'report_jobs'):
            from report_job_service import ReportJobs
            app.state.report_jobs = ReportJobs(ROOT/'local_report_jobs',create_report)
        return app.state.report_jobs


@app.post('/api/jobs',status_code=202)
def submit_job(request: Investigation):
    if request.mode == 'live' and (not request.issue.strip() or not request.send_source_to_ai):
        raise HTTPException(422,'Describe the issue and confirm source sharing first.')
    try:
        return jobs().submit(request)
    except OverflowError:
        raise HTTPException(429,'Queue or local history is full. Wait for active work to finish.')


@app.get('/api/jobs')
def job_history():
    return jobs().history()


@app.get('/api/jobs/{identity}')
def job_detail(identity: str):
    try:
        return jobs().get(identity)
    except KeyError:
        raise HTTPException(404,'Job not found.')


@app.post('/api/jobs/{identity}/cancel')
def cancel_job(identity: str):
    try:
        return jobs().cancel(identity)
    except KeyError:
        raise HTTPException(404,'Job not found.')


if FRONTEND.is_dir():
    app.mount('/assets',StaticFiles(directory=FRONTEND),name='assets')
