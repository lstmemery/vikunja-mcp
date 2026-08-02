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
import re
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

# ПЕРВЫЙ вопрос перепроверки — «а не приземлился ли мой push?» — двумя ветвями: «я всё
# ещё вершина» и «уехало, но поверх легло новее». Мутация гасит ОБЕ, и получается ровно
# круг 1 этой карточки: вопрос не задан, приземлившийся собственный bump читается как
# «меня накрыли», job зеленеет без тега и без stable (tracker #716).
LANDED_LINES = (
    '    if [ "$tip" = "$head" ]; then\n',
    '    elif git merge-base --is-ancestor "$head" "$tip"; then\n',
)

# ПОСЛЕДНИЙ push релизного пути — канала. Отсутствие `-f` тут и есть вся защита от отката
# канала назад: без него push fast-forward-only, git сам отказывается перевести stable на
# коммит, не содержащий текущей вершины канала. Мутация возвращает `-f`, то есть поведение
# ДО #737, и обязана вернуть измеренный откат.
STABLE_PUSH_LINE = (
    'if ! git push origin "refs/heads/${STABLE_BRANCH}:refs/heads/${STABLE_BRANCH}"; then\n'
)
FORCED_STABLE_PUSH_LINE = (
    'if ! git push -f origin "refs/heads/${STABLE_BRANCH}:refs/heads/${STABLE_BRANCH}"; then\n'
)


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


def _release_sh_without_the_landed_question() -> str:
    """Круг 1 этой карточки: перепроверка спрашивает «кто накрыл», не спросив «а не уехало ли»."""
    text = RELEASE_SH.read_text()
    for line, dead in zip(LANDED_LINES, ("    if false; then\n", "    elif false; then\n")):
        assert text.count(line) == 1, (
            f"ветвь первого вопроса {line!r} не найдена дословно ровно один раз в "
            "scripts/release.sh — мутация ничего не снимет, и тест мутации станет тавтологией"
        )
        text = text.replace(line, dead)
    return text


