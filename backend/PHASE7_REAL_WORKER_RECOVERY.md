# Real-source worker crash recovery

The fixed Requests and ItsDangerous runners now share a durable ownership layer. Before creating a container, it saves a job identity and random container name in a SQLite journal. An operating-system file lock allows one controller for that journal.

Every container receives the job and journal identities as labels. Cleanup lists the exact reserved name, checks the full immutable container ID and both ownership labels, removes by ID, then verifies absence. A name alone never grants permission to remove a container.

The next real-worker invocation reconciles earlier reservations before starting more source execution. If Docker is unavailable or an ownership mismatch prevents cleanup, execution fails closed. Reconciliation includes previously cleaned names, addressing delayed Docker creation after a timed-out request. The historical shared journal records the lifecycle kind as baseline; test purpose remains in each report.

## Verified

- A trusted child controller created and started a labelled sleeper, then exited abruptly with code 86 without Python cleanup.
- A second controller recovered the journal, verified ownership and removed that sleeper. No repository code ran in this crash fixture.
- All four ItsDangerous comparison containers passed their expected outcomes with the journal enabled.
- The Requests profile was rerun with the same ownership protection.

## Boundaries

Recovery runs before the next real-worker invocation, not automatically at web-server startup. The journal covers Docker resource ownership. It does not promise automatic removal of host temporary patch directories after an abrupt host-process kill. Cancellation remains cooperative at the report-operation boundary. These limits must remain visible when describing project completion.

## Interview checkpoints

- Why save intent before creating a container? A crash between creation and recording would otherwise leave an untracked resource.
- Why use labels and immutable IDs? Names can be reused; ownership must be proven before deletion.
- Why fail closed on an unavailable Docker daemon? A failed query does not prove a container is absent.
- What is reconciliation? Comparing recorded intent with actual resources and resolving interrupted work.
