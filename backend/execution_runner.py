"""Disabled production entry point and explicitly simulated job lifecycle.

No process launcher or real executor is implemented here. Simulation results
never establish that repository tests ran. Limits are policy metadata only.
"""
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from typing import Callable, ContextManager, Protocol

from patch_validator import assert_safe_tree, prepare_patch_copy


@dataclass(frozen=True)
class ExecutionProfile:
    name: str = "synthetic-pilot-v1"
    wall_seconds: int = 60
    cpus: int = 1
    memory_mib: int = 512
    process_limit: int = 64
    scratch_mib: int = 256
    output_bytes: int = 1_048_576


PROFILE = ExecutionProfile()


@dataclass(frozen=True)
class RunResult:
    status: str
    passed: bool | None = None

    def __post_init__(self):
        if self.status not in {
            "completed", "timeout", "resource_limit", "output_limit_exceeded",
            "dependency_unavailable", "collection_error", "worker_error",
        }:
            raise ValueError("Unknown worker result status")
        if self.status == "completed" and type(self.passed) is not bool:
            raise ValueError("Completed results require a boolean passed value")
        if self.status != "completed" and self.passed is not None:
            raise ValueError("Incomplete results cannot claim a test outcome")

    def to_dict(self):
        return {"status": self.status, "passed": self.passed}


class SimulationWorker(Protocol):
    def run(self, snapshot: Path, profile: ExecutionProfile) -> RunResult: ...


def _report(status, reason, *, simulated=False):
    return {
        "execution_status": status, "reason": reason, "tested": False,
        "simulated": simulated, "baseline": None, "patched": None,
        "comparison": "not_run", "profile": PROFILE.name,
    }


def run_patch_job(repo_path, changes, allowed_files):
    """Fail closed before reading source or creating a worker.

    Intentionally no environment flag, request option, or enabled=True switch.
    A verified real isolation adapter is a separate future implementation.
    """
    return _report("execution_disabled", "Verified isolated worker is not configured")


def simulate_patch_job(
    repo_path, changes, allowed_files,
    worker_factory: Callable[[], ContextManager[SimulationWorker]],
    cancelled: Callable[[], bool] = lambda: False,
):
    """Developer-only, synchronous simulation; never wire to a public route.

    The trusted caller supplies a fake context-managed worker. Source files are
    copied and parsed, not imported. No background jobs or restart recovery are
    implemented. Caller owns repo_path; this function owns all created copies.
    """
    report = _report("simulation_pending", "Fake-worker results only", simulated=True)
    try:
        with ExitStack() as stack:
            if cancelled():
                report["execution_status"] = "simulation_cancelled"
                return report
            assert_safe_tree(repo_path)
            owner = stack.enter_context(tempfile.TemporaryDirectory(prefix="codepilot-simulation-"))
            baseline = Path(owner) / "baseline"
            shutil.copytree(repo_path, baseline, ignore=shutil.ignore_patterns(".git"))
            assert_safe_tree(baseline)
            patch_owner, validation = prepare_patch_copy(baseline, changes, allowed_files)
            if patch_owner is not None:
                stack.enter_context(patch_owner)
            report["validation"] = validation
            if not validation["all_valid"]:
                report["execution_status"] = "validation_failed"
                return report
            patched = Path(patch_owner.name) / "repository"
            for label, snapshot in (("baseline", baseline), ("patched", patched)):
                if cancelled():
                    report["execution_status"] = "simulation_cancelled"
                    return report
                # A separate context provides a fresh fake worker for each run.
                with worker_factory() as worker:
                    result = worker.run(snapshot, PROFILE)
                    if not isinstance(result, RunResult):
                        raise TypeError("Worker must return RunResult")
                report[label] = result.to_dict()
                if cancelled():
                    report["execution_status"] = "simulation_cancelled"
                    return report
                if result.status != "completed":
                    report["execution_status"] = "simulation_" + result.status
                    return report
            report["execution_status"] = "simulation_completed"
            outcomes = (report["baseline"]["passed"], report["patched"]["passed"])
            report["comparison"] = {
                (False, True): "simulated_failure_to_pass",
                (True, True): "simulated_no_selected_regression",
                (True, False): "simulated_possible_regression",
                (False, False): "simulated_failures_remain",
            }[outcomes]
            return report
    except Exception:
        # Do not leak exception text, host paths, or worker output to clients.
        report["execution_status"] = "simulation_worker_error"
        report["reason"] = "Simulation failed; no repository tests were executed"
        report["comparison"] = "not_run"
        return report
