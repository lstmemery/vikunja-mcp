"""Scoped delegated board moves: per-card, user-instruction-gated, audited.

WHAT THIS IS. The board's human-only gates (Done in both directions, Backlog triage,
label changes) stay in force for every ordinary tool. This module adds ONE narrow,
explicitly user-authorized exception channel — `Workflow.delegated_move` — for the
moves the human otherwise performs by hand in the web UI on their OWN explicit chat
instruction for THAT SPECIFIC card (2026-09-18: widened from three to five on the
captain's instruction — see AMENDMENT below):

    mark-done          a card the user said to close (with verification evidence)
    move-stage         ANY stage-to-stage move the user ordered, named in the record
                       (`stage = "Done"`, "Queue", "Review", … including out of Done
                       and into/out of Icebox — the captain's orders unblock bookkeeping
                       moves on his own board; a move TO Done still passes the
                       self-certification guard and its opt-out below)
    triage-to-queue    a Backlog card the user told the agent to work on
    add-label / clear-label   a label change the user asked for by name

AMENDMENT 2026-09-18 (captain: "I want to give my agents admin privileges to Vikunja
so that they can move the cards on my orders. If something needs to be reviewed, they
should still ask me for review."): the original four actions covered the closes and
edits the errands board needed; the captain's instruction covers CARD MOVES GENERALLY
— any transition between the board's columns on his explicit recorded instruction.
What does NOT change: the instruction gate, the per-card record, the dated audit
comment, the expiry window, the default-refuse posture. What the widening does NOT
do: certify agent work — a `move-stage` into Done on a card the agent's own account
created still passes the self-certification guard (refused without the `reviewed`
verdict unless VIKUNJA_DELEGATION_AGENT_MARK_DONE is set), anything that genuinely
needs independent review still goes through advance(to='review') / review_task, and
anything needing a human decision still goes to call_human. The record-format
rationale in ADR 0002's human-only-Done line is amended by this same change.

WHAT IT IS NOT. Not a blanket "agents may self-approve" grant, and never a route for
an agent to certify its own authored work: mark-done refuses a card the agent itself
created unless an independent review verdict (`reviewed` label) already landed on it —
unless the deployment opted out of THAT one guard (see AGENT_MARK_DONE below); the
same guard applies to `move-stage` into Done — and every fired transition posts a
dated `[delegated-move]` audit comment QUOTING the user instruction that authorized
it. Refusal is the default everywhere else: an action outside the allowlist, a card
without a matching in-date record, an expired record, a missing record file, or a
record that does not parse — each refuses and names why.

THE THREE ARMS, all of which a HUMAN controls (removing any one of them kills the
capability without touching the code):

1. The designated identity. A second, narrower Vikunja API token (`omp-delegated`)
   lives in the DESIGNATED delegated sources only — the `vikunja-delegated`
   registration's own env block (VIKUNJA_TOKEN) or `~/.config/vikunja-mcp/env-delegated`
   (mode 600, never committed) — and `config.load_config` refuses to arm delegation when
   NEITHER supplies a token, and refuses the shared agent token from EITHER source (a
   copy-paste of the shared token is the identity mistake in both spellings) — a
   delegated server cannot load the shared `omp-agent` identity, by construction.
   Revoking the token in Vikunja's web UI ends delegation instantly.

2. The server entry. `VIKUNJA_DELEGATION=1` in the `vikunja-delegated` registration
   (a file the user manages). Without it the server registers the ordinary tools and
   never registers `delegated_move`; with it the server registers ONLY this tool, so
   the narrow scope is visible in the tool list itself.

3. The per-card record. `~/.config/vikunja-mcp/delegation-authorized.toml` — one
   `[[authorized_move]]` block per delegated transition, carrying the quoted user
   instruction and its expiry. No entry, no move; a blanket grant is NOT expressible
   (there is no task_id wildcard and no multi-card form).

Provenance honesty, stated rather than glossed: the record file is TRANSCRIBED by the
agent from the user's chat instruction — the chat transcript is the source of truth,
and the audit comment on the card is what makes every delegated move human-auditable
(the human reads the quote against their own memory and revokes via arm 1/2 on
disagreement). This is a guardrail, not a cryptographic boundary: the security
boundary is the scoped token, exactly as the project README says.
"""

import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The arm switch for the delegated server registration AND for the token swap in
# load_config. Truthy spellings are closed: anything else reads as "not armed".
ENV_DELEGATION = "VIKUNJA_DELEGATION"

