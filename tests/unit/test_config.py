import pytest

from vikunja_mcp.config import (
    DEFAULT_WIP_LIMIT,
    Config,
    ConfigError,
    _parse_env_file,
    load_config,
)


def _write_toml(path, project_id=3, url="https://tracker.zz.hgdev.com"):
    path.joinpath(".vikunja-mcp.toml").write_text(
        f'[tracker]\nurl = "{url}"\nproject_id = {project_id}\nproject = "hgdev-infra"\n'
    )


def test_reads_repo_toml_and_env_token(tmp_path):
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk_secret"})
    assert cfg == Config(
        url="https://tracker.zz.hgdev.com", token="tk_secret",
        project_id=3, project_name="hgdev-infra",
    )


def test_walks_up_to_find_toml(tmp_path):
    _write_toml(tmp_path)
    deep = tmp_path / "roles" / "vikunja"
    deep.mkdir(parents=True)
    cfg = load_config(cwd=deep, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.project_id == 3


def test_env_overrides_toml(tmp_path):
    _write_toml(tmp_path, project_id=3)
    cfg = load_config(cwd=tmp_path, environ={
        "VIKUNJA_TOKEN": "tk",
        "VIKUNJA_URL": "https://tracker.vpn.hgdev.com",
        "VIKUNJA_PROJECT_ID": "7",
    })
    assert cfg.url == "https://tracker.vpn.hgdev.com"
    assert cfg.project_id == 7


def test_user_env_file_supplies_token(tmp_path, monkeypatch):
    _write_toml(tmp_path)
    user_file = tmp_path / "userenv"
    user_file.write_text("# comment\nVIKUNJA_TOKEN=tk_from_file\n\nOTHER=x\n")
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", user_file)
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk_from_file"


def test_env_token_beats_user_file(tmp_path, monkeypatch):
    _write_toml(tmp_path)
    user_file = tmp_path / "userenv"
    user_file.write_text("VIKUNJA_TOKEN=file_tk\n")
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", user_file)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "env_tk"})
    assert cfg.token == "env_tk"


def test_missing_token_raises_with_hint(tmp_path):
    _write_toml(tmp_path)
    with pytest.raises(ConfigError, match="VIKUNJA_TOKEN"):
        load_config(cwd=tmp_path, environ={})


def test_missing_toml_and_env_raises(tmp_path):
    with pytest.raises(ConfigError, match="vikunja-mcp.toml"):
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})


def test_env_only_no_toml_works(tmp_path):
    cfg = load_config(cwd=tmp_path, environ={
        "VIKUNJA_TOKEN": "tk", "VIKUNJA_URL": "http://x", "VIKUNJA_PROJECT_ID": "5",
    })
    assert cfg.project_id == 5 and cfg.project_name is None


# --- F4: quotes / inline comments in the user env file ---

def test_env_file_strips_surrounding_double_quotes(tmp_path):
    path = tmp_path / "userenv"
    path.write_text('VIKUNJA_TOKEN="abc"\n')
    assert _parse_env_file(path)["VIKUNJA_TOKEN"] == "abc"


def test_env_file_strips_surrounding_single_quotes(tmp_path):
    path = tmp_path / "userenv"
    path.write_text("VIKUNJA_TOKEN='abc'\n")
    assert _parse_env_file(path)["VIKUNJA_TOKEN"] == "abc"


def test_env_file_strips_trailing_comment_on_unquoted_value(tmp_path):
    path = tmp_path / "userenv"
    path.write_text("VIKUNJA_TOKEN=abc # note\n")
    assert _parse_env_file(path)["VIKUNJA_TOKEN"] == "abc"


def test_env_file_keeps_hash_inside_quotes(tmp_path):
    """Кавычки защищают значение — # внутри них не комментарий."""
    path = tmp_path / "userenv"
    path.write_text('VIKUNJA_TOKEN="abc # not a comment"\n')
    assert _parse_env_file(path)["VIKUNJA_TOKEN"] == "abc # not a comment"


# --- F5: bad VIKUNJA_PROJECT_ID ---

def test_bad_project_id_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="VIKUNJA_PROJECT_ID/project_id must be a number"):
        load_config(cwd=tmp_path, environ={
            "VIKUNJA_TOKEN": "tk", "VIKUNJA_URL": "http://x", "VIKUNJA_PROJECT_ID": "abc",
        })


