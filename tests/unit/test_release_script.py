"""Стенд для `scripts/release.sh`: релизный путь проверяется ЗАПУСКОМ (tracker #716).

Почему тест такой тяжёлый (настоящий bare-репозиторий, настоящие клоны, настоящие
push'и): единственный отказ, который здесь важен, — гонка ДВУХ релизных job'ов, а её
нельзя ни подделать фейком, ни вычитать из диффа. Прогон 30754732335 (sha ``0664256f``)
умер на ``fatal: tag 'v0.2.171' already exists``, потому что коммиты ``0664256`` и
``75a1e52`` оба сидели на базе ``v0.2.170`` — второй приземлился поверх первого РАНЬШЕ,
чем приземлился bump первого, — и оба честно посчитали следующий патч одинаково.

Стенд воспроизводит ровно эту форму, а мутация (снятие ЕДИНСТВЕННОЙ строки решения
гейта) обязана вернуть дословный текст той ошибки: тест, который не краснеет от снятия
защиты, сертифицирует не защиту, а собственную зелень.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_SH = REPO_ROOT / "scripts" / "release.sh"
BUMP_PY = REPO_ROOT / "scripts" / "bump_version.py"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

BASE_VERSION = "0.2.170"
NEXT_VERSION = "v0.2.171"

# Единственная строка решения гейта. Мутация заменяет её на `false` («меня никогда не
# накрывали»), то есть на поведение ДО фикса. Переименуют переменные — тест упадёт на
# _mutated_release_sh, а не тихо перестанет мутировать.
DECISION_LINE = '    [ "$tip" != "$GITHUB_SHA" ] && git merge-base --is-ancestor "$GITHUB_SHA" "$tip"\n'


def _env() -> dict[str, str]:
    """Изолированный git: ни пользовательского конфига, ни подсказок терминала."""
    env = dict(os.environ)
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_AUTHOR_NAME="stand",
        GIT_AUTHOR_EMAIL="stand@example.com",
        GIT_COMMITTER_NAME="stand",
        GIT_COMMITTER_EMAIL="stand@example.com",
        GIT_TERMINAL_PROMPT="0",
    )
    return env


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_env(), capture_output=True, text=True, check=check
    )


def _write_version_files(root: Path, version: str) -> None:
    (root / "src" / "vikunja_mcp").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "vikunja-mcp"\nversion = "{version}"\n')
    (root / "src" / "vikunja_mcp" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (root / "uv.lock").write_text(f'[[package]]\nname = "vikunja-mcp"\nversion = "{version}"\n')


def _mutated_release_sh() -> str:
    text = RELEASE_SH.read_text()
    assert text.count(DECISION_LINE) == 1, (
        "строка решения гейта не найдена дословно ровно один раз в scripts/release.sh — "
        "мутация ничего не снимет, и тест мутации станет тавтологией"
    )
    return text.replace(DECISION_LINE, "    false\n")


@pytest.fixture
def stand(tmp_path: Path):
    """bare origin + один коммит с версией 0.2.170 и настоящими scripts/ из репо."""

    class Stand:
        def __init__(self) -> None:
            self.root = tmp_path
            self.origin = tmp_path / "origin.git"
            _git(tmp_path, "init", "--bare", "-q", "-b", "main", str(self.origin))
            seed = tmp_path / "seed"
            _git(tmp_path, "clone", "-q", str(self.origin), str(seed))
            _write_version_files(seed, BASE_VERSION)
            self.install_scripts(seed)
            _git(seed, "add", "-A")
            _git(seed, "commit", "-qm", "base")
            _git(seed, "push", "-q", "origin", "main")
            self.seed = seed
            self.c0 = _git(seed, "rev-parse", "HEAD").stdout.strip()

        def install_scripts(self, root: Path, release_sh: str | None = None) -> None:
            scripts = root / "scripts"
            scripts.mkdir(exist_ok=True)
            shutil.copy(BUMP_PY, scripts / "bump_version.py")
            (scripts / "release.sh").write_text(
                RELEASE_SH.read_text() if release_sh is None else release_sh
            )

        def checkout(self, name: str, sha: str, release_sh: str | None = None) -> Path:
            """Чекаут «как actions/checkout@v4»: полная история, все теги, detached на sha."""
            work = self.root / name
            _git(self.root, "clone", "-q", str(self.origin), str(work))
            _git(work, "fetch", "-q", "--tags", "origin")
            _git(work, "checkout", "-q", "--detach", sha)
            if release_sh is not None:
                self.install_scripts(work, release_sh)
            return work

        def release_job(self, work: Path, sha: str) -> subprocess.CompletedProcess[str]:
            """Ровно то, что делает job `release`: bump_version.py -> release.sh."""
            bumped = subprocess.run(
                [sys.executable, "scripts/bump_version.py"],
                cwd=work, env=_env(), capture_output=True, text=True, check=True,
            )
            version = bumped.stdout.strip().splitlines()[-1]
            env = _env()
            env["VERSION"] = version
            env["GITHUB_SHA"] = sha
            # ci.yml зовёт `sh scripts/release.sh`, а на ubuntu-раннере /bin/sh — это
            # dash, не bash. Где dash есть, прогнать пин под ним можно так:
            # RELEASE_SHELL=dash uv run pytest tests/unit/test_release_script.py
            return subprocess.run(
                [os.environ.get("RELEASE_SHELL", "sh"), "scripts/release.sh"],
                cwd=work, env=env, capture_output=True, text=True, check=False,
            )

        def land_sibling(self, name: str, release: bool = False) -> str:
            """Сиблинг приземляет свой таск-коммит поверх main (и, опционально, релизится)."""
            clone = self.root / name
            _git(self.root, "clone", "-q", str(self.origin), str(clone))
            (clone / "note.txt").write_text(name)
            _git(clone, "add", "-A")
            _git(clone, "commit", "-qm", f"task commit {name}")
            _git(clone, "push", "-q", "origin", "HEAD:main")
            sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
            if release:
                work = self.checkout(f"{name}-job", sha)
                done = self.release_job(work, sha)
                assert done.returncode == 0, done.stderr
            return sha

        def remote_main(self) -> str:
            # refs/heads/main, а не main: на стенде бывает ТЕГ с этим же именем.
            return _git(self.origin, "rev-parse", "refs/heads/main").stdout.strip()

        def remote_tags(self) -> list[str]:
            """Только версионные теги: на стенде бывает и посторонний тег `main`."""
            return _git(self.origin, "tag", "--list", "v*").stdout.split()

        def remote_stable(self) -> str | None:
            got = _git(self.origin, "rev-parse", "--verify", "-q", "refs/heads/stable", check=False)
            return got.stdout.strip() or None

        def remote_file(self, ref: str, path: str) -> str:
            return _git(self.origin, "show", f"{ref}:{path}").stdout

    return Stand()


def test_tip_of_main_releases_normally(stand):
    """Обычное приземление: bump поверх позеленевшего sha, тег, stable — всё как было."""
    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode == 0, done.stderr
    tip = stand.remote_main()
    assert tip != stand.c0
    assert _git(stand.origin, "rev-parse", f"{tip}^").stdout.strip() == stand.c0
    assert stand.remote_tags() == [NEXT_VERSION]
    assert _git(stand.origin, "rev-list", "-n1", NEXT_VERSION).stdout.strip() == tip
    assert stand.remote_stable() == tip
    # bump трогает ВСЕ ТРИ version-файла — иначе `uv sync --locked` покраснеет на локе.
    for path in ("pyproject.toml", "src/vikunja_mcp/__init__.py", "uv.lock"):
        assert '"0.2.171"' in stand.remote_file("refs/heads/main", path)


def test_superseded_with_the_tag_already_taken_skips(stand):
    """Форма прогона 30754732335: сиблинг уже занял тег. Гейт — тихий выход, ноль push'ей."""
    stand.land_sibling("sib", release=True)
    before = (stand.remote_main(), stand.remote_tags(), stand.remote_stable())

    work = stand.checkout("w", stand.c0)
    assert f'"{BASE_VERSION}"' in (work / "src" / "vikunja_mcp" / "__init__.py").read_text()
    done = stand.release_job(work, stand.c0)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" in done.stdout
    assert (stand.remote_main(), stand.remote_tags(), stand.remote_stable()) == before


