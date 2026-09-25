"""Description Evidence blocks written by ``advance(to='review')``."""

from __future__ import annotations

import html
import re

from .formatting import html_to_text

EVIDENCE_START = "<!-- vikunja-mcp:evidence:start -->"
EVIDENCE_END = "<!-- vikunja-mcp:evidence:end -->"

_SECTION_HEADINGS = (
    "What changed",
    "Verification",
    "Before / after",
    "Artifacts",
    "Residual risks",
    "Approve if",
)
_PLACEHOLDERS = {"fill this in", "placeholder", "tbd", "todo"}


class EvidenceBlockError(ValueError):
    """The supplied Evidence block is absent, incomplete, or malformed."""


def _not_placeholder(value: str, field: str) -> str:
    value = value.strip()
    if not value or value.casefold() in _PLACEHOLDERS:
        raise EvidenceBlockError(f"{field} must contain a value, not a placeholder")
    return value


def _validate_sections(sections: dict[str, str]) -> None:
    for heading in _SECTION_HEADINGS:
        _not_placeholder(sections[heading], heading)

    verification = [line.strip() for line in sections["Verification"].splitlines() if line.strip()]
    pairs = _verification_pairs(verification)
    if not pairs:
        raise EvidenceBlockError(
            "Verification needs one non-empty 'Command:' and 'Key output:' line per check"
        )

    before_after = [line.strip() for line in sections["Before / after"].splitlines() if line.strip()]
    if (len(before_after) != 2 or not before_after[0].startswith("Before:")
            or not before_after[1].startswith("After:")
            or not before_after[0][7:].strip() or not before_after[1][6:].strip()):
        raise EvidenceBlockError(
            "Before / after needs one non-empty 'Before:' line followed by one 'After:' line"
        )

    approve_if = [line.strip() for line in sections["Approve if"].splitlines() if line.strip()]
    if len(approve_if) != 1:
        raise EvidenceBlockError("Approve if must be a single non-empty line")


def _verification_pairs(lines: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for index in range(0, len(lines), 2):
        command = lines[index]
        if not command.startswith("Command:") or not command[8:].strip():
            raise EvidenceBlockError(
                "Verification needs alternating non-empty 'Command:' and 'Key output:' lines"
            )
        if index + 1 >= len(lines):
            raise EvidenceBlockError(
                "Verification needs one non-empty 'Command:' and 'Key output:' line per check"
            )
        output = lines[index + 1]
        if not output.startswith("Key output:") or not output[11:].strip():
            raise EvidenceBlockError(
                "Verification needs alternating non-empty 'Command:' and 'Key output:' lines"
            )
        pairs.append((command[8:].strip(), output[11:].strip()))
    return pairs


def parse_evidence_block(value: str | None) -> dict[str, str]:
    """Parse and validate the required Markdown-shaped Evidence block."""
    if not isinstance(value, str) or not value.strip():
        raise EvidenceBlockError("an Evidence block is required")

    lines = value.replace("\r\n", "\n").replace("\r", "\n").strip().splitlines()
    if not lines or lines[0].strip() != "## Evidence":
        raise EvidenceBlockError("the block must start with '## Evidence'")

    sections: dict[str, list[str]] = {}
    current = None
    for line in lines[1:]:
        match = re.fullmatch(r"###\s+(.+?)\s*", line.strip())
        if match:
            heading = match.group(1)
            if heading not in _SECTION_HEADINGS:
                raise EvidenceBlockError(f"unknown Evidence section '{heading}'")
            if heading in sections:
                raise EvidenceBlockError(f"duplicate Evidence section '{heading}'")
            sections[heading] = []
            current = heading
        elif current is None:
            if line.strip():
                raise EvidenceBlockError("content must be placed under a required section")
        else:
            sections[current].append(line)

    missing = [heading for heading in _SECTION_HEADINGS if heading not in sections]
    if missing:
        raise EvidenceBlockError("missing sections: " + ", ".join(missing))

    normalized = {heading: "\n".join(lines).strip() for heading, lines in sections.items()}
    _validate_sections(normalized)
    return normalized


def _paragraphs(value: str) -> str:
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in value.splitlines():
        if line.strip():
            current.append(line)
        elif current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    return "".join(
        "<p>" + "<br>".join(html.escape(line, quote=False) for line in paragraph) + "</p>"
        for paragraph in paragraphs
    )


def _section_html(heading: str, value: str) -> str:
    if heading == "Verification":
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        items = []
        for command, output in _verification_pairs(lines):
            items.append(
                "<p><strong>Command:</strong> "
                f"<code>{html.escape(command, quote=False)}</code><br>"
                "<strong>Key output:</strong> "
                f"<code>{html.escape(output, quote=False)}</code></p>"
            )
        content = "".join(items)
    elif heading == "Before / after":
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        before, after = lines[0][7:].strip(), lines[1][6:].strip()
        content = (
            "<p><strong>Before:</strong> "
            f"{html.escape(before, quote=False)}<br>"
            "<strong>After:</strong> "
            f"{html.escape(after, quote=False)}</p>"
        )
    else:
        content = _paragraphs(value)
    return f"<h3>{heading}</h3>{content}"


def render_evidence_block(value: str | None) -> str:
    """Render a validated plain-text Evidence block as a safe HTML fragment."""
    sections = parse_evidence_block(value)
    body = "<h2>Evidence</h2>" + "".join(
        _section_html(heading, sections[heading]) for heading in _SECTION_HEADINGS
    )
    return f"{EVIDENCE_START}\n{body}\n{EVIDENCE_END}"


def set_evidence_block(description: str, value: str | None) -> str:
    """Put the current Evidence block first, replacing a prior tool-written block."""
    description = description or ""
    block = render_evidence_block(value)
    starts = description.count(EVIDENCE_START)
    ends = description.count(EVIDENCE_END)
    if starts != ends or starts > 1:
        raise EvidenceBlockError("the existing Evidence block markers are incomplete")
    if starts:
        start = description.index(EVIDENCE_START)
        end = description.index(EVIDENCE_END)
        if end < start:
            raise EvidenceBlockError("the existing Evidence block markers are out of order")
        before = description[:start]
        after = description[end + len(EVIDENCE_END):]
        if not before.strip():
            after = re.sub(r"^\s*<hr\s*/?>\s*", "", after, count=1, flags=re.IGNORECASE)
        remainder = (before + after).strip()
        return block + ("\n<hr>\n" + remainder if remainder else "")
    suffix = f"<hr>\n{description}" if description.strip() else ""
    return block + ("\n" + suffix if suffix else "")


def has_valid_evidence_block(description: str | None) -> bool:
    """Check that the description starts with one complete, tool-written Evidence block."""
    description = description or ""
    if description.count(EVIDENCE_START) != 1 or description.count(EVIDENCE_END) != 1:
        return False
    marker = description.index(EVIDENCE_START)
    if description[:marker].strip():
        return False
    start = marker + len(EVIDENCE_START)
    end = description.index(EVIDENCE_END)
    if end < start:
        return False
    body = description[start:end].strip()
    if not body.startswith("<h2>Evidence</h2>"):
        return False

    headings = list(re.finditer(r"<h3>(.*?)</h3>", body))
    if [match.group(1) for match in headings] != list(_SECTION_HEADINGS):
        return False
    sections = {}
    for index, match in enumerate(headings):
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        sections[match.group(1)] = html_to_text(body[match.end():section_end])
    try:
        _validate_sections(sections)
    except EvidenceBlockError:
        return False
    return True