# --- #39: repo-local .vikunja-mcp.env layer (env > repo-env > repo toml > user file) ---

def _write_repo_env(path, **kv):
    lines = "\n".join(f"{k}={v}" for k, v in kv.items())
    path.joinpath(".vikunja-mcp.env").write_text(lines + "\n")


def test_repo_env_supplies_token_when_user_file_empty(tmp_path):
    _write_toml(tmp_path)
    _write_repo_env(tmp_path, VIKUNJA_TOKEN="tk_repo_env")
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk_repo_env"


def test_env_beats_repo_env(tmp_path):
    _write_toml(tmp_path)
    _write_repo_env(tmp_path, VIKUNJA_TOKEN="tk_repo_env")
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk_env"})
    assert cfg.token == "tk_env"


def test_repo_env_beats_user_file(tmp_path, monkeypatch):
    _write_toml(tmp_path)
    _write_repo_env(tmp_path, VIKUNJA_TOKEN="tk_repo_env")
    user_file = tmp_path / "userenv"
    user_file.write_text("VIKUNJA_TOKEN=tk_user\n")
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", user_file)
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk_repo_env"


def test_repo_env_found_via_walkup_from_subdirectory(tmp_path):
    """Один walk-up (тот же, что ищет toml) — repo-env лежит рядом с найденным toml."""
    _write_toml(tmp_path)
    _write_repo_env(tmp_path, VIKUNJA_TOKEN="tk_repo_env")
    deep = tmp_path / "roles" / "vikunja"
    deep.mkdir(parents=True)
    cfg = load_config(cwd=deep, environ={})
    assert cfg.token == "tk_repo_env"
    assert cfg.project_id == 3


def test_repo_env_quotes_and_trailing_comment(tmp_path):
    """Переиспользует _parse_env_file — те же правила кавычек/# что и у user env file."""
    _write_toml(tmp_path)
    tmp_path.joinpath(".vikunja-mcp.env").write_text(
        'VIKUNJA_TOKEN="tk quoted # not a comment"\n'
    )
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk quoted # not a comment"


def test_repo_env_url_and_project_id_override_toml(tmp_path):
    _write_toml(tmp_path, project_id=3, url="https://tracker.zz.hgdev.com")
    _write_repo_env(
        tmp_path,
        VIKUNJA_URL="https://tracker.override.example",
        VIKUNJA_PROJECT_ID="99",
        VIKUNJA_TOKEN="tk",
    )
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.url == "https://tracker.override.example"
    assert cfg.project_id == 99


def test_repo_env_must_be_beside_toml_not_elsewhere(tmp_path):
    """Не отдельный walk-up: .vikunja-mcp.env в cwd, но не рядом с найденным toml, — игнорируется."""
    _write_toml(tmp_path)
    deep = tmp_path / "roles" / "vikunja"
    deep.mkdir(parents=True)
    _write_repo_env(deep, VIKUNJA_TOKEN="tk_wrong_place")
    with pytest.raises(ConfigError, match="VIKUNJA_TOKEN"):
        load_config(cwd=deep, environ={})


def test_no_repo_env_file_behavior_unchanged(tmp_path, monkeypatch):
    _write_toml(tmp_path)
    user_file = tmp_path / "userenv"
    user_file.write_text("VIKUNJA_TOKEN=tk_from_file\n")
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", user_file)
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk_from_file"


# --- #252: notify_webhook — Slack-compatible YC ping URL, a secret of the token's class ---

def test_notify_webhook_defaults_none(tmp_path):
    """Absent everywhere -> the feature ships off, no error."""
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.notify_webhook is None


def test_notify_webhook_from_env(tmp_path):
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={
        "VIKUNJA_TOKEN": "tk", "VIKUNJA_NOTIFY_WEBHOOK": "https://hooks.example/env",
    })
    assert cfg.notify_webhook == "https://hooks.example/env"


def test_notify_webhook_from_repo_env(tmp_path):
    _write_toml(tmp_path)
    _write_repo_env(
        tmp_path, VIKUNJA_TOKEN="tk", VIKUNJA_NOTIFY_WEBHOOK="https://hooks.example/repo",
    )
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.notify_webhook == "https://hooks.example/repo"


def test_notify_webhook_from_user_env_file(tmp_path, monkeypatch):
    _write_toml(tmp_path)
    user_file = tmp_path / "userenv"
    user_file.write_text(
        "VIKUNJA_TOKEN=tk\nVIKUNJA_NOTIFY_WEBHOOK=https://hooks.example/user\n"
    )
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", user_file)
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.notify_webhook == "https://hooks.example/user"


