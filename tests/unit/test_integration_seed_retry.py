"""VMCP-129 (615): the integration suite's seeding retry, pinned where it runs on EVERY push.

The bug it exists for is probabilistic (5 of 9 fresh-container suite runs), so the integration
suite cannot pin it: a green run proves nothing about the next one. What CAN be pinned
deterministically is the property that makes the retry safe to have at all — a PUT whose write
already applied is never re-issued — and that is what these tests are. They import the helper
from the integration conftest, which is import-safe without a server (its module body only reads
an env var; every test in that package is skipped when it is unset).

The risk being pinned is a real one and it points the OTHER way from the flake: `PUT` here is
CREATE, so a blind retry after a 500 that had in fact succeeded would mint a second project /
label / comment — the exact duplication those tests assert against. A helper that made the suite
green by quietly duplicating rows would be worse than the flake it replaced.
"""
import pytest

from tests.integration.conftest import seed_row
from vikunja_mcp.api import VikunjaError


def _raiser(statuses, ok="created"):
    """A create() that raises the given statuses in order, then succeeds. Records its calls."""
    calls = []

    def create():
        calls.append(len(calls))
        if len(calls) <= len(statuses):
            raise VikunjaError(statuses[len(calls) - 1], "boom")
        return ok

    return create, calls


def test_a_clean_create_is_called_once_and_never_consults_the_reader():
    """The overwhelmingly common path must cost nothing: no extra read per seeded row, or the
    three boundary tests would each pay 53 full paged reads for a collision that fires once."""
    reads = []
    create, calls = _raiser([])
    assert seed_row(create, lambda: reads.append(1), backoff=0) == "created"
    assert len(calls) == 1
    assert reads == []


def test_a_500_whose_write_did_not_land_is_re_issued_in_place():
    create, calls = _raiser([500])
    assert seed_row(create, lambda: False, backoff=0) == "created"
    assert len(calls) == 2, "the absent row was not re-created"


def test_a_500_whose_write_DID_land_is_not_re_issued():
    """THE ONE THAT MATTERS. Vikunja can apply the write and still fail on the way back; the row
    is there. Re-issuing it would mint a duplicate — silently, and only on the rare collision
    path, i.e. exactly where nobody would look. Delete the `if landed(): return None` branch in
    seed_row and this test must go red (verified by doing it)."""
    create, calls = _raiser([500])
    assert seed_row(create, lambda: True, backoff=0) is None
    assert len(calls) == 1, f"the landed row was created {len(calls)} times — a duplicate"


def test_a_4xx_is_raised_at_once_and_the_reader_is_never_consulted():
    """A 4xx is a refusal, not contention: retrying cannot help and the extra read is pure cost.
    Only 5xx is ambiguous about whether the write applied."""
    reads = []
    create, calls = _raiser([409])
    with pytest.raises(VikunjaError) as e:
        seed_row(create, lambda: reads.append(1), backoff=0)
    assert e.value.status == 409
    assert len(calls) == 1
    assert reads == []


def test_retries_are_bounded_and_a_server_that_never_works_still_fails_loudly():
    """The point of the helper is to absorb ONE collision, not to hide a broken server: a suite
    that loops forever, or that passes with rows missing, would be worse than the red run."""
    create, calls = _raiser([500, 500, 500, 500, 500])
    with pytest.raises(VikunjaError) as e:
        seed_row(create, lambda: False, attempts=4, backoff=0)
    assert e.value.status == 500
    assert len(calls) == 4, "attempts= is not the number of create attempts"


def test_a_collision_that_clears_on_the_second_retry_still_seeds():
    """Two collisions in a row is not something the measurement showed, but the budget exists
    precisely so the suite does not hang on the tail of the distribution."""
    create, calls = _raiser([500, 503])
    assert seed_row(create, lambda: False, attempts=4, backoff=0) == "created"
    assert len(calls) == 3