def _release_sh_with_a_forced_stable_push() -> str:
    """Поведение ДО #737: канал двигается безусловным форс-push'ем."""
    text = RELEASE_SH.read_text()
    assert text.count(STABLE_PUSH_LINE) == 1, (
        "push канала не найден дословно ровно один раз в scripts/release.sh — мутация "
        "ничего не снимет, и тест мутации станет тавтологией"
    )
    return text.replace(STABLE_PUSH_LINE, FORCED_STABLE_PUSH_LINE)


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

        def shim_push_lands_but_fails(self, *, then_land_sibling: bool = False) -> Path:
            """PATH-шим: push в main ВЫПОЛНЯЕТСЯ, а затем врёт отказом.

            Ровно тот отказ, который этот же CLAUDE.md уже описывает агентам: «сервер
            может принять ref и упасть уже на ответе (502, оборванное соединение) —
            клиент видит ошибку, коммит на главной». Подделывается тут ТОЛЬКО ответ
            клиента: сам ref едет на remote по-настоящему.
            """
            real = shutil.which("git")
            assert real, "git не найден в PATH"
            bin_dir = self.root / "shim"
            bin_dir.mkdir(exist_ok=True)
            hook = ""
            if then_land_sibling:
                clone = self.root / "sib-mid-push"
                _git(self.root, "clone", "-q", str(self.origin), str(clone))
                land = bin_dir / "land-sibling"
                land.write_text(
                    f"#!/bin/sh\nset -e\n"
                    f'"{real}" -C "{clone}" fetch -q origin\n'
                    f'"{real}" -C "{clone}" checkout -q -B main origin/main\n'
                    f'echo sib > "{clone}/note.txt"\n'
                    f'"{real}" -C "{clone}" add -A\n'
                    f'"{real}" -C "{clone}" commit -qm "task commit sib"\n'
                    f'"{real}" -C "{clone}" push -q origin HEAD:refs/heads/main\n'
                )
                land.chmod(0o755)
                hook = f'  "{land}"\n'
            (bin_dir / "git").write_text(
                "#!/bin/sh\n"
                'for a in "$@"; do\n'
                "  case \"$a\" in HEAD:refs/heads/main|HEAD:main)\n"
                f'    "{real}" "$@" >/dev/null 2>&1\n'
                f"{hook}"
                '    echo "fatal: the remote end hung up unexpectedly" >&2\n'
                "    exit 1;;\n"
                "  esac\n"
                "done\n"
                f'exec "{real}" "$@"\n'
            )
            (bin_dir / "git").chmod(0o755)
            return bin_dir

        def shim_stable_push(self, *, action: str) -> Path:
            """PATH-шим на ПОСЛЕДНЕМ push'е релиза — том, что двигает канал.

            `action` выбирает, ЧТО происходит в окне между решением job'а и этим push'ем:

            ``sibling_releases`` — туда впихивается ПОЛНЫЙ релиз сиблинга (таск-коммит
            поверх текущей вершины + его собственный job целиком: bump в main, тег, канал),
            после чего push выполняется по-настоящему. Это форма #737.

            ``lands_but_fails`` — push ВЫПОЛНЯЕТСЯ, а клиенту врём отказом (тот же обрыв
            на ответе, что уже воспроизводится на push'е в main).

            ``fails_and_breaks_origin`` — push НЕ выполняется, а origin становится
            недостижим: перепроверке нечем ответить, кто где стоит.
            """
            real = shutil.which("git")
            assert real, "git не найден в PATH"
            shell = os.environ.get("RELEASE_SHELL", "sh")
            bin_dir = self.root / f"shim-stable-{action}"
            bin_dir.mkdir()
            if action == "sibling_releases":
                clone = self.root / "sib-mid-stable"
                _git(self.root, "clone", "-q", str(self.origin), str(clone))
                marker = bin_dir / "done"
                body = (
                    f'if [ -e "{marker}" ]; then exit 0; fi\n'
                    f': > "{marker}"\n'
                    # БЕЗ шима в PATH: релиз сиблинга сам доходит до push'а канала, и
                    # рекурсия иначе была бы бесконечной.
                    f'PATH="{_env()["PATH"]}"; export PATH\n'
                    f'"{real}" -C "{clone}" fetch -q origin\n'
                    f'"{real}" -C "{clone}" checkout -q -B main origin/main\n'
                    f'echo sib > "{clone}/note.txt"\n'
                    f'"{real}" -C "{clone}" add -A\n'
                    f'"{real}" -C "{clone}" commit -qm "task commit sib"\n'
                    f'"{real}" -C "{clone}" push -q origin HEAD:refs/heads/main\n'
                    f'S=$("{real}" -C "{clone}" rev-parse HEAD)\n'
                    f'V=$(cd "{clone}" && "{sys.executable}" scripts/bump_version.py'
                    f" | tail -n1)\n"
                    f'cd "{clone}" && VERSION="$V" GITHUB_SHA="$S" {shell} scripts/release.sh'
                    f' >"{bin_dir}/sib.out" 2>"{bin_dir}/sib.err"\n'
                )
                tail = f'    exec "{real}" "$@";;\n'
            elif action == "lands_but_fails":
                body = f'    "{real}" "$@" >/dev/null 2>&1\n'
                tail = '    echo "fatal: the remote end hung up unexpectedly" >&2\n    exit 1;;\n'
            elif action == "fails_and_breaks_origin":
                body = f'    "{real}" remote set-url origin "{self.root / "vanished.git"}"\n'
                tail = '    echo "fatal: the remote end hung up unexpectedly" >&2\n    exit 1;;\n'
            else:  # pragma: no cover - опечатка в самом тесте
                raise AssertionError(action)
            if action == "sibling_releases":
                sib = bin_dir / "release-sibling"
                sib.write_text("#!/bin/sh\nset -eu\n" + body)
                sib.chmod(0o755)
                body = f'    "{sib}"\n'
            (bin_dir / "git").write_text(
                "#!/bin/sh\n"
                'for a in "$@"; do\n'
                # Шаблон с `*` по краям: мутация вида `+refs/heads/stable:…` не должна
                # ронять шим по постороннему поводу — иначе тест краснеет не за то.
                '  case "$a" in *refs/heads/stable:refs/heads/stable*)\n'
                f"{body}{tail}"
                "  esac\n"
                "done\n"
                f'exec "{real}" "$@"\n'
            )
            (bin_dir / "git").chmod(0o755)
            return bin_dir

        def release_job(
            self, work: Path, sha: str, path_prefix: Path | None = None
        ) -> subprocess.CompletedProcess[str]:
            """Ровно то, что делает job `release`: bump_version.py -> release.sh."""
            bumped = subprocess.run(
                [sys.executable, "scripts/bump_version.py"],
                cwd=work, env=_env(), capture_output=True, text=True, check=True,
            )
            version = bumped.stdout.strip().splitlines()[-1]
            env = _env()
            env["VERSION"] = version
            env["GITHUB_SHA"] = sha
            if path_prefix is not None:
                env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
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


def test_a_landed_push_that_reported_failure_still_releases(stand):
    """ПЕРВЫЙ вопрос перепроверки: push приземлился, клиент соврал отказом (tracker #716).

    Ни второго актора, ни человека, ни второй попытки: я ВЕРШИНА, прогон один. Круг 1
    этой карточки задавал только ВТОРОЙ вопрос («меня накрыли?») — и получал «да», потому
    что накрыл его СОБСТВЕННЫЙ приземлившийся bump: тега нет, `stable` не двинулся, job
    ЗЕЛЁНЫЙ. Здесь релиз обязан ДОЕХАТЬ: тег и `stable` ещё не пушились.
    """
    work = stand.checkout("w", stand.c0)
    shim = stand.shim_push_lands_but_fails()
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" not in done.stdout
    assert "but landed" in done.stdout
    tip = stand.remote_main()
    assert _git(stand.origin, "rev-parse", f"{tip}^").stdout.strip() == stand.c0
    assert stand.remote_tags() == [NEXT_VERSION]
    assert _git(stand.origin, "rev-list", "-n1", NEXT_VERSION).stdout.strip() == tip
    assert stand.remote_stable() == tip


