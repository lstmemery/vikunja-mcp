"""Config resolution: env > repo .vikunja-mcp.env (repo-local, beside toml) >
repo .vikunja-mcp.toml (walk-up) > ~/.config/vikunja-mcp/env."""
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ENV_URL = "VIKUNJA_URL"
ENV_TOKEN = "VIKUNJA_TOKEN"
ENV_PROJECT_ID = "VIKUNJA_PROJECT_ID"
ENV_NOTIFY_WEBHOOK = "VIKUNJA_NOTIFY_WEBHOOK"
ENV_WORKTREE_ROOT = "VIKUNJA_WORKTREE_ROOT"
REPO_FILE = ".vikunja-mcp.toml"
REPO_ENV_FILE = ".vikunja-mcp.env"
USER_ENV_FILE = Path("~/.config/vikunja-mcp/env").expanduser()

# How many Design/Build tasks one token may CLAIM into when the repo toml sets no wip_limit.
# "Claim into", not "hold": the number gates the `claim` transition and is not an invariant on the
# active count — a card re-entering Build past the gate takes the active count OVER it, legitimately
# (tracker #529; the paths are enumerated in `claim`'s tool docstring).
# THE ONE definition of that number (workflow._effective_wip_limit and the < 1 refusal below
# both read it) — human decision of 2026-07-30, tracker #524: an unset key means THREE, not
# "no gate". It used to mean no gate in the code while the rulebook told the pump the drain was
# SERIAL — two contradictory meanings for one absent key — and the humans want three parallel
# per-task agents everywhere without hand-editing every project's toml. Consequence, accepted:
# "no limit" is no longer expressible at all (wip_limit = 0 stays a ConfigError, it is NOT the
# unbounded spelling), and claim starts refusing a 4th active task in projects that set nothing.
DEFAULT_WIP_LIMIT = 3


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    project_id: int
    project_name: str | None = None
    # committed team policy (read ONLY from the repo toml, not env/secret): when true,
    # claim() refuses a new task while you already have an active Design/Build one.
    # Default off -> ships inert and reversible; opt in per team.
    enforce_single_wip: bool = False
    # how many tasks this token may CLAIM into Design/Build — the parallel-drain slot
    # count, and the generalisation of enforce_single_wip (which is exactly wip_limit=1).
    # A gate on `claim`, never an invariant on the active count (see DEFAULT_WIP_LIMIT above).
    # Committed TEAM POLICY of the same class: repo toml ONLY, never env, never a secret.
    # None means "the key is ABSENT from the toml" and nothing more — it is NOT "no gate".
    # The effective number is resolved one layer up, in workflow._effective_wip_limit:
    # enforce_single_wip = true -> 1, otherwise DEFAULT_WIP_LIMIT. Keeping absence visible
    # HERE is exactly what keeps that precedence expressible (resolve the default in this
    # dataclass and the legacy flag could never be reached).
    wip_limit: int | None = None
    # Slack-compatible incoming-webhook URL pinged when call_human parks a card in
    # Your Call (#252). A secret of the token's class — whoever holds the URL can post
    # into the humans' channel — so like the token it is NEVER read from the committed
    # repo toml, only from the env layers. None (default) -> the feature is off.
    notify_webhook: str | None = None
    # where per-task git worktrees are materialised (parallel drain). MACHINE-local, unlike
    # wip_limit — so unlike it, the env layers DO win over the committed toml.
    # None -> workspace_cmd's default, a `<repo>.worktrees` sibling of the repo.
    worktree_root: str | None = None


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]              # кавычки защищают значение — # внутри не комментарий
        else:
            value = value.split(" #", 1)[0].rstrip()   # только у НЕзакавыченных значений
        out[key.strip()] = value
    return out


def _find_repo_toml(start: Path) -> Path | None:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        p = candidate / REPO_FILE
        if p.is_file():
            return p
    return None


