# Phase 7: live isolation checks with trusted synthetic programs

Completed October 20, 2026. Docker Desktop's Linux engine on WSL 2 was selected for a local synthetic pilot. It was started during this step, and the official Python image was downloaded. Existing application containers and images were not removed or reconfigured. Docker remains running and the downloaded Python image remains cached for future trusted checks.

The production `run_patch_job` entry point still returns `execution_disabled`. No repository source, repository tests, dependency installers, or AI-generated commands were executed. No API route or execution-enabling switch was added.

## What changed, step by step

1. `isolation_probe.py` provides developer-only fixed probes. It accepts a local immutable image ID, not a repository path, test command, or arbitrary script through its command line. It never pulls an image automatically.
2. Each probe creates its own uniquely named container with no host folder or engine socket mounted. A non-root identity, no Linux capabilities, no-new-privileges, default seccomp filtering, no external network, and read-only root filesystem restrict its access.
3. Scratch storage is temporary, capped, and configured with noexec/nosuid/nodev. Memory, CPU quota, process count, and open-file settings are passed explicitly. Proxy variables are overridden with empty values; no host secret variables are forwarded.
4. Before starting a probe, the controller inspects the container configuration. The permissions probe also checks the restrictions from inside the container. Passing a command-line flag alone is not treated as proof that every restriction took effect.
5. The controller drains stdout without retaining its contents. Output above 1 MiB triggers container removal. Docker disk logging is disabled. A wall-clock deadline also triggers removal. The timeout probe deliberately uses a short two-second controller deadline; other probes use 30 seconds.
6. Every probe removes only its own unique container name, then confirms that name is absent from a successful Docker listing. Removal or confirmation failure makes the probe fail. There is no global prune operation.
7. Results are written to `isolation_probe_results.json`. These are evidence from trusted probes, not repository test results or authorization to enable execution.

## Image identity

The official `python:3.13-slim` image was explicitly downloaded for this check. The actual run used the immutable local image identifier:

```
sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
```

The inspected registry digest was `python@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f`. A future run must inspect and review its image identity rather than assume a mutable tag always identifies the same content.

## Observed results

| Probe | What was observed |
| --- | --- |
| Permissions | UID 65534; zero effective capabilities; no-new-privileges and seccomp filtering active; only the loopback interface present; an attempted external socket connection failed; root write denied; scratch write permitted. |
| Environment and mounts | A synthetic host-only environment marker and named secret variables were absent; no host bind mounts or Docker socket were exposed; scratch mount flags were present. Actual secrets were not read. |
| CPU configuration | Cgroup CPU quota equaled one CPU. This verifies configuration, not a performance benchmark. |
| Scratch storage | Writing bounded sample data hit `ENOSPC` under the 256 MiB scratch limit. |
| Process count | Creating a bounded number of sleeping Python children hit `EAGAIN` under the 64-process limit. Children were terminated before container cleanup. |
| Memory | A bounded allocation exceeding 512 MiB was killed; Docker reported `OOMKilled: true` and exit code 137. |
| Timeout | A trusted sleeping program was stopped by the controller deadline, and its container was removed. |
| Output limit | A trusted output generator crossed 1 MiB; the controller removed its container without retaining the stream. |
| Cleanup | Every probe container was absent after removal. |

Seven unit checks cover launch restrictions, immutable image IDs, safe container names, unavailable runtime handling, configuration rejection before start, launch failure cleanup, and refusal to pass when cleanup fails.

## Limits that still matter

This is a trusted synthetic pilot, not a verified hostile-code execution service. Ordinary Linux containers share a kernel within the Docker environment. Docker's daemon and its control interface remain privileged infrastructure. Probe success does not prove resistance to kernel or runtime vulnerabilities.

No host folder was mounted, so this step does not validate importing repository snapshots, dependency handling, test selection, baseline-versus-patched execution, output rendering, or repository-specific compatibility. The current source snapshots can still contain `.env` or other sensitive files: they must not be copied wholesale into a future worker. Design and verify a minimal input-artifact policy first.

This controller has no job queue, restart recovery, durable ownership journal, or protection against the daemon becoming unresponsive during cleanup. Its timeout is a controller decision followed by a Docker removal request, not a hard real-time guarantee. Failure of cleanup is reported, not hidden. The combined storage budget also includes separate 16 MiB shared memory in addition to 256 MiB scratch; memory allocations remain constrained by the memory cgroup.

The script is trusted developer tooling; do not expose its image or Docker executable parameters through public API requests. A future adapter needs a reviewed image allowlist and exact artifact provenance. No subprocess implementation has been connected to `execution_runner.py`.

## Interview checkpoints

- **Why inspect and probe?** Inspection checks configured settings; inside-container probes check observable behavior. Neither alone proves complete isolation.
- **Why disable networking explicitly?** Containers normally have network access. A dedicated no-network configuration must be part of the worker policy.
- **Why is exit code 137 insufficient by itself?** A process can be killed for different reasons. Docker's OOM state supplies the additional evidence for this memory probe.
- **Why check cleanup before declaring success?** A timed-out child can keep consuming resources if its environment survives the request.
- **Why avoid host mounts?** Bind mounts expose host files to container processes and can grant host write access. Minimal copied artifacts need separate validation.
- **Why keep production disabled after passing?** These checks do not yet cover hostile repository input, secure artifact transfer, full job ownership, or recovery.

## Next implementation step

Build a repository-input artifact policy with trusted synthetic tests: include only explicitly selected files, reject links and sensitive paths, bound file count and total size, and record a content manifest. Then verify transfer into an isolated worker without host bind mounts. Keep actual repository tests disabled until artifact handling and the complete job lifecycle have been checked.

## Official references

- [Docker container configuration and runtime limits](https://docs.docker.com/engine/containers/run/)
- [Docker CLI create options](https://docs.docker.com/reference/cli/docker/container/create/)
- [Docker no-network driver](https://docs.docker.com/engine/network/drivers/none/)
- [Docker Engine security considerations](https://docs.docker.com/engine/security/)