def test_without_the_landed_question_the_release_is_silently_lost(stand):
    """МУТАЦИЯ: снимаем ПЕРВЫЙ вопрос — обязан вернуться тихий зелёный без релиза.

    Негативный пин не считается пином, пока не показано, что он краснеет от снятия
    ИМЕННО своей защиты: без этого он сертифицирует собственную зелень.
    """
    work = stand.checkout("w", stand.c0, release_sh=_release_sh_without_the_landed_question())
    shim = stand.shim_push_lands_but_fails()
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" in done.stdout          # молча решил, что накрыт
    assert stand.remote_tags() == []                 # тега нет
    assert stand.remote_stable() is None             # канал не двинулся
    # ...при том что bump УЖЕ на main: состояние полу-собранное, а job зелёный.
    assert '"0.2.171"' in stand.remote_file("refs/heads/main", "src/vikunja_mcp/__init__.py")


def test_a_landed_push_with_a_newer_tip_on_top_is_loud(stand):
    """Приземлилось, но поверх УЖЕ легло новее: ЗВУК ВАЖНЕЕ ТИШИНЫ (tracker #716).

    Тега на remote нет, а тег без `stable` это половина релиза, — значит громко, ровно
    как было ДО всякого гейта: отбитый push = красный job. Обоснование тут с #737 ДРУГОЕ:
    прежнее «форс-push откатил бы канал назад» стало ложным, когда `-f` с канала убрали.
    Класс полу-состояний — #723.
    """
    work = stand.checkout("w", stand.c0)
    shim = stand.shim_push_lands_but_fails(then_land_sibling=True)
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode != 0, done.stdout
    assert "release skipped" not in done.stdout
    assert "NOT pushed" in done.stderr
    assert stand.remote_tags() == []
    assert stand.remote_stable() is None
    my_bump = _git(work, "rev-parse", "HEAD").stdout.strip()
    assert stand.remote_main() != my_bump
    _git(stand.origin, "merge-base", "--is-ancestor", my_bump, "refs/heads/main")


def test_a_fetch_that_cannot_answer_is_never_a_skip(stand):
    """Пинит СВОЙСТВО, а не строку: не сумевший ответить fetch НИКОГДА не даёт skip.

    Пара к test_a_stale_fetch_head_never_decides: там fetch не смог ЗАПИСАТЬ ответ,
    здесь — не смог его ПОЛУЧИТЬ. Разница видна в FETCH_HEAD и решает, какая из трёх
    точек отказа держит; обе формы измерены, см. соседний тест.

    Здесь origin недоступен -> job обязан быть КРАСНЫМ, и ни слова `release skipped`,
    что бы ни лежало в FETCH_HEAD от wildcard-фетча чекаута. Тавтологией тест не
    является: мутация «спросить не удалось -> тихий skip» (ветка `if ! read_tip` отдаёт
    `skip`+`exit 0` вместо `exit 1`) убивает РОВНО его — control 0 failed; мутация
    1 failed, выборка `tests/unit/test_release_script.py`, collected 16 в обоих раундах.
    """
    work = stand.checkout("w", stand.c0)
    assert (work / ".git" / "FETCH_HEAD").read_text().strip(), "стенд не оставил FETCH_HEAD"
    _git(work, "remote", "set-url", "origin", str(stand.root / "vanished.git"))

    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0, done.stdout
    assert "release skipped" not in done.stdout
    assert stand.remote_main() == stand.c0
    assert stand.remote_tags() == []
    assert stand.remote_stable() is None


