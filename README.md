# Competitions

Organizer-hosted, invitation-only challenge app for Möbius. This replaces the
initial demo; no sample people, challenges, or scores load in the app. Existing
legacy `competitions.json` is left untouched, not silently imported as real data.

## How it works

- The local signed-in installation owner organizes its challenges. Competitors
  and reviewers use their own installation of this same package.
- Create a draft, invite competitors by their actual Mobius `@handle`, and let
  each person accept or decline from their own Competitions app. Accepted
  competitors can submit to every card; no organizer hostname needs to be
  copied into a form.
- Create a draft, invite people, add one or more cards, then open the challenge
  after at least one invite is accepted. Dates are UTC calendar dates; end date
  is inclusive. Nobody can self-enroll.
- Accepting an invitation opens the organizer’s challenge automatically. The
  organizer hostname is carried inside the verified invitation, not copied by
  hand.
- Human cards require assigned reviewers. All assigned reviewers must approve;
  any rejection rejects the entry. Reviewers may leave comments.
- AI cards explicitly ask the organizer to authorize model credit. A supervised
  job picks one pending entry per minute, sends the requirements and submitted
  text/images to the owner's Möbius model gateway, and records a numeric reference
  plus rationale. URLs are not fetched; the agent must disclose unverifiable
  evidence. Failures remain visible for explicit retry. No model tools, approval,
  rejection, or official scoring authority is provided to the model.
- Organizer-only scores are 0.0–10.0 with at most one decimal. At least one
  approved entry is required. The leaderboard sums integer tenths; missing
  scores remain null, including the total when no card is scored.
- Other competitors appear only in the dedicated per-card list-then-click view.
  Main competitor card responses do not include the roster or other entries.
- Images are PNG/JPEG/WebP, validated on the server, limited to 2 MB each and
  24 megapixels. A submission with combined attachments must fit 7 MB JSON.

## Identity and security

`identity_manage` is required to use the local verified profile and account
handle directory. No owner credential is forwarded to a peer. Incoming remote
commands must prove control of a host that the Möbius directory currently
associates with the claimed `@handle`. Each short-lived proof binds the target,
actor, action, full command digest, and idempotency ID. Proofs expire in two
minutes. Remote callers never receive organizer authority, even when their
handle matches the organizer's. Outbound DNS is checked and pinned, redirects
and private addresses rejected using the transport originally shipped by
Möbius Social (`peer_transport.py`, `net_utils.py`, `dns_resolver.py`).

This uses the platform's existing directory-backed **handles**, not invented
M-1234 IDs or email/display names. It does not yet support inviting by opaque
`user_…` account IDs: the current identity bridge resolves handles to hosts.
If an account changes its handle, an organizer must deliberately reconcile its
membership/history; do not silently grant an old entry's history to a new
handle owner. See RELEASE_CHECKLIST.md before distributing.

All domain writes run in SQLite transactions under the app's numeric storage
directory. Submission caps include pending, approved, and rejected entries.
Retries with the same request ID return the original result; different content
under that ID is rejected. The original requirements and submission fields are
not silently changed after publication. Both local and remote callers pass
through the same domain rules.

The app is online-only. The organizer and participant installations must be
publicly reachable over HTTPS and registered under their Möbius accounts.
No Social installation is required. All code and state are app-owned: no
platform source modification or server restart is needed.

## Files and tests

- `domain.py`: field registry, validation, permissions, transactions, scores.
- `service.py`: reviewed JSON service, local identity and peer verification.
- `review_job.py`: supervised AI worker (never finalizes entries).
- `index.jsx`, `ChallengeForms.jsx`, `CardView.jsx`, `ui.jsx`, `theme.js`: UI.
- `tests/`: isolated fixtures only; tests make no paid model calls.

Run `python3 -m unittest discover -s tests -v`. Apply using the platform app
helper; source edits alone do not change the accepted runtime. The package uses
Python/httpx/Pillow/FastAPI supplied by the current Möbius runtime, and React plus
the supported OpenAI UI icon import. The schedule entry is executable Python.