def test_notify_webhook_env_beats_repo_env(tmp_path):
    _write_toml(tmp_path)
    _write_repo_env(
        tmp_path, VIKUNJA_TOKEN="tk", VIKUNJA_NOTIFY_WEBHOOK="https://hooks.example/repo",
    )
    cfg = load_config(
        cwd=tmp_path, environ={"VIKUNJA_NOTIFY_WEBHOOK": "https://hooks.example/env"},
    )
    assert cfg.notify_webhook == "https://hooks.example/env"


def test_notify_webhook_never_read_from_toml(tmp_path):
    """Вебхук-URL — секрет того же класса, что и токен (кто держит URL, тот постит в канал
    людей): из КОММИТИМОГО toml он не читается никогда, только из env-слоёв — иначе публичный
    репозиторий с toml утёк бы URL так же, как утёк бы токен."""
    tmp_path.joinpath(".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\n'
        'notify_webhook = "https://hooks.example/leaked"\n'
    )
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.notify_webhook is None


# --- #38: enforce_single_wip policy flag (committed in the toml, default off) ---

def test_enforce_single_wip_defaults_false(tmp_path):
    """Absent from the toml -> the WIP gate ships inert."""
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.enforce_single_wip is False


def test_enforce_single_wip_reads_true_from_toml(tmp_path):
    tmp_path.joinpath(".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nenforce_single_wip = true\n'
    )
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.enforce_single_wip is True


# --- wip_limit: the parallel-drain slot count (committed in the toml, generalises #38) ---

def test_an_absent_wip_limit_stays_none_at_the_config_layer(tmp_path):
    """None here means "the key is absent", NOT "no gate" (that is what this test used to pin,
    before tracker #524 made an unset key mean DEFAULT_WIP_LIMIT). The number is deliberately
    resolved one layer up, in Workflow._effective_wip_limit: if load_config substituted the
    default, `enforce_single_wip = true` could never be reached and the legacy flag would
    silently widen from 1 to 3. Absence has to stay visible for that precedence to exist."""
    (tmp_path / ".vikunja-mcp.toml").write_text('[tracker]\nurl = "http://x"\nproject_id = 3\n')
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    assert load_config(cwd=tmp_path, environ={}).wip_limit is None


def test_wip_limit_reads_from_toml(tmp_path):
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nwip_limit = 3\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    assert load_config(cwd=tmp_path, environ={}).wip_limit == 3


def test_wip_limit_is_never_read_from_env(tmp_path):
    """Committed TEAM POLICY, like enforce_single_wip: a machine-local env var must not
    quietly widen another repo's slot count. Absent from the toml stays absent (None) even
    with the env var set, so the effective limit is the default — never the env's 9."""
    (tmp_path / ".vikunja-mcp.toml").write_text('[tracker]\nurl = "http://x"\nproject_id = 3\n')
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_WIP_LIMIT": "9"})
    assert cfg.wip_limit is None


def test_wip_limit_below_one_is_a_config_error(tmp_path):
    """0 slots would silently wedge every claim — fail loudly at load instead."""
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nwip_limit = 0\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    with pytest.raises(ConfigError, match="wip_limit"):
        load_config(cwd=tmp_path, environ={})


def test_the_below_one_refusal_names_the_default_and_not_no_limit(tmp_path):
    """The refusal is the only place the config layer TELLS a human what omitting the key does,
    and it used to say "omit the key entirely for no limit" — false since tracker #524, and
    exactly the kind of stale sentence that teaches the wrong contract. It must name the
    default, and must not offer "no limit" at all: 0 is not the unbounded spelling, there is
    none. Reads DEFAULT_WIP_LIMIT so the message follows the constant instead of drifting."""
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nwip_limit = 0\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    with pytest.raises(ConfigError) as err:
        load_config(cwd=tmp_path, environ={})
    assert f"default of {DEFAULT_WIP_LIMIT}" in str(err.value)
    assert "for no limit" not in str(err.value)


# --- #37: require_review_independence (committed team policy, toml ONLY, default off) ---

def test_require_review_independence_defaults_false(tmp_path):
    """Absent from the toml -> the gate ships INERT, and that default is load-bearing rather
    than cautious: in a solo setup one token is both implementer and reviewer, so a gate that
    were on by default would refuse EVERY review here and at every consumer on `stable`."""
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.require_review_independence is False