# The deliberate opt-out of ONE guard: the self-certification refusal
# (workflow._require_review_on_self_authored). Off by default, and when off a card the
# agent's own account created still refuses a delegated move to DONE (mark-done, or
# move-stage with stage = "Done") unless the independent `reviewed` verdict is on it —
# even on the user's explicit instruction. ON (truthy, same closed set as
# ENV_DELEGATION), the user's recorded instruction IS the certification: the record
# file must still quote it verbatim, the audit comment still lands before the move,
# and every other guard (already-Done, Icebox for mark-done, allowlist, expiry,
# audit-failure) holds exactly as before. Only the created_by/verdict read is lifted,
# and only for the Done target.
#
# WHY THIS LAYER AND NOT A `.vikunja-mcp.toml` KEY. The gate lives ONLY on the armed
# delegated server, whose project (a personal errands board) typically has no repo — a
# walk-up toml would govern by where the SESSION happened to start, not by the board, so
# the same card could be closable from one cwd and refused from another. The delegation
# arms are deliberately USER-MANAGED GLOBAL surfaces (token file, server entry, record
# file), and this rides beside the arm switch itself: set it in the `vikunja-delegated`
# entry's env block in the MCP registration file, next to VIKUNJA_DELEGATION=1 — the same
# file, the same editing hand, one more line. It is NOT a per-card or per-record grant:
# a record cannot set it, and no tool call can.
ENV_AGENT_MARK_DONE = "VIKUNJA_DELEGATION_AGENT_MARK_DONE"
# Optional override of the per-card record path (machine-local tests). Absent -> the
# default below. Like the token files, this is an env-layer key only, never toml.
ENV_AUTHORIZED_FILE = "VIKUNJA_DELEGATION_AUTHORIZED_FILE"
DELEGATED_ENV_FILE = Path("~/.config/vikunja-mcp/env-delegated").expanduser()
AUTHORIZED_FILE = Path("~/.config/vikunja-mcp/delegation-authorized.toml").expanduser()

# THE ALLOWLIST, closed by construction — the same closed-set discipline as
# config.LANGUAGES: a value outside the set is refused by name, never silently
# narrowed. Exactly the transitions the human asked to delegate, plus the label half
# split by direction (add vs clear) because the record must say which. `move-stage`
# (2026-09-18 captain widening) is the general form: the record's `stage` key names
# the target column, so ONE action covers every stage-to-stage move on instruction —
# including into/out of Done and Icebox, which mark-done refuses by name.
ACTIONS = frozenset(
    {"mark-done", "move-stage", "triage-to-queue", "add-label", "clear-label"}
)

# Labels no delegated call may touch. The two VERDICT labels are the review
# independence surface (`review_task` writes them; an agent setting `reviewed`
# by hand would make a self-certified card indistinguishable from an accepted
# one). `epic` is a structural container marker (decompose's).
PROTECTED_LABELS = frozenset({"reviewed", "review-failed", "epic"})

# How old a record may be at fire time. Bounds the window a stale file authorizes:
# without it, a months-old file (or one transcribed from a conversation the user no
# longer endorses) keeps delegating forever. Chosen over "no limit" for the same
# reason DEFAULT_WIP_LIMIT chose a number over absence: "no bound" is not a spelling
# a gate should have.
MAX_RECORD_AGE = timedelta(days=7)

# The audit marker, shared with every delegated transition. Startswith-pinned by
# tests; the text names the date, the action, the card, the quoted instruction and
# the record, in that order — a human scanning the card reads the authorization
# without opening the record file.
DELEGATED_MARKER = "[delegated-move]"

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DelegationPolicy:
    """What this server instance may delegate. None-armed -> every ordinary gate
    holds and `delegated_move` refuses before it reads anything."""

    armed: bool
    authorized_file: Path
    # the self-certification opt-out (ENV_AGENT_MARK_DONE): when true, a delegated
    # move of an agent-authored card to Done (mark-done, or move-stage to Done) does
    # NOT refuse for lacking the `reviewed` verdict — the user's recorded instruction
    # + the mandatory audit comment carry the certification instead. Default False =
    # the guard fires exactly as before.
    agent_mark_done: bool = False


@dataclass(frozen=True)
class AuthorizedMove:
    """One per-card record: exactly what the user's instruction authorized, no more."""

    task_id: int
    action: str
    label: str | None
    instruction: str
    evidence: str | None
    authorized_at: datetime
    expires: datetime
    # the TARGET stage, move-stage only: the record names the destination the user's
    # instruction authorized, so the tool call carries no target of its own — the
    # record is the single source of what was authorized. (Defaulted field last: the
    # dataclass forbids a default before non-default fields.)
    stage: str | None = None