def test_a_stale_fetch_head_never_decides(stand):
    """Пинит `|| return 1` У САМОГО `git fetch` — ту строку, что пережила два свипа.

    Почему она переживала. Свип снимал её, и не падало НИЧЕГО: во всех СЕТЕВЫХ формах
    отказа (нет remote, remote не репозиторий, объекты нечитаемы, рефа нет) git
    УСЕКАЕТ FETCH_HEAD в 0 байт, так что следом честно падает `rev-parse`, и строка
    оказывается защитой в глубину. Но универсалия «упавший fetch усекает FETCH_HEAD»
    ЛОЖНА, и контрпример построен: если FETCH_HEAD НЕЛЬЗЯ ПЕРЕЗАПИСАТЬ, fetch падает
    (`error: cannot open '.git/FETCH_HEAD': Permission denied`, rc=255), а файл
    остаётся ПРЕЖНИМ — `rev-parse` отдаёт код 0 и УСТАРЕВШУЮ вершину. В этой форме
    `|| return 1` у fetch — ЕДИНСТВЕННОЕ, что не даёт решить по протухшим данным.

    Стенд: я — вершина (релиз ДОЛЖЕН состояться), а в FETCH_HEAD лежит ПОТОМОК моего
    sha. Со строкой job спрашивает, не получает ответа, ничего не решает и релизится
    нормально; без неё — читает потомка как «меня накрыли» и молча не релизится.
    """
    ahead = stand.root / "ahead"
    _git(stand.root, "clone", "-q", str(stand.origin), str(ahead))
    (ahead / "note.txt").write_text("descendant")
    _git(ahead, "add", "-A")
    _git(ahead, "commit", "-qm", "descendant of c0")
    _git(ahead, "push", "-q", "origin", "HEAD:refs/heads/side")
    descendant = _git(ahead, "rev-parse", "HEAD").stdout.strip()

    work = stand.checkout("w", stand.c0)
    fetch_head = work / ".git" / "FETCH_HEAD"
    fetch_head.write_text(f"{descendant}\t\tbranch 'main' of {stand.origin}\n")
    fetch_head.chmod(0o444)
    try:
        done = stand.release_job(work, stand.c0)
    finally:
        fetch_head.chmod(0o644)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" not in done.stdout, "решил по УСТАРЕВШЕМУ FETCH_HEAD"
    assert stand.remote_tags() == [NEXT_VERSION]
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