def test_require_review_independence_reads_true_from_toml(tmp_path):
    tmp_path.joinpath(".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nrequire_review_independence = true\n'
    )
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.require_review_independence is True


def test_require_review_independence_is_never_read_from_env(tmp_path):
    """Committed TEAM POLICY of the wip_limit class, NOT a machine-local knob like
    worktree_root: whether review independence is enforced is a property of the PROJECT, so it
    lives in a file the whole team reviews. Both directions are pinned, because either one
    alone would pass a broken implementation: an env var must not turn the gate ON where the
    toml is silent (below), and must not turn it OFF where the toml says true (second half) —
    a machine that could opt out of its team's gate by exporting a variable would make the
    gate worthless exactly where it matters."""
    (tmp_path / ".vikunja-mcp.toml").write_text('[tracker]\nurl = "http://x"\nproject_id = 3\n')
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    cfg = load_config(
        cwd=tmp_path, environ={"VIKUNJA_REQUIRE_REVIEW_INDEPENDENCE": "true"}
    )
    assert cfg.require_review_independence is False

    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nrequire_review_independence = true\n'
    )
    cfg = load_config(
        cwd=tmp_path, environ={"VIKUNJA_REQUIRE_REVIEW_INDEPENDENCE": "false"}
    )
    assert cfg.require_review_independence is True


def test_the_repo_env_file_cannot_set_review_independence_either(tmp_path):
    """The repo-local .vikunja-mcp.env is an ENV layer (it carries the token), so it is on the
    secret side of the split, not the policy side. Pinned separately from the process env
    because it sits in the repo DIRECTORY and so reads like a committed file — it is not one
    (it is gitignored), and policy must not be settable from it."""
    (tmp_path / ".vikunja-mcp.toml").write_text('[tracker]\nurl = "http://x"\nproject_id = 3\n')
    (tmp_path / ".vikunja-mcp.env").write_text(
        "VIKUNJA_TOKEN=t\nVIKUNJA_REQUIRE_REVIEW_INDEPENDENCE=true\n"
    )
    assert load_config(cwd=tmp_path, environ={}).require_review_independence is False


def test_wip_limit_non_numeric_is_a_config_error(tmp_path):
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nwip_limit = "many"\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    with pytest.raises(ConfigError, match="wip_limit"):
        load_config(cwd=tmp_path, environ={})


# --- worktree_root: MACHINE-local path for per-task git worktrees (parallel drain) ---

def test_worktree_root_defaults_to_none(tmp_path):
    (tmp_path / ".vikunja-mcp.toml").write_text('[tracker]\nurl = "http://x"\nproject_id = 3\n')
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    assert load_config(cwd=tmp_path, environ={}).worktree_root is None


def test_worktree_root_reads_from_toml(tmp_path):
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nworktree_root = "../wt"\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    assert load_config(cwd=tmp_path, environ={}).worktree_root == "../wt"


def test_env_overrides_worktree_root(tmp_path):
    """Unlike wip_limit (team policy), the worktree location is MACHINE-local — env wins."""
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nworktree_root = "../wt"\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_WORKTREE_ROOT": "/srv/trees"})
    assert cfg.worktree_root == "/srv/trees"


# --- VMCP-225 (768): a base url carrying a query or fragment can never reach the API ----------