def is_armed(environ: dict[str, str]) -> bool:
    return (environ.get(ENV_DELEGATION) or "").strip().lower() in _TRUTHY


def load_delegation(environ: dict[str, str]) -> DelegationPolicy:
    """The mechanism arm: is this server instance a delegated one, and where does its
    per-card record live. Reads NO secret — the token swap lives in config.load_config,
    so the delegated identity stays a config-layer fact like every other credential."""
    armed = (environ.get(ENV_DELEGATION) or "").strip().lower() in _TRUTHY
    raw = (environ.get(ENV_AUTHORIZED_FILE) or "").strip()
    authorized_file = Path(raw).expanduser() if raw else AUTHORIZED_FILE
    # Same closed truthy set as the arm switch: an unknown spelling reads as OFF, never
    # as a guess — an opt-out that half-fires on a typo would be worse than one that
    # needs re-spelling.
    agent_mark_done = (environ.get(ENV_AGENT_MARK_DONE) or "").strip().lower() in _TRUTHY
    return DelegationPolicy(
        armed=armed, authorized_file=authorized_file, agent_mark_done=agent_mark_done,
    )


def _aware(dt: datetime, field: str, line: str) -> datetime:
    """Every recorded instant must carry an offset. A naive datetime would compare
    against a clock the record's author did not name — refuse rather than guess UTC."""
    if dt.tzinfo is None:
        raise ValueError(
            f"{line}: {field} must carry a timezone offset (e.g. 2026-09-17T00:00:00Z), "
            f"got a bare datetime — the record's window would otherwise be read in an "
            f"unnamed zone"
        )
    return dt


def parse_authorized_file(path: Path) -> list[AuthorizedMove]:
    """Strict parse of the per-card record. STRICT means: a file that exists but does
    not parse is a REFUSAL, never a silent empty list — an unreadable record must look
    the same from the outside as a missing one looks from the inside (loud), because
    the two demand different fixes (fix the file vs record the instruction)."""
    if not path.is_file():
        raise FileNotFoundError(path)
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    entries_raw = raw.get("authorized_move")
    if entries_raw is None:
        raise ValueError(
            f"{path}: no [[authorized_move]] blocks — an empty file delegates nothing; "
            f"if a move was authorized, record it as one block per transition"
        )
    if not isinstance(entries_raw, list):
        raise ValueError(f"{path}: authorized_move must be an array of tables")
    entries: list[AuthorizedMove] = []
    for i, raw_entry in enumerate(entries_raw, start=1):
        line = f"{path} authorized_move #{i}"
        unknown = set(raw_entry) - {
            "task_id",
            "action",
            "label",
            "stage",
            "instruction",
            "evidence",
            "authorized_at",
            "expires",
        }
        if unknown:
            raise ValueError(
                f"{line}: unknown key(s) {', '.join(sorted(unknown))} — a record says "
                f"exactly what it authorizes; an unrecognized key is a typo or a "
                f"future format this parse must not silently accept"
            )
        action = str(raw_entry.get("action") or "").strip()
        if action not in ACTIONS:
            raise ValueError(
                f"{line}: action must be one of {', '.join(sorted(ACTIONS))}, got {action!r}"
            )
        task_id = raw_entry.get("task_id")
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1:
            raise ValueError(f"{line}: task_id must be a positive Vikunja task id, got {task_id!r}")
        label = raw_entry.get("label")
        if action in ("add-label", "clear-label"):
            if not (label or "").strip():
                raise ValueError(f"{line}: {action} requires label = the label title, verbatim")
            label = str(label).strip()
        elif label is not None:
            raise ValueError(
                f"{line}: label is only meaningful for add-label/clear-label — "
                f"remove it so the record cannot read as authorizing more than it does"
            )
        stage = raw_entry.get("stage")
        if action == "move-stage":
            if not (stage or "").strip():
                raise ValueError(
                    f"{line}: move-stage requires stage = the target column, verbatim "
                    f"(e.g. stage = \"Done\") — the record names the destination the "
                    f"user's instruction authorized"
                )
            stage = str(stage).strip()
        elif stage is not None:
            raise ValueError(
                f"{line}: stage is only meaningful for move-stage — remove it so the "
                f"record cannot read as authorizing more than it does"
            )
        instruction = str(raw_entry.get("instruction") or "").strip()
        if not instruction:
            raise ValueError(
                f"{line}: instruction is required — quote the user's own words; an "
                f"empty instruction would make the audit comment name nobody"
            )
        evidence = raw_entry.get("evidence")
        if action == "mark-done":
            if not (evidence or "").strip():
                raise ValueError(
                    f"{line}: mark-done requires evidence — what was verified and how, "
                    f"the content the Done verdict rests on"
                )
            evidence = str(evidence).strip()
        elif evidence is not None:
            evidence = str(evidence).strip() or None
        authorized_at = raw_entry.get("authorized_at")
        expires = raw_entry.get("expires")
        if not isinstance(authorized_at, datetime) or not isinstance(expires, datetime):
            raise ValueError(
                f"{line}: authorized_at and expires must both be datetimes "
                f"(TOML 2026-09-16T15:40:00Z form)"
            )
        authorized_at = _aware(authorized_at, "authorized_at", line)
        expires = _aware(expires, "expires", line)
        if expires <= authorized_at:
            raise ValueError(
                f"{line}: expires ({expires.isoformat()}) is not after authorized_at "
                f"({authorized_at.isoformat()}) — a record with no live window "
                f"authorizes nothing and must be corrected, not silently skipped"
            )
        if expires - authorized_at > MAX_RECORD_AGE:
            raise ValueError(
                f"{line}: expires ({expires.isoformat()}) is more than "
                f"{MAX_RECORD_AGE.days} days after authorized_at "
                f"({authorized_at.isoformat()}) — a record may not outlive the week's "
                f"ceiling on record age; a long-lived block would keep delegating long "
                f"after the conversation it transcribed. Record the user's fresh "
                f"instruction in a new block instead"
            )
        entries.append(
            AuthorizedMove(
                task_id=task_id,
                action=action,
                label=label,
                stage=stage,
                instruction=instruction,
                evidence=evidence,
                authorized_at=authorized_at,
                expires=expires,
            )
        )
    return entries


