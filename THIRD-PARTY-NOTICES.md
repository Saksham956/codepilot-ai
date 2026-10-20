# Third-party source snapshots

The CodePilot backend includes unchanged upstream source in reviewed input snapshots and JSON source artifacts. The source licenses apply to those files independently of CodePilot's original code.

- **Requests**, Python Software Foundation / Requests contributors. Snapshot commit `b25c87d7cb8d6a18a37fa12442b5f883f9e41741`. Apache License 2.0 notice: `backend/real_requests_smoke_inputs/UPSTREAM-LICENSE.txt`. Source: https://github.com/psf/requests
- **ItsDangerous**, Pallets contributors. Snapshot commit `096c8d42545d3b68ea21a4f890fb2b2d8979c0bd`. BSD 3-Clause notice: `backend/itsdangerous_inputs/UPSTREAM-LICENSE.txt`. Source: https://github.com/pallets/itsdangerous

Deliberate exercise mutations are created only in temporary working copies. Downloaded dependency wheels are excluded from Git; each dependency retains its own license.
