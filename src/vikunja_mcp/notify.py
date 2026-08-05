"""Best-effort Slack-webhook ping for cards parked in Your Call (#252).

A card lands in Your Call when an agent needs a human decision (call_human) — but the human
used to discover it only by looking at the board. This closes the gap: call_human, having
parked the card, POSTs ONE Slack-compatible incoming-webhook message — {"text": ...}, the
minimal shape every Slack-compatible gateway accepts — to the URL from config
(VIKUNJA_NOTIFY_WEBHOOK; a secret of the token's class: env layers only, never the committed
toml — whoever holds the URL can post into the humans' channel). No URL configured -> no
notifier is built at all and call_human behaves bit-for-bit as before.

Delivery is BEST-EFFORT BY CONTRACT, split across two layers on purpose:
  * this notifier RAISES on any failure (non-2xx, timeout, DNS, refused connection) — it never
    guesses what a failure means;
  * call_human — the single best-effort boundary — swallows it with a one-line stderr note
    (the #134/#135 contract), so a down/misconfigured gateway costs the PING, never the parked
    question, never the tool result, and never a byte on stdout (the MCP protocol channel).
One attempt, short timeout, no retries: a human ping is stale the moment it is late, and
call_human's result must not stall behind a dead gateway (api.py's retry ladder is for the
tracker itself, deliberately NOT mirrored here).

The message is CAPPED and ORDERED, and the two are different fixes for the same defect
(#802). A long ping does not arrive truncated — it does not arrive AT ALL: measured live
2026-08-05 against this project's own gateway, a 4097-character text answered
`500 {"success":false,"error":"Internal server error"}`, while the same host answered 404 on
a deliberately wrong path, so DNS, TLS and routing were working and the path was right. Only
the FAILING side was probed here, on purpose — a success posts into a human's channel — so
the exact boundary is inherited rather than re-measured: #802 reports 4096 passing and 4097
failing, which coincides with Telegram sendMessage's documented limit. The consequence is
the part that matters and it does not depend on the exact number: the more thoroughly an
agent wrote the question, the surer the human never heard about it — while the rulebook is
what asks for thoroughness ("what you need, what you considered, what you recommend").
Replayed through a local stand at that boundary, the REAL questions of the two cards parked
that day composed to 5352 and 4613 characters and both were refused; both had reported
notified=false live.
  * the CAP is what fixes the refusal — nothing else can, since reordering a rejected
    message still delivers nothing;
  * the ORDER is what makes the loss GRADED rather than total. The ref, the title and above
    all the LINK come before the question, so the tail this module drops (and anything a
    downstream reader might drop) is the part that is redundant — the full question is on
    the card already — instead of the address by which a human reaches it.
Counting is in UTF-16 code units for a reason that needs no gateway to be measured: that
count is never SMALLER than the code-point count, so ONE cap satisfies either rule, and the
gateway's own rule is not established (the boundary above was measured with a repeated
single-character filler, where the two agree). It is also the unit Telegram documents. The
rules diverge by 2x in the worst case: measured on the stand, 3000 emoji composed to a
3075-CHARACTER payload of 6075 UTF-16 units, which a character-counting gateway would pass
on and a UTF-16-counting reader would refuse."""
import httpx

# A ping, not a conversation: long enough for a healthy gateway, short enough that a dead one
# can't meaningfully delay call_human (which has already parked the card when this fires).
_TIMEOUT_SECONDS = 5.0

# Headroom under the measured 4096: unpriced insurance against a gateway that frames the text
# before forwarding, not a proof that any particular gateway does. Cheap, because what the cap
# costs is a tail of the question, and the question is on the card in full either way.
_MAX_TEXT_UNITS = 3900

# Says a tail was dropped and where the rest is. It sits AFTER the clipped question, with the
# link already above it — so the reader learns there is more AND how to reach it.
_TRUNCATION_NOTE = "\n[…] truncated — the full question is on the card"


def _utf16_units(text: str) -> int:
    """Length in UTF-16 code units. Equals len() for BMP text (Latin, Cyrillic, punctuation)
    and doubles for astral characters (emoji)."""
    return len(text.encode("utf-16-le")) // 2


def _clip(text: str, budget: int) -> str:
    """The longest PREFIX of `text` fitting `budget` UTF-16 code units. Slicing is by code
    point, so a surrogate pair is never split in half — an astral character either fits
    whole (2 units) or is dropped whole."""
    if budget <= 0:
        return ""
    if _utf16_units(text) <= budget:
        return text
    used = 0
    for i, char in enumerate(text):
        width = 2 if ord(char) > 0xFFFF else 1
        if used + width > budget:
            return text[:i]
        used += width
    return text


class WebhookNotifier:
    def __init__(self, webhook_url: str, tracker_url: str, client: httpx.Client | None = None):
        # `client` is an injection seam for tests (httpx.MockTransport), same pattern as
        # VikunjaAPI; production builds its own. The client lives as long as the notifier —
        # the server process — mirroring the API client's lifecycle.
        self.webhook_url = webhook_url
        self.tracker_url = tracker_url
        self._client = client or httpx.Client(timeout=_TIMEOUT_SECONDS)

    def task_link(self, task_id: int) -> str:
        """Frontend deep-link to the card. config's url is also the API base, so it may carry
        a trailing slash or the /api/v1 suffix — the FRONTEND link must carry neither
        (…/api/v1/tasks/N is a 404 for a human)."""
        base = self.tracker_url.rstrip("/")
        if base.endswith("/api/v1"):
            base = base[: -len("/api/v1")]
        return f"{base}/tasks/{task_id}"

    def compose(self, ref: str, title: str, question: str, task_id: int) -> str:
        """The ping text: what a human needs at a glance — the readable ref, the title, the
        deep-link to answer on, and as much of the question as fits (the ref NAMES the card,
        the LINK is what reaches it — the identifier is not searchable, see _ref).

        Fields are laid out in RECOVERABILITY order, and clipped in the reverse of it, so
        that a message too long for its channel degrades instead of vanishing (#802):
        the question's tail goes first, the title next, and the link never — a ping whose
        link survived is still actionable, a ping whose link was cut is noise. The result
        is guaranteed to hold the link and to be at most _MAX_TEXT_UNITS UTF-16 units; the
        one shape that drops the question ENTIRELY (a ref plus title long enough to leave
        no room for even the truncation note) still carries the link, which is the point."""
        link = self.task_link(task_id)
        prefix = f"[Your Call] {ref} — "
        # The title is clipped against what the frame and the link do NOT need, so the link
        # is never what overflows — even for a title longer than the whole budget.
        title = _clip(title, _MAX_TEXT_UNITS - _utf16_units(prefix + "\n" + link))
        head = f"{prefix}{title}\n{link}"

        room = _MAX_TEXT_UNITS - _utf16_units(head) - 1   # -1 for the newline before it
        if not question or room <= 0:
            return head
        if _utf16_units(question) <= room:
            return f"{head}\n{question}"
        room -= _utf16_units(_TRUNCATION_NOTE)
        if room <= 0:
            return head
        return f"{head}\n{_clip(question, room)}{_TRUNCATION_NOTE}"

    def your_call(self, ref: str, title: str, question: str, task_id: int) -> None:
        """POST the one-message ping (see compose for its shape and its length guarantee).
        Raises on any failure; the CALLER (call_human) is the best-effort boundary that
        swallows it — so notified=false keeps meaning exactly what it meant: the question
        IS parked, only the ping was lost, and the caller must not retry."""
        text = self.compose(ref, title, question, task_id)
        self._client.post(self.webhook_url, json={"text": text}).raise_for_status()