def load_config(cwd: Path | None = None, environ: Mapping[str, str] | None = None) -> Config:
    import os

    env = dict(environ) if environ is not None else dict(os.environ)
    user = _parse_env_file(USER_ENV_FILE)

    repo: dict = {}
    repo_env: dict[str, str] = {}
    toml_path = _find_repo_toml(cwd or Path.cwd())
    if toml_path is not None:
        repo = tomllib.loads(toml_path.read_text()).get("tracker", {})
        # repo-local .env лежит СТРОГО рядом с найденным toml — отдельного walk-up
        # для него нет, это одна и та же директория (предсказуемо, без сюрпризов)
        repo_env = _parse_env_file(toml_path.parent / REPO_ENV_FILE)

    url = env.get(ENV_URL) or repo_env.get(ENV_URL) or repo.get("url") or user.get(ENV_URL)
    token = env.get(ENV_TOKEN) or repo_env.get(ENV_TOKEN) or user.get(ENV_TOKEN)
    # секрет класса токена: env-слои ТОЛЬКО, коммитимый toml сознательно пропущен
    notify_webhook = (
        env.get(ENV_NOTIFY_WEBHOOK)
        or repo_env.get(ENV_NOTIFY_WEBHOOK)
        or user.get(ENV_NOTIFY_WEBHOOK)
        or None
    )
    raw_pid = (
        env.get(ENV_PROJECT_ID)
        or repo_env.get(ENV_PROJECT_ID)
        or repo.get("project_id")
        or user.get(ENV_PROJECT_ID)
    )

    if not url or raw_pid is None:
        raise ConfigError(
            f"{REPO_FILE} with [tracker] url/project_id not found (searched from "
            f"{cwd or Path.cwd()} upward) and no {ENV_URL}/{ENV_PROJECT_ID} in env"
        )
    if not token:
        raise ConfigError(
            f"no token: put VIKUNJA_TOKEN=... in {REPO_ENV_FILE} next to {REPO_FILE}, "
            f"in {USER_ENV_FILE} (chmod 600), or pass it via env {ENV_TOKEN}"
        )
    # #768: a base url carrying a query or a fragment cannot work, and used to fail SILENTLY
    # and far from here. `canonical_base_url` appends `/api/v1` to the END OF THE STRING, so
    # measured on the real client: `https://h?Token=Ab` becomes `https://h?Token=Ab/api/v1`,
    # whose raw_path is `/?Token=Ab/api/v1` — every call hits the instance ROOT and comes back
    # 404 or HTML, never an API error a reader could act on. With a fragment it is quieter
    # still: `https://h#Frag` sends raw_path `/` and the suffix never reaches the wire at all.
    # A url that already ends in the suffix but carries a query gets it TWICE
    # (`https://h/api/v1?x=1` -> `.../api/v1?x=1/api/v1`), because the string does not END with
    # it. The same append also makes canonicalisation NON-INJECTIVE on these shapes, so the
    # #148 repoint guard reads `https://h?a=b` and `https://h?a=b/api/v1` as ONE endpoint.
    #
    # REFUSED rather than repaired, and the choice is the point. Inserting the suffix into the
    # PATH (`https://h?Q=A` -> `https://h/api/v1?Q=A`) would make the client work — by silently
    # keeping a query nobody meant to send and attaching it to EVERY API call. A base url with a
    # query is almost always a typo or a link copied out of a browser, so the useful answer is
    # to say so at the moment the config is read.
    #
    # HERE and not in `canonical_base_url`, deliberately: that function raises NOTHING today,
    # its docstring says so, and ONE of the four counts in its argument against rewriting it
    # onto `urllib.parse.urlsplit` rests on exactly that (urlsplit raises ValueError on an
    # unclosed IPv6 authority — a new crash class in a path that raises nothing). Breaking its
    # totality would cost that count, not the argument: the other three are about
    # PERMISSIVENESS and do not depend on totality at all.
    #
    # WHAT THE FIVE `load_config` CALL SITES DO WITH THIS REFUSAL, measured before and after,
    # because an earlier draft of this comment said both consumers come through here anyway —
    # true of the error's SHAPE, and it hid what the two SWALLOWING sites do with it. THREE ARE
    # LOUD, which is the point of the card. The refusal fires INSIDE this function, before any
    # client exists, so what makes those three loud is the caller: `server._wf` lets it out to
    # `_tool`, which renders it as `{"error": ...}`; `claimable_cmd` prints it as its one JSON
    # line and exits 1; `workspace_cmd._build_workflow` (the `--gc` board fetch) propagates it
    # the same way. THE OTHER TWO SWALLOW IT — and the two swallows are NOT the same event: one
    # got quieter, the other merely answers differently. Neither is repaired here:
    #   * `server._reload_workflow_from_disk` wraps `load_config()` in `except Exception:
    #     return False`, so this ConfigError reaches nobody. Measured on a token rotation that
    #     ALSO mistypes the url: before this guard that input raised the loud #148 repoint
    #     refusal NAMING BOTH URLS; now the reload returns False and `_tool` falls through to
    #     the original 401, handing the caller the auth guidance — a confidently WRONG cause,
    #     since the fault is the url. The diagnosis is REPLACED, not merely quietened. Safety is
    #     untouched: no repoint happens, the cached Workflow is the SAME OBJECT afterwards, and
    #     the controls did not move (a genuine host change still raises, a cosmetic-only one
    #     still returns True).
    #   * `workspace_cmd.worktree_root` catches ConfigError and falls back to the default
    #     sibling. NOTHING got quieter here — that path printed nothing before and prints
    #     nothing now; what changed is the ANSWER. Measured against a toml carrying
    #     `worktree_root = "../CUSTOM"`: `workspace <id>` and `--release` still exit 0 on a url
    #     with a query, but the tree now lands in `<repo>.worktrees` instead of `../CUSTOM`.
    #     The handler's own comment (INSIDE the `except`, not above it) declares ConfigError to
    #     mean only "this repo has no tracker config", the expected and fine case — and #768
    #     puts a genuinely-broken-config case into that same class, so it falsifies that
    #     premise rather than merely adding an instance. Not repaired here: splitting
    #     ConfigError into subtypes so one can be caught without the other is its own slice.
    #
    # BOUNDARY, stated rather than glossed, because CLOSED and LOUD are not the same word.
    # Closed everywhere is the SILENT-CLIENT class this card was filed for: no path that reads
    # config can now build a client on such a url. Loud is three of the five sites above — so
    # `workspace` is not "closed" as a COMMAND: `--gc` refuses, while create and release run to
    # exit 0, building no client and paying the swallow instead. Outside config nothing is
    # closed at all: `VikunjaAPI("https://h?x=1", tok)` constructed directly still builds an
    # unusable client, and so does `setup --url`, a second real CLI entry point that builds its
    # client straight from the argument without calling `load_config`. That is the price of
    # keeping the normalizer total.
    if "?" in url or "#" in url:
        bad = "query" if "?" in url else "fragment"
        raise ConfigError(
            f"the tracker url must not carry a {bad}: {url!r}. A Vikunja base url is "
            f"scheme + host[:port] + an optional path (e.g. https://tracker.example or "
            f"https://tracker.example/vikunja) — the /api/v1 suffix is appended for you. "
            f"Written with a '?' or '#', the suffix lands INSIDE the query or fragment and "
            f"every request goes to the instance root instead of the API, which surfaces as "
            f"404s or HTML rather than as a config error. Drop everything from the first "
            f"'?' or '#'."
        )
    try:
        project_id = int(raw_pid)
    except (TypeError, ValueError):
        raise ConfigError(
            f"VIKUNJA_PROJECT_ID/project_id must be a number, got {raw_pid!r}"
        )

    raw_limit = repo.get("wip_limit")
    wip_limit: int | None = None
    if raw_limit is not None:
        try:
            wip_limit = int(raw_limit)
        except (TypeError, ValueError):
            raise ConfigError(f"wip_limit must be a number, got {raw_limit!r}")
        if wip_limit < 1:
            raise ConfigError(
                f"wip_limit must be >= 1 (got {wip_limit}) — omit the key for the "
                f"default of {DEFAULT_WIP_LIMIT}; there is no spelling for 'no limit'"
            )

    worktree_root = (
        env.get(ENV_WORKTREE_ROOT)
        or repo_env.get(ENV_WORKTREE_ROOT)
        or repo.get("worktree_root")
        or user.get(ENV_WORKTREE_ROOT)
        or None
    )

    return Config(
        url=str(url), token=str(token),
        project_id=project_id, project_name=repo.get("project"),
        enforce_single_wip=bool(repo.get("enforce_single_wip", False)),
        notify_webhook=notify_webhook,
        wip_limit=wip_limit,
        worktree_root=worktree_root,
    )