def test_a_foreign_orphan_bump_swallows_an_earlier_landing(stand):
    """ЧЕТВЁРТОЕ проглатывание, ПИН ИЗВЕСТНОГО ЗАЗОРА, а не желаемого (tracker #740).

    Вершину main держит ЧУЖОЙ осиротевший bump: job сиблинга умер между push'ем main и
    push'ем тега. Тогда БОЛЕЕ РАННЕЕ приземление, чей job запускается ПОСЛЕ, глотается
    на своём ПЕРВОМ и ЕДИНСТВЕННОМ прогоне — перезапусков ноль, второго актора в его
    собственном пути нет.

    Почему это НЕ проглатывание (2): у (2) оговорка «первый прогон ГРОМКИЙ» держится на
    том, что вершина — СОБСТВЕННЫЙ bump упавшего job'а, и тихо становится только после
    ручного `gh run rerun`. Здесь обрыв случился в ЧУЖОМ прогоне, и глотается тот, кто
    не падал вовсе. Почему НЕ (3): там вершина — коммит, у которого ПРОГОН ЕСТЬ (красный,
    отменённый); здесь вершина — BUMP-коммит, а на них прогонов не бывает вовсе
    (перемерено: 60 из 60 подряд bump-sha отдают `[]`).

    Тест пинит ТЕКУЩЕЕ поведение, а не желаемое: гейт ВНЁС этот зазор (на том же входе
    до-716 инлайн даёт `non-fast-forward` и exit 1), и закрывать его в карточке про
    коллизию тега — новая красная ветка на релизном пути, то есть отдельная работа.

    Что тест НЕ тавтология — ИЗМЕРЕНО, а не обещано: «мутацией» тут служит сама починка,
    поэтому проверялось так. Кандидат в починку #740 (не считать «накрыт», если вершина —
    осиротевший bump: `! git log -1 --format=%s "$tip" | grep -q "^chore: v"` третьим
    конъюнктом) даёт на выборке `tests/unit/test_release_script.py`, collected 17 в обоих
    раундах: control 0 failed; кандидат 5 failed, и ЭТОТ тест — среди упавших. Значит он
    покраснеет, когда #740 закроют, и позовёт переписать (4) в `scripts/release.sh` и в
    CLAUDE.md — иначе прозе поверят, а она устареет молча. Остальные четыре падения —
    отдельная находка для #740: наивный признак «вершина это bump» ЛОМАЕТ здоровый
    superseded-путь, потому что в его стендах вершина тоже bump. Признак нужен другой.
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
    sibling = stand.land_sibling("sib")           # таск-коммит сиблинга поверх c0
    sib_job = stand.checkout("sib-job", sibling)
    died = stand.release_job(sib_job, sibling)    # его релиз умирает на push'е ТЕГА
    assert died.returncode != 0, died.stdout
    hook.unlink()                                 # дальше remote ЗДОРОВЫЙ

    orphan = stand.remote_main()
    assert orphan != sibling, "вершиной должен стать bump сиблинга, а не его таск-коммит"
    assert stand.remote_tags() == []
    assert stand.remote_stable() is None

    # МОЙ job: первый и единственный прогон для c0, никаких шимов и перезапусков.
    work = stand.checkout("mine", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" in done.stdout       # ЗЕЛЁНЫЙ, и это и есть проглатывание
    assert stand.remote_tags() == []              # тега по-прежнему нет
    assert stand.remote_stable() is None          # канал не двинулся
    assert stand.remote_main() == orphan          # вершина осталась чужим сиротой


def test_the_channel_is_never_moved_backwards(stand):
    """#737: полный релиз сиблинга В ОКНЕ перед push'ем канала — канал НЕ едет назад.

    До этой карточки последним действием стоял безусловный `git push -f` на `stable`, и
    стенд мерил один и тот же откат в трёх рядах (ветка «приземлилось», обычный путь,
    до-716 инлайн из ci.yml): job ЗЕЛЁНЫЙ, оба тега на месте, а канал уезжает на МОЙ
    bump, хотя вершина main уже на патч дальше. Работа не терялась, но потребители до
    следующего релиза тянули код старше последнего тега.

    Здесь пинится не «push сделан», а НАБЛЮДАЕМОЕ состояние канала: он стоит на вершине
    (bump'е сиблинга), не равен моему bump'у — и при этом СОДЕРЖИТ его, то есть мой код
    до потребителей доехал. Что тест не вакуумный, показывает соседний
    test_with_a_forced_stable_push_the_rollback_comes_back: тот же стенд, тот же шим,
    единственная разница — вернули `-f`, и откат возвращается.

    Свип, выборка `tests/unit/test_release_script.py`, collected 25 во всех раундах,
    возмущался МИР (scripts/release.sh), а не тело теста: control 0 failed; вернуть `-f`
    на push канала 4 failed (этот тест, его мутационный сосед, пин «канал вне main —
    громко» и текстовый пин «форсированных push'ей ноль»); снять ветку «канал уже содержит
    меня» (`elif false`) 1 failed — снова ЭТОТ тест, потому что job тогда краснеет на
    состоянии, которое краснеть не должно. То есть он держит ОБА конца: и «не
    откатывать», и «не паниковать».
    """
    work = stand.checkout("w", stand.c0)
    shim = stand.shim_stable_push(action="sibling_releases")
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    my_bump = _git(work, "rev-parse", "HEAD").stdout.strip()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "not moved backwards" in done.stdout
    assert stand.remote_tags() == [NEXT_VERSION, "v0.2.172"]
    channel = stand.remote_stable()
    assert channel == stand.remote_main(), "канал обязан остаться на вершине, а не уехать назад"
    assert channel != my_bump
    _git(stand.origin, "merge-base", "--is-ancestor", my_bump, channel)  # но мой код в нём есть


def test_with_a_forced_stable_push_the_rollback_comes_back(stand):
    """МУТАЦИЯ соседнего теста: вернули `-f` — обязан вернуться измеренный откат канала.

    Возмущается МИР (одна строка скрипта), а не тело теста: удалить кейс из проверки
    дешевле и даёт вакуумно-зелёный раунд, поэтому так тут нельзя. Job при этом остаётся
    ЗЕЛЁНЫМ — ровно поэтому дефект и жил незамеченным.
    """
    work = stand.checkout("w", stand.c0, release_sh=_release_sh_with_a_forced_stable_push())
    shim = stand.shim_stable_push(action="sibling_releases")
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    my_bump = _git(work, "rev-parse", "HEAD").stdout.strip()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "not moved backwards" not in done.stdout
    assert stand.remote_tags() == [NEXT_VERSION, "v0.2.172"], "тег сиблинга остаётся на месте"
    assert stand.remote_stable() == my_bump, "с -f канал обязан откатиться на мой bump"
    assert stand.remote_stable() != stand.remote_main()


def test_a_refused_channel_push_that_does_not_contain_us_is_loud(stand):
    """Отказ push'а канала по НЕ-гоночной причине — КРАСНЫЙ, а не тихий не-сдвиг.

    Это и есть та ловушка, за которую #716 отбивали дважды: «починка» отката, отдающая
    зелёный job при неподвинутом канале. Здесь канал стоит на базовом коммите (моего
    bump'а он НЕ содержит), push отвергнут хуком — и job обязан покраснеть.

    Свип, выборка `tests/unit/test_release_script.py`, collected 25 в обоих раундах:
    control 0 failed; заменить `exit 1` этой ветки на `exit 0` (то есть построить ровно тот
    тихий не-сдвиг, которого карточка боялась) 3 failed — этот тест и оба соседа, которые
    приходят в ту же ветку другими дорогами (канал вне main; тег-омоним `stable`).
    """
    _git(stand.seed, "branch", "-f", "stable", stand.c0)
    _git(stand.seed, "push", "-q", "origin", "refs/heads/stable:refs/heads/stable")
    hook = stand.origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read -r old new ref; do\n"
        '  case "$ref" in refs/heads/stable) echo "refused $ref" >&2; exit 1;; esac\n'
        "done\n"
        "exit 0\n"
    )
    hook.chmod(0o755)

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0, done.stdout
    assert "channel NOT moved" in done.stderr
    assert "release skipped" not in done.stdout
    assert stand.remote_stable() == stand.c0, "канал остался там, где стоял"
    assert stand.remote_tags() == [NEXT_VERSION], "тег уже уехал: полу-состояние, но ГРОМКОЕ"


def test_a_channel_pointed_off_main_is_loud_rather_than_overwritten(stand):
    """Отказ здесь даёт САМ git (не хук): пинится, что push канала действительно ff-only.

    Канал указан на коммит ВНЕ главной ветки — состояние, которого ни одна процедура репо
    не создаёт (задокументированный откат целится в ТЕГ, а тег всегда предок вершины), но
    рукой создать можно. До #737 следующий же релиз молча затирал его форс-push'ем; теперь
    git отказывает как на non-fast-forward, и job краснеет, назвав состояние.

    ЦЕНА, названная прямо: пока человек не поправит канал, КАЖДЫЙ следующий релиз будет
    красным на этом шаге. Это осознанный размен — канал, указывающий не туда, куда говорит
    последний тег, чинится человеком, а не молча перетирается; и «громко» здесь сильнее,
    чем «само зарастёт», ровно по правилу, за которое #716 отбивали дважды.

    Свип, выборка `tests/unit/test_release_script.py`, collected 25 в обоих раундах:
    control 0 failed; вернуть `-f` на push канала 4 failed, и этот тест среди них — с `-f`
    посторонний канал просто затирается.
    """
    side = stand.root / "side"
    _git(stand.root, "clone", "-q", str(stand.origin), str(side))
    (side / "side.txt").write_text("side")
    _git(side, "add", "-A")
    _git(side, "commit", "-qm", "off-main commit")
    off_main = _git(side, "rev-parse", "HEAD").stdout.strip()
    _git(side, "push", "-q", "origin", f"{off_main}:refs/heads/stable")

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0, done.stdout
    assert "channel NOT moved" in done.stderr
    assert stand.remote_stable() == off_main, "канал не тронут — его чинит человек"
    assert stand.remote_tags() == [NEXT_VERSION]


def test_a_tag_named_stable_does_not_decide_where_the_channel_is(stand):
    """Та же ловушка, что у `main`, но со стороны канала: рефспек чтения обязан быть ПОЛНЫМ.

    `git fetch origin stable` резолвит имя по всем пространствам имён, поэтому ТЕГ с
    именем `stable` уводит fetch на себя, и перепроверка начинает судить о канале по
    чужому коммиту. Стенд делает разницу наблюдаемой: ветка `stable` стоит на базе, тег
    `stable` — на постороннем коммите, push отбит хуком. Верный ответ называет БАЗУ.

    Свип, выборка `tests/unit/test_release_script.py`, collected 25 в обоих раундах:
    control 0 failed; укоротить рефспек чтения до `git fetch --quiet origin stable`
    1 failed — этот тест.

    ЧЕГО ОН НЕ ЗАКРЫВАЕТ, и это измерено, а не предположено. Защит в read_remote_ref от
    этой ловушки ДВЕ — полный рефспек и `^{commit}`, — и до #737 ни одна не была запинена
    ПООТДЕЛЬНОСТИ: тот же свип на выборке из одного
    test_a_tag_named_main_does_not_fake_a_supersession, collected 1 во всех раундах, даёт
    control 0 failed; короткий рефспек 0 failed; снятый `^{commit}` 0 failed; ОБА сразу
    1 failed. Причина — в его стенде тег `main` висит на ТОМ ЖЕ коммите, что и ветка, так
    что уход fetch на тег наблюдаем только через объект тега, а его разыменовывает
    `^{commit}`. Этот тест закрывает половину (рефспек) и на обе стороны сразу, потому что
    функция общая; вторая половина осталась: снятый `^{commit}` в одиночку по-прежнему
    даёт 0 failed на всём файле (collected 25, control 0 failed). Заведено отдельной
    карточкой — тут это чужой слайс.
    """
    side = stand.root / "side"
    _git(stand.root, "clone", "-q", str(stand.origin), str(side))
    (side / "side.txt").write_text("side")
    _git(side, "add", "-A")
    _git(side, "commit", "-qm", "unrelated")
    decoy = _git(side, "rev-parse", "HEAD").stdout.strip()
    _git(side, "tag", "-a", "stable", "-m", "тег-омоним", decoy)
    _git(side, "push", "-q", "origin", "refs/tags/stable:refs/tags/stable")
    _git(stand.seed, "branch", "-f", "stable", stand.c0)
    _git(stand.seed, "push", "-q", "origin", "refs/heads/stable:refs/heads/stable")

    hook = stand.origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read -r old new ref; do\n"
        '  case "$ref" in refs/heads/stable) echo "refused $ref" >&2; exit 1;; esac\n'
        "done\n"
        "exit 0\n"
    )
    hook.chmod(0o755)

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0, done.stdout
    assert stand.c0 in done.stderr, "перепроверка обязана судить по ВЕТКЕ stable"
    assert decoy not in done.stderr, "перепроверка ушла на ТЕГ с именем stable"
    assert stand.remote_stable() == stand.c0


def test_a_landed_channel_push_that_reported_failure_is_green(stand):
    """Push канала приземлился, а клиент соврал отказом — тот же обрыв, что у main.

    Красить это нельзя: канал ДОКАЗУЕМО стоит ровно на моём bump'е, релиз состоялся
    полностью.

    ЧТО ИМЕННО ЗДЕСЬ ЗАПИНЕНО — СООБЩЕНИЕ, а не вердикт, и путать это дорого. `git
    merge-base --is-ancestor X X` отвечает 0 (проверено), поэтому ветвь равенства
    ПОГЛОЩЕНА соседней ветвью «канал уже содержит меня»: снимешь равенство — job всё
    равно зелёный, изменится только текст в логе. Ветвь стоит ради текста, и текст этого
    стоит: «твой push всё-таки приземлился» и «тебя увёз более новый релиз» — разные
    новости для человека, читающего красный или странный прогон. Свип это и показывает,
    выборка `tests/unit/test_release_script.py`, collected 25 в обоих раундах:
    control 0 failed; снять эту ветвь (`if false`) 1 failed — этот тест, и падает он на
    отсутствии строки, а не на коде возврата.
    """
    work = stand.checkout("w", stand.c0)
    shim = stand.shim_stable_push(action="lands_but_fails")
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    my_bump = _git(work, "rev-parse", "HEAD").stdout.strip()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "stable push reported failure but landed" in done.stdout
    assert stand.remote_stable() == my_bump == stand.remote_main()
    assert stand.remote_tags() == [NEXT_VERSION]


def test_an_unreadable_channel_is_never_green(stand):
    """Не сумевший ответить fetch НИКОГДА не даёт зелёного — теперь и со стороны канала.

    Пара к test_a_fetch_that_cannot_answer_is_never_a_skip: чтение вершины и чтение канала
    делает ОДНА функция, поэтому свойство пинится с обеих сторон. Шим отбивает push и
    заодно уводит origin в никуда: перепроверке нечем ответить, кто где стоит, — и «не
    знаю» обязано быть красным, а не молчаливым «ну наверное там уже мой код».

    Свип, выборка `tests/unit/test_release_script.py`, collected 25 во всех раундах:
    control 0 failed; заменить `exit 1` этой ветки на `exit 0` 1 failed — этот тест.

    А вот ЧЕГО этот тест НЕ держит, сказано отдельно, потому что легко решить обратное.
    В общей read_remote_ref две точки отказа, и убивается только ОДНА. Тот же свип,
    collected 25 во всех раундах: control 0 failed; снять `|| return 1` у `git fetch`
    1 failed — но это test_a_stale_fetch_head_never_decides, не этот; снять `|| return 1`
    у `git rev-parse` 0 failed, то есть НИ ОДНОГО. Причина та же, что описана у самой
    функции: в стендовых (сетевых) формах отказа fetch падает первым, и до rev-parse
    дело не доходит вовсе. Свести чтение вершины и чтение канала в одну функцию этот
    зазор не создало и не закрыло — он ровно тот же, что был у read_tip до #737.
    """
    work = stand.checkout("w", stand.c0)
    shim = stand.shim_stable_push(action="fails_and_breaks_origin")
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode != 0, done.stdout
    assert "could not be read" in done.stderr
    assert "not moved backwards" not in done.stdout
    assert stand.remote_stable() is None, "канал не двинулся"
    assert stand.remote_tags() == [NEXT_VERSION]


def test_the_script_keeps_its_two_unpinned_release_properties():
    """Две вещи, которые поведенческий стенд не ловит, а снятие которых дорого стоит.

    (1) ci-skip-маркер в теме bump-коммита — второй пояс против рекурсии CI (первый —
    то, что GITHUB_TOKEN не ретриггерит прогон, и стендом он не воспроизводится).
    (2) НИ ОДИН push релизного пути не форсируется. Про main это измерено вторым проходом
    #716: с `--force` таск-коммит сиблинга, приземлившийся в окне гонки, ПРОПАДАЕТ. Про
    `stable` до #737 стояло обратное — «форсируется ровно один ref, и это by design», —
    и by design оно откатывало канал назад; теперь форсированных push'ей НОЛЬ.

    Свип по ВСЕМ ЧЕТЫРЁМ написаниям форса, выборка `tests/unit/test_release_script.py`,
    collected 25 во всех раундах, control 0 failed: `-f` перед origin 4 failed;
    `--force` — то же место, тот же детектор; ВЕДУЩИЙ ПЛЮС в рефспеке
    (`+refs/heads/stable:…`, форс вообще без флага) 4 failed; флаг В КОНЦЕ строки
    (`… "<рефспек>" -f;`) 4 failed. Последние два пин ПРОПУСКАЛ, пока второй проход не
    построил первый из них и не получил ЗЕЛЁНЫЙ job с настоящим откатом канала при
    зелёном этом тесте; второй пропускала уже ПОЧИНКА — подстрока `" -f "` не видит
    `-f;`, — и это поймал свип, а не чтение регулярки.
    """
    text = RELEASE_SH.read_text()
    assert "[skip" + " ci]" in text, "тема bump-коммита обязана нести ci-skip-маркер"

    # Комментарии отбрасываются, и это не послабление: строка, начинающаяся с `#`, для sh
    # не команда вовсе. Иначе пин ловил бы прозу — например абзац про то, что человеческий
    # `git push -f origin stable` мимо скрипта этой правкой НЕ чинится.
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    # `git push` ВНУТРИ сообщения — не push, а текст: красная ветка отказа подсказывает
    # человеку ручное лекарство `git push -f origin stable`, и без этого различения пин
    # краснел бы от собственной подсказки. Признак — то, что стоит ПЕРЕД вхождением:
    # `echo` (это аргумент echo) или открывающая кавычка (вхождение внутри строки).
    # ЧЕСТНАЯ ГРАНИЦА: `echo x && git push -f …` в одной строке пин пропустит. Скрипт так
    # не пишет, а поведенческие тесты форс ловят в любом написании — этот пин про
    # НАПИСАНИЯ, и его цена названа, а не замолчана.
    pushes = [
        ln
        for ln in code
        if "git push" in ln
        and "echo" not in ln.split("git push")[0]
        and not ln.split("git push")[0].rstrip().endswith('"')
    ]
    # НАПИСАНИЙ ФОРСА ЧЕТЫРЕ, и знать это пришлось замером: первая версия пина искала
    # только `-f ` и `--force` и пропускала ДВА других — ВЕДУЩИЙ ПЛЮС в рефспеке
    # (`+refs/heads/stable:…`, форс без единого флага) и флаг В КОНЦЕ строки (`… -f`).
    # Второй проход построил первый из них и получил ЗЕЛЁНЫЙ job с настоящим откатом
    # канала при зелёном тексте этого теста. Поведенческие тесты его ловят (вернуть форс
    # любым написанием -> `test_the_channel_is_never_moved_backwards` краснеет), но пин,
    # который существует ровно затем, чтобы ловить написания, обязан ловить написания.
    forced = [
        ln
        for ln in pushes
        if re.search(r"(?<![\w-])(-f|--force)(?![\w-])", ln)
        or any(part.lstrip('"').startswith("+") for part in ln.split())
    ]
    assert forced == [], forced


def test_ci_calls_the_script_and_pushes_nothing_itself():
    """Гейт бесполезен, если рядом останется push мимо него: ci.yml не пушит сам."""
    text = CI_YML.read_text()
    assert "sh scripts/release.sh" in text
    assert "git push" not in text, (
        "релизные push'и живут только в scripts/release.sh — push из ci.yml обойдёт гейт"
    )


def test_ci_serialises_release_jobs():
    """Пин на `concurrency` — потому что после #737 её пропажу больше нечем заметить.

    Раньше группа была ЕДИНСТВЕННЫМ, что закрывало окно перед push'ем канала, и её снятие
    вернуло бы откат; теперь откат ловит сам скрипт, а самые частые исходы гонок, которые
    группа предотвращает, ЗЕЛЁНЫЕ — skip «меня накрыли» и notice «канал уже впереди».
    Сказать «ни одного красного» нельзя (ветка «мой bump на main, а поверх легло новее»
    красная, и без группы она тоже учащается) — но НАДЁЖНОГО сигнала не остаётся, а
    ненадёжный сигнал не сигнал. Значит заметить удаление или переименование группы можно
    ровно одним способом: спросить об этом текстом. Держит она две разные вещи: сериализацию (`group`) и то, что идущий
    релиз не отменяют на полпути между push'ями (`cancel-in-progress: false`), поэтому
    и спрашивается про обе — свип, выборка `tests/unit/test_release_script.py`,
    collected 25 во всех раундах: control 0 failed; убрать строку `group: release` из
    ci.yml 1 failed; убрать `cancel-in-progress: false` 1 failed, оба раза этот тест.
    """
    text = CI_YML.read_text()
    assert "concurrency:" in text
    assert "group: release" in text, "релизные job'ы обязаны сериализоваться одной группой"
    assert "cancel-in-progress: false" in text, (
        "отмена идущего релиза оставит полу-состояние: bump на main без тега и без канала"
    )