def test_superseded_with_the_tag_still_free_also_skips(stand):
    """Сиблинг приземлился, но не релизился: имя тега СВОБОДНО, а релиз всё равно не мой.

    Это тот случай, на котором ломаются «пересчитать версию» варианты: имя можно взять
    любое, но bump сидит поверх НЕ-вершины, поэтому push в main всё равно non-fast-forward
    (см. test_without_the_guard_a_free_tag_name_does_not_help).
    """
    sibling = stand.land_sibling("sib")

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" in done.stdout
    assert stand.remote_main() == sibling
    assert stand.remote_tags() == []
    assert stand.remote_stable() is None


def test_lost_race_after_the_pre_push_check_still_skips(stand):
    """Остаточное окно: main уехал МЕЖДУ проверкой и push'ем — ловит перепроверка.

    Сиблинг приземляется ровно в момент push'а (pre-receive хук на стенде): гейт перед
    `git tag` его ещё не видел, поэтому решение принимает вторая проверка — та, что стоит
    после отбитого push'а. Тег к этому моменту создан ЛОКАЛЬНО, но push'ей не было ни
    одного, поэтому на remote по-прежнему пусто.
    """
    clone = stand.root / "sib"
    _git(stand.root, "clone", "-q", str(stand.origin), str(clone))
    (clone / "note.txt").write_text("sibling")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "task commit sib")
    _git(clone, "push", "-q", "origin", "HEAD:refs/heads/sib")
    sibling = _git(clone, "rev-parse", "HEAD").stdout.strip()

    hook = stand.origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "if [ -f arm ]; then\n"
        "  rm -f arm\n"
        # git запрещает update-ref из pre-receive, пока выставлен GIT_QUARANTINE_PATH
        # ("ref updates forbidden inside quarantine environment") — снимаем его, чтобы
        # сиблинг «приземлился» ровно посреди чужого push'а.
        "  unset GIT_QUARANTINE_PATH\n"
        '  git update-ref refs/heads/main "$(git rev-parse refs/heads/sib)"\n'
        '  echo "stand: sibling landed mid-push" >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0\n"
    )
    hook.chmod(0o755)
    (stand.origin / "arm").write_text("")

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" in done.stdout
    assert stand.remote_main() == sibling
    assert stand.remote_tags() == []
    assert stand.remote_stable() is None