def find_authorized(
    entries: list[AuthorizedMove],
    task_id: int,
    action: str,
    label: str | None,
    now: datetime,
) -> tuple[AuthorizedMove | None, str | None]:
    """The FIRST record matching (task_id, action, label) that is inside its window,
    or the refusal reason. Label match is case- and whitespace-insensitive (`label_key`
    semantics, same tolerance the board's own label writes apply); task_id and action
    are exact — an authorization names ONE card and ONE action."""

    def norm(value: str | None) -> str:
        return (value or "").strip().casefold()

    matching = [
        e
        for e in entries
        if e.task_id == task_id and e.action == action and norm(e.label) == norm(label)
    ]
    if not matching:
        return None, (
            f"no [[authorized_move]] record matches task {task_id} action '{action}'"
            + (f" label '{label}'" if label is not None else "")
            + " — a delegated move fires ONLY on the user's explicit instruction for "
            "THIS card; record that instruction in a new block in the record file "
            "(quoted verbatim, with authorized_at and a near-future expires) and "
            "retry"
        )
    live = [e for e in matching if e.authorized_at <= now and now < e.expires]
    if len(live) == 1:
        return live[0], None
    if len(live) > 1:
        return None, (
            f"{len(live)} records match task {task_id} action '{action}' and are all "
            f"live — remove the duplicates so one instruction maps to one move"
        )
    # Zero live among >= 1 matching: every one is expired or not-yet-valid. Say which.
    stale = matching[0]
    if now < stale.authorized_at:
        return None, (
            f"the record for task {task_id} action '{action}' is not valid yet "
            f"(authorized_at {stale.authorized_at.isoformat()}) — correct the record "
            f"rather than firing early"
        )
    return None, (
        f"the record for task {task_id} action '{action}' expired at "
        f"{stale.expires.isoformat()} — delegated instructions are short-lived; "
        f"record the user's fresh instruction in a new block with a new expiry"
    )


def audit_text(record: AuthorizedMove, me_username: str) -> str:
    """The dated audit comment body for one delegated transition. The record is the
    single source of every field quoted: the action, the card and the user
    instruction — QUOTED verbatim, the human reads it against their own chat and
    revokes (token / server entry / record file) on disagreement."""
    now = datetime.now(timezone.utc)
    lines = [
        f"{DELEGATED_MARKER} {now.strftime('%Y-%m-%dT%H:%MZ')} — {record.action} on task "
        f"{record.task_id}, performed by the delegated '{me_username}' token on the user's "
        f"explicit instruction:",
        f"> {record.instruction}",
    ]
    if record.evidence:
        lines.append(f"evidence: {record.evidence}")
    lines.append(
        f"authorization: {record.authorized_at.isoformat()} -> "
        f"{record.expires.isoformat()} in the delegation record file; every other "
        f"transition stays human-only"
    )
    return "\n".join(lines)