@pytest.mark.parametrize(
    "url,expected_word",
    [
        ("https://tracker.example?Token=Ab", "query"),
        ("https://tracker.example#Frag", "fragment"),
        ("https://tracker.example?a=1#f", "query"),
        ("https://tracker.example:3456?a=1", "query"),
        ("https://tracker.example/vikunja?a=1", "query"),
        ("https://tracker.example/api/v1?x=1", "query"),
    ],
    ids=["query", "fragment", "both", "query-behind-a-port", "query-after-a-path",
         "query-after-the-suffix-itself"],
)
def test_a_url_with_a_query_or_fragment_is_refused_at_config_time(tmp_path, url, expected_word):
    """MEASURED before the fix, through the real client: `canonical_base_url` appends `/api/v1`
    to the END OF THE STRING, so

        https://h?Token=Ab   -> https://h?Token=Ab/api/v1     raw_path `/?Token=Ab/api/v1`
        https://h#Frag       -> https://h#Frag/api/v1         raw_path `/`   (suffix never sent)
        https://h/api/v1?x=1 -> https://h/api/v1?x=1/api/v1   (appended TWICE)

    Every one of those talks to the instance ROOT, so the failure surfaces as a 404 or a page of
    HTML rather than as a config error anyone can act on. The same append also makes the
    canonicalisation NON-INJECTIVE here, which is why the #148 repoint guard read
    `https://h?a=b` and `https://h?a=b/api/v1` as one endpoint.

    Refused rather than repaired: inserting the suffix into the PATH would make the client work
    while silently attaching a query nobody meant to send to EVERY API call.

    `query-after-the-suffix-itself` is the row that would survive a narrower fix aimed only at
    "url has no path".

    MUTATION-CHECKED, selection `tests/unit/test_config.py`, `__pycache__` deleted and then
    PYTHONDONTWRITEBYTECODE=1, each round restored from a byte copy and the file confirmed
    sha256-identical; the script refuses unless the guard matches exactly once. Control round:
    0 failed.
      * remove the guard entirely -> 6 failed, i.e. every row here and nothing else
      * keep only `?` (drop the fragment half) -> 1 failed, the `fragment` row alone
      * keep only `#` (drop the query half) -> 4 failed, the four query-bearing rows
      * pin the message's noun to the constant "query" -> 1 failed, the `fragment` row, which
        is what makes the WORD real rather than only the refusal
      * INVERT the noun picker (`"fragment" if "#" in url else "query"`) -> 1 failed, and that
        row is `both` ALONE — the round that gives that row a job, see below
    The two half-rounds are what say the terminators are pinned SEPARATELY rather than by one
    row that happens to carry both, and what carries that is NOT disjointness on its own — two
    disjoint sets, one of them empty, would say nothing — but that both are NON-EMPTY and
    disjoint: `{fragment}` against the four query-bearing rows. They do not sum to the first
    round, and the arithmetic is worth stating because an earlier draft of this docstring got
    it wrong: 1 + 4 is FIVE of the six. The row left out is `both`, which carries BOTH
    terminators and so is still refused by either half of the guard. That makes it silent about
    SEPARATENESS — but not idle, and the difference is one round: inverting the picker reddens
    `both` and nothing else, so it is the one row pinning WHICH noun a url carrying both
    terminators is told about. It earned that only here, from the `expected_word` assertion.
    """
    _write_toml(tmp_path)
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk", "VIKUNJA_URL": url})
    assert f"must not carry a {expected_word}" in str(exc.value)
    assert url in str(exc.value), "the refusal must quote the offending url back"


@pytest.mark.parametrize(
    "url",
    [
        "https://tracker.example",
        "https://tracker.example/",
        "https://tracker.example:3456",
        "https://tracker.example/vikunja",
        "https://tracker.example/api/v1",
        "http://localhost:3456",
    ],
)
def test_ordinary_urls_are_untouched_by_the_query_guard(tmp_path, url):
    """The control. A guard that also refused a legitimate url would be worse than the hole."""
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk", "VIKUNJA_URL": url})
    assert cfg.url == url


def test_the_normalizer_itself_stays_total(tmp_path):
    """The BOUNDARY of this fix, asserted so it cannot be mistaken for a wider claim.

    `canonical_base_url` still raises nothing on these shapes and still produces the broken
    string — the refusal lives at the config layer, not in the normalizer. That is deliberate:
    the normalizer raising NOTHING is the property that ONE of the four counts in its own
    docstring's argument against `urllib.parse.urlsplit` rests on. So a caller who bypasses the
    config and constructs `VikunjaAPI("https://h?x=1", tok)` by hand still gets an unusable
    client — and so does `setup --url`, which is not a hand-written constructor call at all but
    a second real CLI entry point, building its client straight from the argument without
    calling `load_config`. So what the config layer closes is narrower than "the product's
    ENTRANCE", which is how an earlier draft of this docstring put it: it closes the
    SILENT-CLIENT class on every path that READS config, while being LOUD on only three of the
    five such paths (the call-site list in `config.py` has the measurements) — `workspace`
    create and release still exit 0 on such a url, and `setup --url` never reads config at all.
    """
    from vikunja_mcp.api import canonical_base_url

    assert canonical_base_url("https://h?Token=Ab") == "https://h?Token=Ab/api/v1"
    assert canonical_base_url("https://h#Frag") == "https://h#Frag/api/v1"
