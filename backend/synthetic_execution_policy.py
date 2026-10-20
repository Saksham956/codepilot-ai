"""Backend-owned selection policy; public requests select only a profile ID."""
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from artifact_transfer_probe import PINNED_IMAGE
from synthetic_worker_job import TRUSTED_HASHES

@dataclass(frozen=True)
class SyntheticProfile:
    name: str
    kind: str
    wall_seconds: int
    description: str


PROFILES = MappingProxyType({profile.name: profile for profile in (
    SyntheticProfile("arithmetic-baseline-v1", "baseline", 30, "Original trusted arithmetic fixture"),
    SyntheticProfile("arithmetic-patched-v1", "patched", 30, "Patched trusted arithmetic fixture"),
    SyntheticProfile("cancellation-demo-v1", "sleep", 10, "Trusted sleeping fixture for cancellation"),
)})


def selected_profile(name):
    if not isinstance(name, str) or name not in PROFILES:
        raise ValueError("Select an approved synthetic profile")
    return PROFILES[name]


def policy_snapshot(name):
    profile = selected_profile(name)
    record = {"version": "synthetic-policy-v1", "profile": name, "kind": profile.kind,
              "wall_seconds": profile.wall_seconds, "image_id": PINNED_IMAGE,
              "fixture_sha256": {"baseline": TRUSTED_HASHES[0], "patched": TRUSTED_HASHES[1]}.get(profile.kind),
              "memory_mib": 512, "cpus": 1, "process_limit": 64, "scratch_mib": 256,
              "capture_bytes": 65536, "output_threshold_bytes": 1048576, "network": "none"}
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"policy_id": digest, "settings": record}


def public_profiles():
    return [{"profile": profile.name, "description": profile.description,
             "wall_seconds": profile.wall_seconds, "memory_mib": 512, "cpus": 1,
             "process_limit": 64, "scratch_mib": 256, "capture_bytes": 65536,
             "output_threshold_bytes": 1048576, "network": "none",
             "source": "backend_authored_fixture", "repository_execution_enabled": False}
            for profile in PROFILES.values()]