def test_without_the_guard_the_tag_collision_comes_back(stand):
    """МУТАЦИЯ: снимаем строку решения — стенд обязан вернуть дословную ошибку прогона."""
    stand.land_sibling("sib", release=True)
    before = (stand.remote_main(), stand.remote_tags(), stand.remote_stable())

    work = stand.checkout("w", stand.c0, release_sh=_mutated_release_sh())
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0
    assert f"fatal: tag '{NEXT_VERSION}' already exists" in done.stderr
    # Даже падая, шаг ничего не пушит: `git tag` стоит ДО всех четырёх push'ей.
    assert (stand.remote_main(), stand.remote_tags(), stand.remote_stable()) == before


def test_without_the_guard_a_free_tag_name_does_not_help(stand):
    """МУТАЦИЯ на свободном имени тега: отказ просто переезжает на push в main.

    Это и есть измеренная причина отвергнуть «пересчитать версию» (направления 1/2/4 из
    карточки): чинится ИМЯ, а не то, что bump сидит поверх не-вершины.
    """
    sibling = stand.land_sibling("sib")

    work = stand.checkout("w", stand.c0, release_sh=_mutated_release_sh())
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0
    assert "non-fast-forward" in done.stderr
    assert stand.remote_main() == sibling
    assert stand.remote_tags() == []


