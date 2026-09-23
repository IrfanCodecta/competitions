# Release gate — not yet a Store release

- [x] Replace role-switched demo and hard-coded records.
- [x] Server-authorized domain and validated fields, caps, scoring.
- [x] Directory-backed peer proof protocol and negative tests.
- [x] Real local profile and organizer create/invite/card/publish smoke checks.
- [x] Browser text submission → human review → scoring → leaderboard test.
- [x] Native date-picker, cover upload, and image submission browser flows.
      Real values persisted; image cap 1 disabled another submission.
      Test-only challenge removed after verification.
- [ ] Live cross-installation verification with two consenting owners (owner deferred).
- [x] One owner-approved text-only live AI review: 10.0 reference and rationale;
      submission stayed pending, official scores empty. Separate admin approval
      and official 5.7 scoring succeeded. Isolated temporary data removed.
      Broker requires X-Mobius-Request-Id; worker now sends it and regression tests cover it.
- [ ] Resolve stable identity requirement for handle changes/reassignment before
      declaring long-lived identity protection or supporting opaque account IDs.
- [ ] Validate and smoke-install accepted package on a clean second instance.
- [ ] Explicit owner approval for any public repository or Store publication.

Unit tests use isolated fake model responses and a mocked account directory;
they prove app policy, not deployment connectivity or model-provider behavior.

A same-host HTTPS exchange probe cannot establish cross-installation behavior:
the platform serializes public requests per app, so exchange → own public proof
waits on itself and times out. Normal local commands do not take that path.
Test real separate hosts, including simultaneous reciprocal traffic, before release.