def test_a_tip_that_does_not_contain_us_is_not_superseded(stand):
    """Пинит КОНЪЮНКТ `is-ancestor`: «вершина другая» — ещё не «меня накрыли».

    Без него любое расхождение читалось бы как накрытие, и `main`, переписанный на
    ЧУЖУЮ линию, уходил бы в тихий зелёный skip вместо громкого отказа. Мутационный
    свип второго прохода показал, что снятие конъюнкта не убивало НИ ОДНОГО теста.
    """
    other = stand.root / "other"
    _git(stand.root, "clone", "-q", str(stand.origin), str(other))
    _git(other, "checkout", "-q", "--orphan", "unrelated")
    _write_version_files(other, BASE_VERSION)
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "unrelated line")
    _git(other, "push", "-q", "-f", "origin", "HEAD:refs/heads/main")
    rewritten = _git(other, "rev-parse", "HEAD").stdout.strip()

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0, done.stdout
    assert "release skipped" not in done.stdout
    assert stand.remote_main() == rewritten
    assert stand.remote_tags() == []


def test_a_tag_named_main_does_not_fake_a_supersession(stand):
    """`git fetch origin main` при теге с именем `main` уводит на ТЕГ (tracker #716).

    `rev-parse FETCH_HEAD` отдаёт объект тега — он не равен sha коммита, а
    `merge-base --is-ancestor` его разыменовывает и отвечает «X предок X». Гейт на
    полном рефспеке и с `^{commit}` обязан этого не заметить и выпустить релиз.
    """
    _git(stand.seed, "tag", "-a", "main", "-m", "tag named main", stand.c0)
    _git(stand.seed, "push", "-q", "origin", "refs/tags/main")

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" not in done.stdout
    assert NEXT_VERSION in stand.remote_tags()
    assert stand.remote_stable() == stand.remote_main()


def test_a_refused_tag_push_is_loud(stand):
    """Пинит `set -eu`: отказ на ВТОРОМ push'е обязан быть красным, а не тихим.

    Полу-состояние (bump в `main`, тега нет, `stable` не двинулся) этот шаг умеет
    оставлять и до, и после гейта — оно вынесено отдельной карточкой. Здесь пинится
    ровно одно: что оно ГРОМКОЕ. Без `set -eu` тот же прогон отдаёт код 0.
    """
    hook = stand.origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read -r old new ref; do\n"
        '  case "$ref" in refs/tags/*) echo "refused $ref" >&2; exit 1;; esac\n'
        "done\n"
        "exit 0\n"
    )
    hook.chmod(0o755)

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0, done.stdout
    assert stand.remote_tags() == []
    assert stand.remote_stable() is None
    assert '"0.2.171"' in stand.remote_file("refs/heads/main", "src/vikunja_mcp/__init__.py")


def test_the_script_keeps_its_two_unpinned_release_properties():
    """Две вещи, которые поведенческий стенд не ловит, а снятие которых дорого стоит.

    (1) ci-skip-маркер в теме bump-коммита — второй пояс против рекурсии CI (первый —
    то, что GITHUB_TOKEN не ретриггерит прогон, и стендом он не воспроизводится).
    (2) push в главную ветку НЕ форсируется: измерено вторым проходом, что с
    `--force` таск-коммит сиблинга, приземлившийся в окне гонки, ПРОПАДАЕТ.
    Форсируется здесь ровно один ref — `stable`, и это by design.
    """
    text = RELEASE_SH.read_text()
    assert "[skip" + " ci]" in text, "тема bump-коммита обязана нести ci-skip-маркер"

    forced = [ln for ln in text.splitlines() if "git push" in ln and ("-f " in ln or "--force" in ln)]
    assert len(forced) == 1 and "refs/heads/stable" in forced[0], forced


def test_ci_calls_the_script_and_pushes_nothing_itself():
    """Гейт бесполезен, если рядом останется push мимо него: ci.yml не пушит сам."""
    text = CI_YML.read_text()
    assert "sh scripts/release.sh" in text
    assert "git push" not in text, (
        "релизные push'и живут только в scripts/release.sh — push из ci.yml обойдёт гейт"
    )
