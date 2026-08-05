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
# «меня накрыли», job зеленеет без сдвига канала (tracker #716). С #723 тег при этом на
# remote ЕСТЬ — он уехал атомарно с bump'ом; круг 1 #716 терял здесь и его.
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

# `--atomic` на ПЕРВОМ push'е: bump и его тег едут ОДНОЙ серверной транзакцией (#723).
# Мутация снимает ТОЛЬКО флаг, оставляя оба рефспека в одной команде, — то есть строит
# ровно ту «дешёвую» замену, которую легко принять за эквивалент, и обязана вернуть два
# РАЗНЫХ измеренных дефекта: полу-состояние «bump без тега» и тег-сироту.
ATOMIC_PUSH_LINE = 'if ! git push --atomic origin "HEAD:refs/heads/${MAIN_BRANCH}" \\\n'
UNATOMIC_PUSH_LINE = 'if ! git push origin "HEAD:refs/heads/${MAIN_BRANCH}" \\\n'

# ...а ЭТА мутация возвращает ФОРМУ до #723 целиком: main отдельным push'ем, тег — вторым,
# ПОСЛЕ него. Две мутации нужны потому, что защита двусоставная, и какая половина работает —
# зависит ОТ ФОРМЫ ОТКАЗА СЕРВЕРА, что измерено на двух видах хуков:
#   `pre-receive` (ОДИН на push) отвергает пачку ЦЕЛИКОМ и без всякого `--atomic` — на таком
#   входе достаточно уже того, что рефспека два в одной команде;
#   `update` (ПО РЕФУ — это и есть форма ref-protection у хостингов) отвергает ровно свой
#   реф, и неатомарная пачка тогда берёт что может: main уезжает, тег отвергнут, то есть
#   «bump без тега» строится и ОДНОЙ командой. Здесь несущий уже флаг.
# Ровно поэтому «строится только раздельными push'ами» было бы ложью, и ровно поэтому мутация
# «снять только флаг» проверяется на `update`-хуке, а не на `pre-receive`.
ATOMIC_PUSH_BLOCK = (
    'if ! git push --atomic origin "HEAD:refs/heads/${MAIN_BRANCH}" \\\n'
    '        "refs/tags/${VERSION}:refs/tags/${VERSION}"; then\n'
)

# ГЕЙТ ЧЕСТНОСТИ SKIP'а (#740): «меня накрыли» уходит в зелёное, только если это доказано —
# канал уже несёт мой sha, ЛИБО вершину ещё никто не выпускал. Мутация вставляет безусловный
# skip первой же строкой тела, то есть возвращает поведение ДО #740 буквально, не трогая ни
# одну строку решения самого накрытия. Переименуют функцию — тест упадёт на ассерте ниже.
HONEST_SKIP_GATE = "skip_or_refuse() {\n"

# ГЕЙТ ИМЕНИ ВЕРСИИ (#769): единственная строка решения «имя `vX.Y.Z` уже занято». Мутация
# гасит её на `false`, то есть возвращает состояние, в котором про имя не спрашивают вовсе и
# всё решает `git tag -a` — а он у КАЖДОГО следующего приземления падает кодом 128 раньше,
# чем скрипт успевает сказать, что именно случилось и что это не зарастёт само.
VERSION_NAME_GATE = "if version_name_taken; then\n"
SEPARATE_MAIN_PUSH = 'if ! git push origin "HEAD:refs/heads/${MAIN_BRANCH}"; then\n'
STABLE_LOCAL_MOVE = 'git branch -f "${STABLE_BRANCH}" HEAD'
SEPARATE_TAG_PUSH = 'git push origin "refs/tags/${VERSION}:refs/tags/${VERSION}"\n'

# ДВЕ ЗАЩИТЫ `read_remote_ref` от «тега-омонима» (#750), и мутации у них РАЗНЫЕ, потому что
# наблюдаются они на РАЗНЫХ вызовах — это и есть причина, по которой ОДИН тест их не разделял.
#
# ПОЛНЫЙ РЕФСПЕК держит чтения ВЕТОК: `git fetch origin main` резолвит имя по всем
# пространствам имён, а `refs/tags/main` стоит в этом порядке РАНЬШЕ `refs/heads/main`, так что
# аннотированный тег с именем ветки уводит fetch на себя. Мутация укорачивает рефспек у
# `read_tip`; у `read_channel` то же самое держит #737.
TIP_REFSPEC_LINE = (
    'read_tip() { read_remote_ref "refs/heads/${MAIN_BRANCH}" && tip="$ref_head"; }\n'
)
SHORT_TIP_REFSPEC_LINE = 'read_tip() { read_remote_ref "${MAIN_BRANCH}" && tip="$ref_head"; }\n'

# `^{commit}` держит чтение ТЕГА, и ТОЛЬКО его. На ветке peel — no-op (ветка не может
# указывать на не-коммит), поэтому до #723, пока третьего чтения не было, эта мутация не
# убивала ничего и выглядела «недостижимой защитой в глубину». `read_tag` сделал её
# наблюдаемой: `git tag -a` создаёт АННОТИРОВАННЫЙ тег, а `fetch refs/tags/<имя>` кладёт в
# FETCH_HEAD ОБЪЕКТ ТЕГА — измерено на git 2.50.1 и запинено прямо в
# test_the_version_tag_is_read_as_a_commit_not_as_a_tag_object, чтобы премисса не жила на вере.
PEEL_LINE = '    ref_head=$(git rev-parse "FETCH_HEAD^{commit}") || return 1\n'
UNPEELED_LINE = '    ref_head=$(git rev-parse "FETCH_HEAD") || return 1\n'


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


def _release_sh_without_atomic() -> str:
    """Поведение ДО #723 по РЕЗУЛЬТАТУ: два рефа в одном push'е, но без атомарности."""
    text = RELEASE_SH.read_text()
    assert text.count(ATOMIC_PUSH_LINE) == 1, (
        "атомарный push не найден дословно ровно один раз в scripts/release.sh — мутация "
        "ничего не снимет, и тест мутации станет тавтологией"
    )
    return text.replace(ATOMIC_PUSH_LINE, UNATOMIC_PUSH_LINE)


def _release_sh_with_a_separate_tag_push() -> str:
    """Форма ДО #723: main одним push'ем, тег — вторым, уже после победы."""
    text = RELEASE_SH.read_text()
    for needle in (ATOMIC_PUSH_BLOCK, STABLE_LOCAL_MOVE):
        assert text.count(needle) == 1, (
            f"{needle!r} не найдено дословно ровно один раз в scripts/release.sh — мутация "
            "ничего не снимет, и тест мутации станет тавтологией"
        )
    text = text.replace(ATOMIC_PUSH_BLOCK, SEPARATE_MAIN_PUSH)
    return text.replace(STABLE_LOCAL_MOVE, SEPARATE_TAG_PUSH + STABLE_LOCAL_MOVE)


def _release_sh_without_the_honest_skip_gate() -> str:
    """Поведение ДО #740: накрытие ЛЮБОЙ вершиной — сразу зелёный skip, без доказательств."""
    text = RELEASE_SH.read_text()
    assert text.count(HONEST_SKIP_GATE) == 1, (
        "гейт честности skip'а не найден дословно ровно один раз в scripts/release.sh — "
        "мутация ничего не снимет, и тест мутации станет тавтологией"
    )
    return text.replace(HONEST_SKIP_GATE, HONEST_SKIP_GATE + '    skip "$tip"\n    exit 0\n')


def _release_sh_without_the_version_name_gate() -> str:
    """Поведение ДО #769: имя версии у origin не спрашивается вовсе, решает `git tag -a`."""
    text = RELEASE_SH.read_text()
    assert text.count(VERSION_NAME_GATE) == 1, (
        "гейт имени версии не найден дословно ровно один раз в scripts/release.sh — мутация "
        "ничего не снимет, и тест мутации станет тавтологией"
    )
    return text.replace(VERSION_NAME_GATE, "if false; then\n")


def _release_sh_with_a_short_tip_refspec() -> str:
    """Снимает ПЕРВУЮ защиту от тега-омонима: чтение вершины по КОРОТКОМУ имени (#750)."""
    text = RELEASE_SH.read_text()
    assert text.count(TIP_REFSPEC_LINE) == 1, (
        "чтение вершины main не найдено дословно ровно один раз в scripts/release.sh — "
        "мутация ничего не снимет, и тест мутации станет тавтологией"
    )
    return text.replace(TIP_REFSPEC_LINE, SHORT_TIP_REFSPEC_LINE)


def _release_sh_without_the_peel() -> str:
    """Снимает ВТОРУЮ защиту: `rev-parse` больше не разыменовывает объект тега (#750)."""
    text = RELEASE_SH.read_text()
    assert text.count(PEEL_LINE) == 1, (
        "разыменование `^{commit}` не найдено дословно ровно один раз в scripts/release.sh — "
        "мутация ничего не снимет, и тест мутации станет тавтологией"
    )
    return text.replace(PEEL_LINE, UNPEELED_LINE)


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

        def shim_push_drops_the_tag_and_lies(self) -> Path:
            """PATH-шим: сервер, который atomic ОБЪЯВИЛ, но не соблюл.

            Ветку берёт, рефспек ТЕГА выбрасывает, потом врёт клиенту отказом. Подделка тут
            в одном — в том, что сервер нарушает собственный контракт; всё остальное
            настоящее. Построено атакующим проходом #723: без проверки тега перепроверка на
            этом входе уходит в ЗЕЛЁНОЕ с релизом без тега.
            """
            real = shutil.which("git")
            assert real, "git не найден в PATH"
            bin_dir = self.root / "shim-drop-tag"
            bin_dir.mkdir()
            (bin_dir / "git").write_text(
                "#!/bin/sh\n"
                'for a in "$@"; do\n'
                "  case \"$a\" in HEAD:refs/heads/main)\n"
                # пересобираем аргументы БЕЗ рефспека тега
                '    set -- "$@"; args=""\n'
                '    for x in "$@"; do\n'
                '      case "$x" in refs/tags/*:refs/tags/*) continue;; esac\n'
                '      args="$args \'$x\'"\n'
                "    done\n"
                f'    eval "\'{real}\' $args" >/dev/null 2>&1\n'
                '    echo "fatal: the remote end hung up unexpectedly" >&2\n'
                "    exit 1;;\n"
                "  esac\n"
                "done\n"
                f'exec "{real}" "$@"\n'
            )
            (bin_dir / "git").chmod(0o755)
            return bin_dir

        def shim_sibling_lands_just_before_push(self) -> Path:
            """PATH-шим: сиблинг приземляется в ОКНЕ между гейтом и push'ем в main.

            Проигранная гонка БЕЗ единого хука: гейт перед `git tag` вершину ещё видел
            своей, а к моменту push'а main уже ушёл вперёд. Отличие от
            `shim_push_lands_but_fails` — тут ничего не подделывается вовсе: push
            выполняется по-настоящему и по-настоящему отбивается сервером, поэтому видно,
            какие рефы он успел взять, а какие нет.
            """
            real = shutil.which("git")
            assert real, "git не найден в PATH"
            bin_dir = self.root / "shim-race"
            bin_dir.mkdir()
            clone = self.root / "sib-mid-window"
            _git(self.root, "clone", "-q", str(self.origin), str(clone))
            land = bin_dir / "land"
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
            (bin_dir / "git").write_text(
                "#!/bin/sh\n"
                'for a in "$@"; do\n'
                "  case \"$a\" in HEAD:refs/heads/main)\n"
                f'    if [ ! -e "{bin_dir}/done" ]; then : > "{bin_dir}/done"; "{land}"; fi\n'
                f'    exec "{real}" "$@";;\n'
                "  esac\n"
                "done\n"
                f'exec "{real}" "$@"\n'
            )
            (bin_dir / "git").chmod(0o755)
            return bin_dir

        def shim_ls_remote_fails(self) -> Path:
            """PATH-шим: `git ls-remote` не отвечает вовсе, остальные чтения — настоящие.

            Нужен ровно гейту честности skip'а (#740): у него ДВА доказательства, и второе
            («вершину ещё никто не выпускал») читается перечислением тегов. Отдельный шим, а
            не сломанный origin, потому что вопрос именно в том, что делает скрипт, когда ОДНО
            из чтений не отвечает, — «не смог спросить» обязано быть красным, а не зелёным.
            """
            real = shutil.which("git")
            assert real, "git не найден в PATH"
            bin_dir = self.root / "shim-ls-remote"
            bin_dir.mkdir()
            (bin_dir / "git").write_text(
                "#!/bin/sh\n"
                'for a in "$@"; do\n'
                '  case "$a" in ls-remote)\n'
                '    echo "fatal: could not read from remote repository" >&2\n'
                "    exit 1;;\n"
                "  esac\n"
                "done\n"
                f'exec "{real}" "$@"\n'
            )
            (bin_dir / "git").chmod(0o755)
            return bin_dir

        def orphan_the_tip_with_a_foreign_bump(self) -> tuple[str, list[str]]:
            """Вершину main держит ЧУЖОЙ осиротевший bump: job сиблинга умер на push'е КАНАЛА.

            Ровно тот вход, ради которого заведена #740. С #723 полу-состояние может быть
            только одной формы — bump И ТЕГ на remote, не двинут ровно `stable`, — поэтому
            строится оно отказом на канале, а не на теге. Отдаёт (вершину, теги сиблинга).
            """
            hook = self.origin / "hooks" / "pre-receive"
            hook.write_text(
                "#!/bin/sh\n"
                "while read -r old new ref; do\n"
                '  case "$ref" in refs/heads/stable) echo "refused $ref" >&2; exit 1;; esac\n'
                "done\n"
                "exit 0\n"
            )
            hook.chmod(0o755)
            sibling = self.land_sibling("sib")            # таск-коммит сиблинга поверх c0
            died = self.release_job(self.checkout("sib-job", sibling), sibling)
            assert died.returncode != 0, died.stdout      # его релиз умирает на push'е КАНАЛА
            hook.unlink()                                 # дальше remote ЗДОРОВЫЙ

            orphan = self.remote_main()
            assert orphan != sibling, "вершиной должен стать bump сиблинга, а не его таск-коммит"
            tags = self.remote_tags()
            assert tags == [NEXT_VERSION]                 # тег СИБЛИНГА уехал атомарно с bump'ом
            assert self.remote_stable() is None
            return orphan, tags

        def squat_the_version_name(self, name: str = "squatter") -> str:
            """Чужой тег с ИМЕНЕМ следующей версии — на коммите, которого в main нет (#769).

            Пушится ОДИН реф, `refs/tags/vX.Y.Z`; коммит уезжает вместе с ним как объект,
            достижимый только из тега. Это и есть форма, ради которой заведена карточка:
            имя занято, а к истории main занявший его коммит отношения не имеет.
            """
            clone = self.root / name
            _git(self.root, "clone", "-q", str(self.origin), str(clone))
            (clone / "foreign.txt").write_text("foreign")
            _git(clone, "add", "-A")
            _git(clone, "commit", "-qm", "foreign commit")
            _git(clone, "tag", "-a", NEXT_VERSION, "-m", NEXT_VERSION)
            ref = f"refs/tags/{NEXT_VERSION}"
            _git(clone, "push", "-q", "origin", f"{ref}:{ref}")
            return _git(clone, "rev-parse", "HEAD").stdout.strip()

        def disable_atomic_push(self) -> None:
            """Сервер перестаёт advertise'ить возможность `atomic` (git ≥ 2.6)."""
            _git(self.origin, "config", "receive.advertiseAtomic", "false")

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
    """МУТАЦИЯ: снимаем строку решения — стенд обязан вернуть коллизию прогона 30754732335.

    ЧТО ЗДЕСЬ «ДОСЛОВНО» — с #769 уже НЕ текст git'а, и это надо читать как перемещение
    сигнала, а не как ослабление пина. Коллизия ровно та же (имя `v0.2.171` занял сосед,
    зарелизившийся первым), но сообщает о ней теперь гейт имени версии: он стоит ДО
    `git tag -a` и спрашивает origin раньше, чем тот успевает упасть своим
    `fatal: tag … already exists`. Дословный текст git'а из суда не исчез — его пинит
    `test_without_the_version_name_gate_the_squatter_is_cryptic`, где снят уже ГЕЙТ, то есть
    литерал стал свойством «гейта нет», чем он и является. Несущее тут — что job КРАСНЫЙ, что
    он называет занятое имя и что не двигается ни один реф.

    Развилка в тексте гейта («тег чужой или осиротевший» / «законный релиз этого репозитория»)
    к этому входу приложима второй половиной: тег законный, сосед выпустился честно. Дотянуться
    сюда можно только мутацией — в живом скрипте накрытие ловится раньше и уходит в
    `skip_or_refuse`, — и ровно поэтому сообщение сформулировано развилкой, а не утверждением
    о происхождении тега.
    """
    stand.land_sibling("sib", release=True)
    before = (stand.remote_main(), stand.remote_tags(), stand.remote_stable())

    work = stand.checkout("w", stand.c0, release_sh=_mutated_release_sh())
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0
    # ЧЕЙ текст — зависит от того, какой из двух гейтов увидит коллизию первым, и ЭТОМУ пину
    # всё равно: он про то, что снятие строки решения возвращает КОЛЛИЗИЮ. Перечислены оба
    # написания, потому что с ассертом на ОДИН текст этот пин краснеет НА ПРИЗЕМЛИВШЕМСЯ
    # дереве: гейт имени версии называет коллизию раньше git'а. Перемерено — выборка
    # `tests/unit/test_release_script.py`, collected 40 во всех раундах, `__pycache__` вычищен
    # перед каждым и `PYTHONDONTWRITEBYTECODE=1`: control 0 failed; узкий ассерт при ЦЕЛОМ
    # гейте 2 failed — ровно этот пин и его сосед из #723, и оба наблюдают «ALREADY TAKEN»,
    # литерала `fatal:` у них нет; узкий ассерт при СНЯТОМ гейте 4 failed, и ни одного из этих
    # двух пинов среди упавших НЕТ, потому что там литерал возвращается. Значит чужой свип
    # узкий ассерт не раздувает ни на единицу: раунд «снять гейт» равен четырём при обоих
    # ассертах (перемерено обоими). Ослабление куплено не за размер того раунда, а за то, что
    # иначе два ПРИЗЕМЛИВШИХСЯ пина краснели бы прямо на дереве с ЦЕЛЫМ гейтом.
    assert NEXT_VERSION in done.stderr and (
        "already exists" in done.stderr or "ALREADY TAKEN" in done.stderr
    ), done.stderr
    # Даже падая, шаг ничего не пушит: `git tag` стоит ДО ОБОИХ push'ей (их с #723 два —
    # атомарный bump+тег и канал; счёт «четыре» из прежней редакции включал локальный
    # `git branch -f`, который push'ем не является).
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


def _tag_named_main_on_a_decoy_above_the_tip(stand) -> str:
    """Тег `main` на коммите, которого на ветке `main` НЕТ и который СОДЕРЖИТ вершину.

    Оба свойства несущие. «Не на ветке» — чтобы полный рефспек и короткий давали РАЗНЫЙ
    ответ (у соседнего теста тег висит на той же вершине, поэтому разница видна только через
    объект тега, то есть требует снятия обеих защит сразу). «Содержит вершину» — чтобы
    подменённый ответ прошёл ОБА конъюнкта `superseded_by_neighbour` и был принят за
    накрытие: иначе короткий рефспек дал бы просто «не накрыли», и релиз состоялся бы
    вопреки мутации.
    """
    _git(stand.seed, "commit", "-q", "--allow-empty", "-m", "decoy above the tip")
    decoy = _git(stand.seed, "rev-parse", "HEAD").stdout.strip()
    _git(stand.seed, "tag", "-a", "main", "-m", "tag named main", decoy)
    _git(stand.seed, "push", "-q", "origin", "refs/tags/main")
    _git(stand.seed, "reset", "-q", "--hard", stand.c0)   # ветку `main` на origin не двигаем
    return decoy


def test_a_tag_named_main_above_the_tip_does_not_fake_a_supersession(stand):
    """ПОЛНЫЙ рефспек чтения вершины, запиненный ПООДИНОЧКЕ (tracker #750).

    Соседний test_a_tag_named_main_does_not_fake_a_supersession держит обе защиты ТОЛЬКО В
    ПАРЕ: там тег висит на ТОЙ ЖЕ вершине, поэтому уход fetch на тег наблюдаем лишь через
    объект тега — а его разыменовывает `^{commit}`. Перемерено ЗДЕСЬ, а не унаследовано от
    #737, потому что с тех пор #723 добавила третье чтение рефа: выборка = ровно тот тест,
    control 0 failed, обе половины сняты сразу 1 failed, а каждая по отдельности его не
    трогает (видно по спискам падений в свипе по всему файлу).

    Здесь тег стоит на ДРУГОМ коммите, так что подменённый ответ отличается уже как КОММИТ,
    и второй защите нечего маскировать: короткий рефспек убивает этот тест В ОДИНОЧКУ, а
    снятый peel его не трогает (дельты — в
    test_with_a_short_tip_refspec_a_tag_named_main_swallows_the_release и в worklog).

    Цена промаха — не косметическая: гейт прочитал бы «меня накрыли» на доске, где вершина
    РОВНО мой коммит, и релиз молча не состоялся бы.
    """
    decoy = _tag_named_main_on_a_decoy_above_the_tip(stand)
    assert decoy != stand.c0

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" not in done.stdout
    assert stand.remote_tags() == [NEXT_VERSION]
    tip = stand.remote_main()
    assert _git(stand.origin, "rev-parse", f"{tip}^").stdout.strip() == stand.c0
    assert stand.remote_stable() == tip


def test_with_a_short_tip_refspec_a_tag_named_main_swallows_the_release(stand):
    """МУТАЦИЯ: чтение вершины по короткому имени — релиз обязан пропасть.

    Дефект дословно: fetch уходит на тег, вершиной оказывается декой, он содержит мой sha,
    предтеговой гейт честно говорит «накрыли», и job уходит в ЗЕЛЁНЫЙ skip. Гейт честности
    #740 тут не спасает и не должен: тега `v*` на декое нет, значит P2 выполняется ЧЕСТНО —
    вершину, которой не существует, действительно никто не выпускал.
    """
    _tag_named_main_on_a_decoy_above_the_tip(stand)

    work = stand.checkout("w", stand.c0, release_sh=_release_sh_with_a_short_tip_refspec())
    done = stand.release_job(work, stand.c0)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" in done.stdout          # ...и это ЛОЖЬ: вершина — мой же sha
    assert stand.remote_main() == stand.c0           # main не двинулся вовсе
    assert stand.remote_tags() == []                 # версии не существует
    assert stand.remote_stable() is None             # канал не двинулся


def test_the_version_tag_is_read_as_a_commit_not_as_a_tag_object(stand):
    """`^{commit}` в `read_remote_ref`, запиненный ПООДИНОЧКЕ (tracker #750).

    Наблюдаем он ровно на ОДНОМ из трёх чтений — на `read_tag`, заведённом #723. На ветке
    peel — no-op (ветка не может указывать на не-коммит: сервер отбивает такой push), и
    именно поэтому свип #737 нашёл эту защиту «неубиваемой». `read_tag` спрашивает
    `refs/tags/${VERSION}`, а `git tag -a` создаёт АННОТИРОВАННЫЙ тег — то есть единственное
    место, где непропиленное чтение возвращает НЕ коммит.

    Цена промаха — ЛОЖНАЯ ТРЕВОГА на здоровом релизе: сравнение `tag_commit != head`
    становится истинным всегда, и job краснеет с «the push was accepted NON-atomically» на
    push'е, который прошёл атомарно (мутационный сосед строит это дословно).
    """
    work = stand.checkout("w", stand.c0)
    shim = stand.shim_push_lands_but_fails()
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "NON-atomically" not in done.stderr, "ложная тревога на здоровом атомарном push'е"
    assert "but landed" in done.stdout
    tip = stand.remote_main()
    assert stand.remote_tags() == [NEXT_VERSION]
    assert stand.remote_stable() == tip

    # ПРЕМИССА пина мерится ЗДЕСЬ ЖЕ, а не принимается на веру: без неё peel был бы no-op и
    # этот тест сертифицировал бы собственную зелень. Тег, который релиз только что нарезал,
    # обязан быть аннотированным, а `fetch` по его ИМЕНИ — оставлять в FETCH_HEAD объект тега.
    assert _git(stand.origin, "cat-file", "-t", NEXT_VERSION).stdout.strip() == "tag"
    probe = stand.root / "peel-probe"
    _git(stand.root, "clone", "-q", str(stand.origin), str(probe))
    _git(probe, "fetch", "-q", "origin", f"refs/tags/{NEXT_VERSION}")
    unpeeled = _git(probe, "rev-parse", "FETCH_HEAD").stdout.strip()
    peeled = _git(probe, "rev-parse", "FETCH_HEAD^{commit}").stdout.strip()
    assert peeled == tip
    assert unpeeled != peeled, (
        "FETCH_HEAD аннотированного тега перестал быть объектом тега: peel стал no-op, и "
        "мутационный сосед ниже больше ничего не доказывает — перемерить #750, а не ослаблять"
    )


def test_without_the_peel_a_landed_push_is_falsely_called_non_atomic(stand):
    """МУТАЦИЯ: снят `^{commit}` — здоровый релиз обязан покраснеть ЛОЖНО.

    Тот же вход, что у соседа выше: push приземлился АТОМАРНО (тег на remote есть и стоит
    ровно на bump'е), клиент соврал отказом. Без peel `read_tag` возвращает объект тега,
    сравнение с `head` не сходится НИКОГДА, и скрипт объявляет атомарный push неатомарным —
    после чего канал не двигается и релиз остаётся недособранным.
    """
    work = stand.checkout("w", stand.c0, release_sh=_release_sh_without_the_peel())
    shim = stand.shim_push_lands_but_fails()
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode != 0, done.stdout + done.stderr
    assert "NON-atomically" in done.stderr
    assert stand.remote_stable() is None                       # канал не двинут...
    assert stand.remote_tags() == [NEXT_VERSION]               # ...хотя тег НА МЕСТЕ
    assert _git(stand.origin, "rev-list", "-n1", NEXT_VERSION).stdout.strip() \
        == stand.remote_main()                                 # то есть тревога ложная


def test_a_landed_push_that_reported_failure_still_releases(stand):
    """ПЕРВЫЙ вопрос перепроверки: push приземлился, клиент соврал отказом (tracker #716).

    Ни второго актора, ни человека, ни второй попытки: я ВЕРШИНА, прогон один. Круг 1
    этой карточки задавал только ВТОРОЙ вопрос («меня накрыли?») — и получал «да», потому
    что накрыл его СОБСТВЕННЫЙ приземлившийся bump: тега нет, `stable` не двинулся, job
    ЗЕЛЁНЫЙ. Здесь релиз обязан ДОЕХАТЬ: с #723 тег уехал ВМЕСТЕ с bump'ом (шим выполняет
    push по-настоящему), поэтому доводить осталось ровно канал — что и проверяют ассерты.
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


def test_without_the_landed_question_the_release_is_lost(stand):
    """МУТАЦИЯ: снимаем ПЕРВЫЙ вопрос — релиз обязан не состояться.

    Негативный пин не считается пином, пока не показано, что он краснеет от снятия
    ИМЕННО своей защиты: без этого он сертифицирует собственную зелень.

    ПРЕДМЕТ ПИНА СУЗИЛА #740, и это надо было заметить, а не переписать ассерт молча. Тест
    назывался «...silently_lost» и мерил ТИШИНУ — зелёный job без сдвига канала. Тишину у
    этой мутации отобрал гейт честности skip'а: вершина здесь — МОЙ СОБСТВЕННЫЙ приземлившийся
    bump, на нём мой тег, а канал меня не несёт, то есть ровно та форма, на которую гейт и
    краснеет (измерено: с гейтом эта мутация даёт rc 1 там, где до него давала rc 0). Значит
    два гварда ПЕРЕКРЫЛИСЬ, и остаток, который держит только ПЕРВЫЙ вопрос, — не «громко ли»,
    а «состоялся ли релиз»: без него канал не двигается вовсе (соседний
    test_a_landed_push_that_reported_failure_still_releases — тот же вход с гвардом, там
    `stable` встаёт на bump). Ассерты ниже поэтому спрашивают про ПОТЕРЮ, а не про тишину.
    """
    work = stand.checkout("w", stand.c0, release_sh=_release_sh_without_the_landed_question())
    shim = stand.shim_push_lands_but_fails()
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode != 0, done.stdout + done.stderr
    assert "release skipped" not in done.stdout      # накрытым себя больше не объявляет...
    assert "but landed" not in done.stdout           # ...но и вопроса «а не уехало ли?» нет
    assert stand.remote_stable() is None             # канал не двинулся — релиз не собран
    # ...при том что bump УЖЕ на main: состояние полу-собранное.
    assert '"0.2.171"' in stand.remote_file("refs/heads/main", "src/vikunja_mcp/__init__.py")
    # С #723 тег уехал ВМЕСТЕ с bump'ом (шим выполняет push по-настоящему), поэтому
    # полу-собрано тут ровно одно — канал.
    assert stand.remote_tags() == [NEXT_VERSION]


def test_a_landed_push_without_its_tag_is_loud(stand):
    """#723: зелёная ветка перепроверки не верит серверу на слово и про ТЕГ тоже.

    Вход построил атакующий проход: сервер ОБЪЯВЛЯЕТ `atomic`, берёт ветку, роняет рефспек
    тега и врёт клиенту отказом. Ветка «а не приземлилось ли?» — единственная, которая после
    отбитого push'а идёт в ЗЕЛЁНОЕ, и раньше она спрашивала remote только про `main`: тег
    считался взятым «раз атомарно». На этом входе получался rc 0 и релиз БЕЗ ТЕГА, то есть
    ровно дыра #723 — причём внесённая её же починкой (до неё отдельный push тега тег
    восстанавливал). Теперь ветка спрашивает и про тег, а «не смог спросить» — тоже красное.

    Свип, выборка `tests/unit/test_release_script.py`, collected 31 во всех раундах,
    возмущался МИР (`scripts/release.sh`), а не тело теста: control 0 failed; снять проверку
    тега (`if false` вместо условия) 1 failed — ровно ЭТОТ тест. Он же — причина не читать
    «`--atomic` избавляет от вопросов к remote»: избавляет от ВТОРОГО PUSH'А, не от проверки.
    """
    work = stand.checkout("w", stand.c0)
    shim = stand.shim_push_drops_the_tag_and_lies()
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode != 0, done.stdout
    assert "finishing the release" not in done.stdout
    assert "NON-atomically" in done.stderr
    my_bump = _git(work, "rev-parse", "HEAD").stdout.strip()
    assert stand.remote_main() == my_bump          # ветку сервер взял...
    assert stand.remote_tags() == []               # ...а тег нет, и это замечено
    assert stand.remote_stable() is None           # канал не двинут, и job красный


def test_a_landed_push_with_a_newer_tip_on_top_is_loud(stand):
    """Приземлилось, но поверх УЖЕ легло новее: ЗВУК ВАЖНЕЕ ТИШИНЫ (tracker #716).

    Обоснование красноты тут переписывалось ДВАЖДЫ, и оба раза потому, что становилось
    ложным. #737 обнулил первое («форс-push откатил бы канал назад»): `-f` с канала убрали.
    #723 обнулил второе («тега на remote нет»): bump и тег теперь уезжают ОДНОЙ
    транзакцией, шим выполняет push по-настоящему — значит тег ЕСТЬ, и текст «tag NOT
    pushed» стал бы враньём в логе job'а. Красным это остаётся по третьей, устойчивой
    причине: канал не двинут, релиз собран не до конца.

    Поэтому тест теперь пинит и НАБЛЮДАЕМОЕ состояние (тег на месте, канал нет), и то, что
    сообщение об этом состоянии не врёт.

    Что вторая половина — не украшение, показал отдельный раунд свипа: выборка
    `tests/unit/test_release_script.py`, collected 31 во всех раундах, возмущался МИР
    (`scripts/release.sh`), control 0 failed; вернуть в эту ветку прежний текст
    «tag ${VERSION} NOT pushed» 1 failed — ровно ЭТОТ тест. Исход job'а мутация не меняет
    вовсе (он красный и так), меняется только правдивость строки в логе — то есть без этого
    ассерта проза устарела бы молча.
    """
    work = stand.checkout("w", stand.c0)
    shim = stand.shim_push_lands_but_fails(then_land_sibling=True)
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode != 0, done.stdout
    assert "release skipped" not in done.stdout
    assert "stable NOT moved" in done.stderr
    assert f"tag {NEXT_VERSION} pushed with it" in done.stderr
    assert "NOT pushed" not in done.stderr.replace("stable NOT moved", "")
    assert stand.remote_tags() == [NEXT_VERSION]
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


def _refuse_tag_pushes_per_ref(stand) -> None:
    """`update`-хук: отвергает РОВНО `refs/tags/*`, остальные рефы пачки проходят.

    Это форма, в которой отказывает ref-protection у хостинга, и единственная, на которой
    видно работу `--atomic`: `pre-receive` (ниже) валит push целиком и без флага.
    """
    hook = stand.origin / "hooks" / "update"
    hook.write_text(
        "#!/bin/sh\n"
        'case "$1" in refs/tags/*) echo "refused $1" >&2; exit 1;; esac\n'
        "exit 0\n"
    )
    hook.chmod(0o755)


def _refuse_tag_pushes_whole_push(stand) -> None:
    """`pre-receive`-хук: увидев тег, отвергает ВСЮ пачку — так этот хук и устроен."""
    hook = stand.origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read -r old new ref; do\n"
        '  case "$ref" in refs/tags/*) echo "refused $ref" >&2; exit 1;; esac\n'
        "done\n"
        "exit 0\n"
    )
    hook.chmod(0o755)


def test_a_refused_tag_leaves_nothing_behind(stand):
    """#723: отказ на теге больше не оставляет ПОЛУ-СОСТОЯНИЯ — ни bump'а, ни тега.

    Раньше тег пушился ОТДЕЛЬНОЙ командой ПОСЛЕ успешного push'а в main, и отказ на ней
    (сеть, 5xx, защита рефов) оставлял версию БЕЗ ТЕГА: измерено на этом же стенде —
    `tags = []` при `__version__ = "0.2.171"` на вершине main, exit 1. Само это не
    лечилось: перезапуск ТОГО ЖЕ job'а отвечал зелёным `release skipped` (предтеговой гейт
    читает собственный осиротевший bump как «меня накрыли»), а следующее приземление
    выпускало v0.2.172 — то есть пропущенная версия не появлялась НИКОГДА, и откатить на
    неё канал было уже нечем.

    Теперь bump и тег едут ОДНОЙ серверной транзакцией, поэтому тот же вход оставляет
    remote нетронутым. Проверяется именно ЭТО (`main` == c0 и версия на вершине всё ещё
    базовая), а не только красный код возврата: громким этот путь был и раньше.

    Хук здесь ПО-РЕФОВЫЙ (`update`), а не `pre-receive`, и это не деталь стенда: он моделирует
    ref-protection хостинга и он единственный, на котором видно работу `--atomic`. Соседний
    test_with_a_separate_tag_push_the_half_state_comes_back берёт `pre-receive` ровно затем,
    чтобы показать вторую половину защиты и её границу.

    Свип, выборка `tests/unit/test_release_script.py`, collected 31 во всех раундах,
    возмущался МИР (`scripts/release.sh`), а не тело теста: control 0 failed; снять ТОЛЬКО
    `--atomic` 8 failed; вернуть форму до #723 (main отдельным push'ем, тег вторым, после)
    8 failed. ЭТОТ тест среди упавших в ОБОИХ раундах и оба раза ПОВЕДЕНЧЕСКИ — то есть он
    один держит обе половины защиты. Разбор состава каждого раунда — в докстрингах
    соответствующих мутационных соседей.
    """
    _refuse_tag_pushes_per_ref(stand)

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0, done.stdout
    assert "release skipped" not in done.stdout
    assert stand.remote_main() == stand.c0, "bump не должен был уехать без своего тега"
    assert '"0.2.170"' in stand.remote_file("refs/heads/main", "src/vikunja_mcp/__init__.py")
    assert stand.remote_tags() == []
    assert stand.remote_stable() is None


def test_without_atomic_the_refused_tag_bumps_main_anyway(stand):
    """МУТАЦИЯ к предыдущему: снимаем ТОЛЬКО флаг — полу-состояние из карточки возвращается.

    Оба рефспека остаются в ОДНОЙ команде, меняется ровно `--atomic`. На ПО-РЕФОВОМ отказе
    этого достаточно: сервер берёт main и отвергает тег, и на remote снова «bump 0.2.171 без
    тега» — та самая пропущенная навсегда версия, ради которой заведена #723. Значит работу
    делает АТОМАРНОСТЬ, а не то, что рефспеки лежат рядом.

    ЗАЧЕМ ТУТ ДВА СЛОЯ И ЧЕМ ОНИ РАЗНЫЕ. Отбой этой мутации ловит уже НЕ отсутствие состояния,
    а проверка тега в ветке «а не приземлилось ли?» — job краснеет с «the push was accepted
    NON-atomically». Промерено, что без ТОЙ проверки эта же мутация уходила в ЗЕЛЁНОЕ
    (перепроверка видела свой bump на main, печатала `finishing the release`, двигала канал,
    rc 0 — релиз без тега и без единого сигнала), и именно этот прогон её и завёл. Итог:
    `--atomic` не даёт состоянию ВОЗНИКНУТЬ, проверка тега не даёт ему пройти ТИХО, и путать
    их нельзя — здесь на remote полу-состояние ЕСТЬ (bump уехал), просто оно замечено.

    Свип, выборка `tests/unit/test_release_script.py`, collected 31 во всех раундах,
    возмущался МИР (`scripts/release.sh`): control 0 failed; снять ТОЛЬКО `--atomic` 8 failed;
    снять проверку тега в зелёной ветке 2 failed, и ЭТОТ тест — один из двух (второй
    `test_a_landed_push_without_its_tag_is_loud`), потому что он держит ИМЕННО второй слой.
    Из восьми в первом раунде ПЯТЬ поведенческих — `test_a_refused_tag_leaves_nothing_behind`,
    этот,
    `test_a_lost_race_does_not_squat_the_version_name`,
    `test_a_server_without_atomic_support_pushes_nothing` и ПОСТОРОННИЙ, ранее существовавший
    пин `test_a_tip_that_does_not_contain_us_is_not_superseded` (вместе с
    `test_without_the_guard_a_free_tag_name_does_not_help`, ловящим тег-сироту своим
    `remote_tags() == []`). Остальные ТРИ — сработавшие гарды мутационных хелперов
    (`assert 0 == 1`: якоря в файле уже нет, хелпер отказывается стать тавтологией);
    убийствами они не являются, поэтому названы отдельно. Классификация не выведена из
    названий — прогнана отдельным раундом и прочитана по тексту ассертов.
    """
    _refuse_tag_pushes_per_ref(stand)

    work = stand.checkout("w", stand.c0, release_sh=_release_sh_without_atomic())
    done = stand.release_job(work, stand.c0, path_prefix=None)

    assert done.returncode != 0, done.stdout + done.stderr
    assert "NON-atomically" in done.stderr            # поймал второй слой, а не первый
    assert "finishing the release" not in done.stdout
    assert stand.remote_main() != stand.c0            # ...но bump на remote УЖЕ уехал
    assert '"0.2.171"' in stand.remote_file("refs/heads/main", "src/vikunja_mcp/__init__.py")
    assert stand.remote_tags() == []                  # версия v0.2.171 не появится НИКОГДА
    assert stand.remote_stable() is None


def test_with_a_separate_tag_push_the_half_state_comes_back(stand):
    """ВТОРАЯ мутация: возвращаем ФОРМУ до #723 — тег отдельным push'ем ПОСЛЕ main.

    Отличается от предыдущей и ВХОДОМ, и тем, что держит: хук здесь `pre-receive`, то есть
    сервер отвергает пачку ЦЕЛИКОМ. На таком входе снятие одного лишь `--atomic` НИЧЕГО не
    ломает (измерено: неатомарная пачка тоже не оставляет ничего — `pre-receive` по
    устройству один на push, а не на реф), и мутация была бы зелёной. Полу-состояние тут
    возвращает именно РАЗДЕЛЬНЫЙ push. Так и распределены две половины защиты: «одна
    команда» покрывает отказ-целиком, `--atomic` — отказ по-рефовый.

    Свип, выборка `tests/unit/test_release_script.py`, collected 31 во всех раундах,
    возмущался МИР (`scripts/release.sh`): control 0 failed; вернуть форму до #723 8 failed.
    Из восьми ПЯТЬ поведенческих (`test_a_refused_tag_leaves_nothing_behind`, ТРИ пина ветки
    «приземлилось» — включая `test_a_landed_push_that_reported_failure_still_releases`, где
    под этой мутацией тег к моменту перепроверки ещё не запушен, — и
    `test_a_server_without_atomic_support_pushes_nothing`), ТРИ — гарды мутационных хелперов
    на исчезнувший якорь.

    Что мутация ПРИМЕНИЛАСЬ, а не промахнулась мимо изменившегося текста, гарантирует
    `count == 1` в самом хелпере: свип это подтвердил с другой стороны — в раунде «снять
    только `--atomic`» ЭТОТ тест падает не поведением, а как раз этим гардом
    (`assert 0 == 1`, якоря в файле больше нет).
    """
    _refuse_tag_pushes_whole_push(stand)

    work = stand.checkout("w", stand.c0, release_sh=_release_sh_with_a_separate_tag_push())
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0, done.stdout
    assert stand.remote_main() != stand.c0
    assert '"0.2.171"' in stand.remote_file("refs/heads/main", "src/vikunja_mcp/__init__.py")
    assert stand.remote_tags() == []                 # версия v0.2.171 не появится НИКОГДА
    assert stand.remote_stable() is None


def test_a_lost_race_does_not_squat_the_version_name(stand):
    """#723, ВТОРАЯ причина держать `--atomic`: проигранная гонка не оставляет тег-сироту.

    Сиблинг приземляется в ОКНЕ между предтеговым гейтом и push'ем (шим, никаких хуков:
    push настоящий и отбивается настоящим сервером). Исход по доске — обычный зелёный skip
    «меня накрыли», и он тут не главное. Главное — что на remote НЕ ПОЯВИЛОСЬ имя версии.

    Почему это отдельный тест, а не оговорка: тег-сироту (имя версии на коммите, которого
    нет в main) следующее приземление НЕ ЛЕЧИТ, а упирается в него — версия на вершине main
    так и осталась базовой, поэтому каждый следующий job считает ТУ ЖЕ версию и умирает на
    `fatal: tag 'v0.2.171' already exists`. Измерено: два приземления подряд, оба rc=128,
    релизы встали НАВСЕГДА. Это строго хуже пропущенной версии, ради которой карточку и
    заводили, — поэтому «просто сложить два рефспека в один push» без `--atomic` было бы
    не дешёвой заменой, а регрессией.

    Свип этого раунда записан у мутационного соседа
    (`test_without_atomic_the_refused_tag_is_swallowed_green`): выборка
    `tests/unit/test_release_script.py`, collected 31 во всех раундах, control 0 failed;
    снять ТОЛЬКО `--atomic` 8 failed, и ЭТОТ тест среди пяти поведенческих — падает на
    `remote_tags() == []` с текстом «имя версии занято коммитом, которого нет в main».
    """
    work = stand.checkout("w", stand.c0)
    shim = stand.shim_sibling_lands_just_before_push()
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" in done.stdout
    assert stand.remote_tags() == [], "имя версии занято коммитом, которого нет в main"
    assert stand.remote_stable() is None


def test_without_atomic_a_lost_race_squats_the_version_name(stand):
    """МУТАЦИЯ к предыдущему: без `--atomic` тег-сирота появляется при ЗЕЛЁНОМ job'е.

    Тот же стенд, тот же шим, единственная разница — снят флаг. Сервер берёт тег (он новый)
    и отбивает main (он не-ff), перепроверка честно видит «меня накрыли» и выходит нулём:
    job зелёный, а имя версии занято навсегда. Ровно этот зелёный и делает дефект опасным.

    КЛИН у следующего приземления с #769 ОЗВУЧЕН, а не убран: тег-сирота — вторая форма того
    же класса, что и чужой тег-сквоттер, поэтому гейт имени версии ловит и его, и следующее
    приземление краснеет уже со своим объяснением вместо голого `fatal: tag … already exists`
    (тот литерал пинит `test_without_the_version_name_gate_the_squatter_is_cryptic`). Что
    осталось прежним и здесь несущее: клин НИКУДА НЕ ДЕЛСЯ — версия на вершине main так и не
    двинулась, поэтому каждое следующее приземление считает ТУ ЖЕ версию.
    """
    work = stand.checkout("w", stand.c0, release_sh=_release_sh_without_atomic())
    shim = stand.shim_sibling_lands_just_before_push()
    done = stand.release_job(work, stand.c0, path_prefix=shim)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" in done.stdout
    assert stand.remote_tags() == [NEXT_VERSION]
    # ...и тег висит НЕ на вершине main: версия там осталась базовой.
    assert '"0.2.170"' in stand.remote_file("refs/heads/main", "src/vikunja_mcp/__init__.py")

    # И это КЛИН, а не разовая потеря: следующее приземление считает ТУ ЖЕ версию.
    nxt = stand.land_sibling("nxt")
    again = stand.release_job(
        stand.checkout("nxt-job", nxt, release_sh=_release_sh_without_atomic()), nxt
    )
    assert again.returncode != 0
    # Как и у соседа выше: пин про КЛИН, а не про то, кто о нём сообщает (см. там же, почему
    # ассерт на один текст сделал бы его сенсором гейта имени версии).
    assert NEXT_VERSION in again.stderr and (
        "already exists" in again.stderr or "ALREADY TAKEN" in again.stderr
    ), again.stderr
    assert '"0.2.170"' in stand.remote_file("refs/heads/main", "src/vikunja_mcp/__init__.py")


def test_a_server_without_atomic_support_pushes_nothing(stand):
    """Новая зависимость названа и запинена: атомарность — свойство СЕРВЕРА, не клиента.

    `--atomic` требует, чтобы receive-pack advertise'ил соответствующую возможность. Важно
    не то, что GitHub её advertise'ит (измерено двумя каналами — чтением
    `info/refs?service=git-receive-pack` по HTTPS и `git push --atomic --dry-run`), а то,
    что будет, ЕСЛИ перестанет: тихой деградации до неатомарного push'а не происходит —
    клиент отказывается пушить вовсе (`fatal: the receiving end does not support --atomic
    push`), remote остаётся нетронутым, и отказ попадает в красную ветку перепроверки.
    То есть отказ этой зависимости — громкий и с ЧИСТЫМ состоянием.

    Пин ПОВЕДЕНЧЕСКИЙ, а не текстовый, и это видно по свипу: выборка
    `tests/unit/test_release_script.py`, collected 31 во всех раундах, возмущался МИР
    (`scripts/release.sh`), control 0 failed; снять `--atomic` 8 failed, а вернуть
    до-#723 форму 8 failed — ЭТОТ тест падает в ОБОИХ, и оба раза поведением
    (`assert 0 != 0`: без флага push проходит, и никакого «does not support» в stderr нет),
    а не проверкой строчки в файле.
    """
    stand.disable_atomic_push()

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0, done.stdout
    assert "release skipped" not in done.stdout
    assert "does not support --atomic push" in done.stderr
    assert stand.remote_main() == stand.c0
    assert stand.remote_tags() == []
    assert stand.remote_stable() is None


def test_a_version_name_squatted_after_checkout_is_named_not_guessed_at(stand):
    """ПЕРВЫЙ красный сквоттера перестаёт называть не тот предмет (tracker #769).

    Чужой тег `v0.2.171` появляется на origin ПОСЛЕ чекаута job'а, поэтому в его клоне тега
    нет, локальный `git tag -a` проходит, и всё решает push. До гейта этот job доезжал до
    push'а и падал в ОБЩУЮ ветку перепроверки — «no newer landing containing …», — которая
    читается как права или защита ветки, то есть как совсем другая беда (пин на это —
    мутационный сосед ниже). Теперь он называет имя, объект и механику.

    Вердикт при этом НЕ МЕНЯЕТСЯ и это здесь главное: до гейта job тоже был красным и тоже
    ничего не пушил — проверяются все три рефа. Гейт покупает сообщение, а не исход.
    """
    work = stand.checkout("w", stand.c0)              # чекаут ДО появления сквоттера
    foreign = stand.squat_the_version_name()
    done = stand.release_job(work, stand.c0)

    assert done.returncode != 0, done.stdout
    assert f"the version name {NEXT_VERSION} is ALREADY TAKEN" in done.stderr
    assert f"git push origin :refs/tags/{NEXT_VERSION}" in done.stderr
    assert "no newer landing containing" not in done.stderr

    assert stand.remote_main() == stand.c0
    assert stand.remote_tags() == [NEXT_VERSION]
    assert _git(stand.origin, "rev-list", "-n1", NEXT_VERSION).stdout.strip() == foreign
    assert stand.remote_stable() is None


def test_a_squatted_version_name_wedges_every_later_landing_the_same_way(stand):
    """КЛИН: имя занято → не уезжает ничто → следующий job считает ТУ ЖЕ версию (#769).

    Это измеренное состояние карточки: под `--atomic` не приземляется НИЧЕГО, поэтому версия
    на вершине main не двигается, `bump_version.py` у каждого следующего приземления считает
    то же самое имя, и так до вмешательства человека. Инвариант репозитория цел и до гейта —
    каждый прогон КРАСНЫЙ, тихого зелёного тут нет ни одного, — а гейт меняет то, ЧТО эти
    прогоны говорят: вместо голого `fatal: tag … already exists` (мутационный сосед ниже
    возвращает его дословно) каждый называет причину, команду и то, что само не зарастёт.

    Три приземления, а не одно, потому что «клин» — утверждение про ПОВТОРЯЕМОСТЬ: разовый
    красный от вечного отличается только тем, что следующий такой же.
    """
    stand.squat_the_version_name()

    for n in (1, 2, 3):
        sha = stand.land_sibling(f"land{n}")
        done = stand.release_job(stand.checkout(f"job{n}", sha), sha)

        assert done.returncode != 0, done.stdout
        assert f"the version name {NEXT_VERSION} is ALREADY TAKEN" in done.stderr
        assert f"fatal: tag '{NEXT_VERSION}' already exists" not in done.stderr
        assert stand.remote_main() == sha, "приземлился только таск-коммит, bump — нет"
        # версия на вершине не двинулась, поэтому следующий круг посчитает ТО ЖЕ имя
        assert '"0.2.170"' in stand.remote_file("refs/heads/main", "src/vikunja_mcp/__init__.py")

    assert stand.remote_tags() == [NEXT_VERSION], "своих тегов релиз не нарезал ни одного"
    assert stand.remote_stable() is None


def test_without_the_version_name_gate_the_squatter_is_cryptic(stand):
    """МУТАЦИЯ к двум предыдущим: снять гейт — и оба текста возвращаются дословно (#769).

    Одна мутация на два теста, потому что дефект один, а форм у него две, и обе надо вернуть:
    у job'а, чей чекаут ПРЕДШЕСТВУЕТ сквоттеру, тега локально нет и он падает уже на push'е,
    в ветку про «никакого более нового приземления не видно»; у КАЖДОГО следующего тег в
    чекауте есть, и он падает на `git tag -a` кодом 128, ничего не сказав.

    Свип, выборка `tests/unit/test_release_script.py`, collected 40 во всех раундах,
    возмущался МИР (`scripts/release.sh`), перед каждым раундом чистился `__pycache__` и
    стоял `PYTHONDONTWRITEBYTECODE=1`, применение мутации сверялось поиском обеих строк на
    диске, а восстановление — по sha256: control 0 failed; снять гейт целиком
    (`if version_name_taken` -> `if false`) 4 failed; снять ТОЛЬКО локальный источник (ветвь
    `elif` с `git rev-parse --verify`) 1 failed — ровно
    `..._still_names_a_squatter_in_the_checkout`, потому что у первого job'а имя читается с
    origin, а клин ловится тем же чтением; снять ТОЛЬКО чтение origin (`if false` на
    `ls-remote`) 1 failed — ровно `..._is_named_not_guessed_at`, потому что у всех
    последующих имя лежит в собственном чекауте; сделать чтение origin FAIL-CLOSED («спросить
    не смог» = «занято») 2 failed — `..._never_reds_a_healthy_release`, ради которого этот
    раунд и заводился, И `..._still_names_a_squatter_in_the_checkout`, потому что fail-closed
    отвечает «занято на origin» ещё до того, как локальный источник вообще спрашивают, и
    ассерт про `this checkout` перестаёт выполняться; control повторно 0 failed.

    ДВЕ вещи в этом свипе стоит прочитать, а не пролистать. ЭТОТ тест в раунде «снять гейт
    целиком» падает не поведением, а ассертом своей же мутации-хелпера (`if
    version_name_taken` в файле уже нет, значит подменять нечего) — это тот самый гард
    «переименуют — тест упадёт на мутации, а не тихо перестанет мутировать», и в четвёрку он
    входит как раз им. И четвёрка ОСТАЛАСЬ четвёркой после ослабления двух приземлившихся
    мутационных пинов (#716 и #723): ослабление куплено НЕ за размер этого раунда, а за то,
    что иначе оба пина краснели бы прямо на ПРИЗЕМЛИВШЕМСЯ дереве. Перемерено той же выборкой,
    collected 40 во всех раундах: control 0 failed; их прежний узкий ассерт на `fatal: tag …
    already exists` при ЦЕЛОМ гейте 2 failed — ровно эти два пина, и оба видят «ALREADY TAKEN»,
    потому что коллизию называет гейт имени версии, а не git; тот же узкий ассерт при СНЯТОМ
    гейте 4 failed — те же четыре, что и с широким, обоих пинов среди упавших нет, литерал
    вернулся.
    Поэтому они ослаблены до «коллизия названа кем угодно»: свойство пина сохранено, а сам
    литерал перевешен на ЭТОТ тест, где он и есть свойство «гейта нет».
    """
    mutated = _release_sh_without_the_version_name_gate()

    work = stand.checkout("w", stand.c0, release_sh=mutated)
    stand.squat_the_version_name()
    first = stand.release_job(work, stand.c0)
    assert first.returncode != 0, first.stdout
    assert "no newer landing containing" in first.stderr
    assert "ALREADY TAKEN" not in first.stderr

    sha = stand.land_sibling("nxt")
    again = stand.release_job(stand.checkout("nxt-job", sha, release_sh=mutated), sha)
    assert again.returncode != 0, again.stdout
    assert f"fatal: tag '{NEXT_VERSION}' already exists" in again.stderr
    assert "ALREADY TAKEN" not in again.stderr

    assert stand.remote_tags() == [NEXT_VERSION]
    assert stand.remote_stable() is None


def test_an_unanswerable_version_name_read_never_reds_a_healthy_release(stand):
    """ПОЛЯРНОСТЬ: у ЭТОГО чтения «спросить не смог» — зелёное, и это не рассогласование.

    Соседний `test_an_unanswerable_tag_read_is_never_a_skip` требует обратного, и оба верны,
    потому что вопросы разные. Там чтение тегов держит ЗЕЛЁНУЮ ветку (skip), поэтому молчание
    origin обязано быть красным. Здесь чтение держит ДИАГНОСТИКУ поверх ветки, которая красна
    при любом ответе, поэтому fail-closed купил бы ровно сообщение, а стоил бы ложного
    красного на каждом релизе, где флакнул один `ls-remote` — то есть ЗАМОРОЗКИ КАНАЛА ценой
    сетевого дребезга, при том что чинит гейт как раз замороженный канал.

    Молчание origin при этом не слепит гейт целиком: источников два, и второй — собственный
    чекаут job'а. Поэтому один и тот же сломанный `ls-remote` оставляет здоровый релиз
    зелёным и всё равно называет клин там, где тег в чекауте уже есть.

    ЭТОТ ПИН ПРИШЛОСЬ ДОКАЗЫВАТЬ ОТДЕЛЬНОЙ МУТАЦИЕЙ, и это ровно та ловушка, про которую
    репозиторий предупреждает: тест вида «такого быть НЕ должно» бывает зелен и с гардом, и
    без него. Замерено — в раунде «снять гейт целиком» он остаётся ЗЕЛЁНЫМ (без гейта
    здоровый релиз тем более зелен), то есть отсутствие гейта он не ловит и ловить не обязан.
    Ловит он ПОЛЯРНОСТЬ, поэтому мутация к нему своя: сделать чтение origin fail-closed
    («спросить не смог» = «занято»). Свип записан у соседа
    `test_without_the_version_name_gate_the_squatter_is_cryptic`; в том раунде control
    0 failed, мутация 2 failed, и ЭТОТ тест — первый из двух (второй, `..._in_the_checkout`,
    падает попутно: fail-closed отвечает раньше, чем спросят локальный источник).
    """
    shim = stand.shim_ls_remote_fails()

    done = stand.release_job(stand.checkout("healthy", stand.c0), stand.c0, path_prefix=shim)

    assert done.returncode == 0, done.stdout + done.stderr
    tip = stand.remote_main()
    assert stand.remote_tags() == [NEXT_VERSION]
    assert stand.remote_stable() == tip


def test_an_unanswerable_version_name_read_still_names_a_squatter_in_the_checkout(stand):
    """Вторая половина той же полярности: локальный источник ловит клин без origin (#769).

    Тег сквоттера уже лежит в чекауте job'а (actions/checkout тянет теги на момент чекаута),
    и это ровно то, на чём `git tag -a` падает кодом 128. Читается он локально, поэтому
    сломанный `ls-remote` диагностику не гасит.
    """
    stand.squat_the_version_name()
    sha = stand.land_sibling("land")
    shim = stand.shim_ls_remote_fails()

    done = stand.release_job(stand.checkout("job", sha), sha, path_prefix=shim)

    assert done.returncode != 0, done.stdout
    assert f"the version name {NEXT_VERSION} is ALREADY TAKEN" in done.stderr
    assert "this checkout" in done.stderr
    assert f"fatal: tag '{NEXT_VERSION}' already exists" not in done.stderr
    assert stand.remote_main() == sha
    assert stand.remote_stable() is None


def test_a_foreign_orphan_bump_no_longer_swallows_an_earlier_landing(stand):
    """ЧЕТВЁРТОЕ проглатывание ЗАКРЫТО В СВОЕЙ ФОРМЕ: накрытие ТРУПОМ красное (tracker #740).

    «В своей форме» — несущая оговорка, а не скромность: закрыт вход, который этот скрипт
    СПОСОБЕН построить, пока сервер соблюдает `atomic`, то есть вершина-bump С ТЕГОМ. Тот же
    осиротевший bump БЕЗ тега глотает как раньше, и это отдельный пин соседом —
    test_an_untagged_orphan_bump_still_swallows, где и разобрано, почему остаток не закрыт.

    Вершину main держит ЧУЖОЙ осиротевший bump: job сиблинга умер на push'е КАНАЛА, оставив
    свой bump и свой тег вершиной main. БОЛЕЕ РАННЕЕ приземление, чей job запускается ПОСЛЕ,
    раньше глоталось тут на своём ПЕРВОМ и ЕДИНСТВЕННОМ прогоне — перезапусков ноль, второго
    актора в его собственном пути нет, — потому что предтеговой гейт спрашивал ровно «содержит
    ли меня вершина», а на это труп отвечает «да» так же уверенно, как здоровый сосед.

    ЧТО ИМЕННО ЗАКРЫТО, а что нет. Гейт честности требует ПОЛОЖИТЕЛЬНОГО доказательства: либо
    канал уже несёт мой sha, либо вершину ещё НИКТО не выпускал (на ней нет версионного тега).
    Труп проваливает оба — тег на нём есть, канал стоит, — и job краснеет. Проглатывание (3)
    (вершина — таск-коммит, чей прогон красный/отменён) при этом НЕ закрыто и намеренно: там
    тега на вершине нет, доказательство P2 выполняется честно, и спросить «выпустят ли её»
    отсюда нечем. Соседний test_superseded_with_the_tag_still_free_also_skips — ровно тот
    вход, и он по-прежнему ЗЕЛЁНЫЙ.

    ЦЕНА КРАСНОГО ИЗМЕРЕНА, а не оценена: ассерты ниже показывают, что после красного на
    remote не двинулся НИ ОДИН из трёх рефов, за которые отвечает этот шаг (вершина, теги,
    канал), — ветка, на которую встал новый отказ, не пушит ничего.
    Поэтому он не пропускает версию, не занимает имя тега и не морозит канал: следующее
    приземление уводит канал дальше само, и последние четыре строки это ПРОГОНЯЮТ, а не
    обещают.

    КОНСТРУКЦИЮ ПЕРЕСОБРАЛА #723, СВОЙСТВО НЕ ТРОГАЛА. Раньше сирота строилась отказом на
    push'е ТЕГА: у сиблинга bump уезжал в main, а тег — нет. С атомарным push'ем такого
    состояния больше не существует, поэтому сирота строится следующей точкой отказа — push'ем
    КАНАЛА (см. `orphan_the_tip_with_a_foreign_bump`).
    """
    orphan, sib_tags = stand.orphan_the_tip_with_a_foreign_bump()

    # МОЙ job: первый и единственный прогон для c0, никаких шимов и перезапусков.
    done = stand.release_job(stand.checkout("mine", stand.c0), stand.c0)

    assert done.returncode != 0, done.stdout + done.stderr
    assert "release skipped" not in done.stdout
    assert "ALREADY TAGGED" in done.stderr
    assert stand.remote_main() == orphan          # ...и на remote не изменилось НИЧЕГО:
    assert stand.remote_tags() == sib_tags        # ни вершина, ни теги,
    assert stand.remote_stable() is None          # ни канал

    # Красный тут — предупреждение, а не тупик: канал догоняет на следующем приземлении.
    nxt = stand.land_sibling("nxt")
    later = stand.release_job(stand.checkout("nxt-job", nxt), nxt)
    assert later.returncode == 0, later.stdout + later.stderr
    channel = stand.remote_stable()
    assert channel is not None
    caught_up = _git(stand.origin, "merge-base", "--is-ancestor", stand.c0, channel, check=False)
    assert caught_up.returncode == 0, "канал обязан догнать проглоченное приземление"


def test_without_the_honest_skip_gate_the_foreign_orphan_swallows_again(stand):
    """МУТАЦИЯ к предыдущему: снимаем гейт честности — проглатывание возвращается дословно.

    Негативный пин не считается пином, пока не показано, что он краснеет от снятия ИМЕННО
    своей защиты. Мутация вставляет безусловный `skip` первой строкой `skip_or_refuse`, то
    есть возвращает поведение ДО #740, не трогая строку решения самого накрытия, — и job
    снова зелен на состоянии, которого не выпустит никто.
    """
    orphan, sib_tags = stand.orphan_the_tip_with_a_foreign_bump()

    work = stand.checkout("mine", stand.c0, release_sh=_release_sh_without_the_honest_skip_gate())
    done = stand.release_job(work, stand.c0)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" in done.stdout       # ЗЕЛЁНЫЙ, и это и есть проглатывание
    assert stand.remote_tags() == sib_tags        # моего релиза не случилось
    assert stand.remote_stable() is None          # канал не двинулся
    assert stand.remote_main() == orphan


def test_an_untagged_orphan_bump_still_swallows(stand):
    """ОСТАТОК #740, ПИН ИЗВЕСТНОГО ЗАЗОРА: осиротевший bump БЕЗ тега глотает по-прежнему.

    «Закрыто» у двух соседних пинов означает ровно ОДНУ форму — ту, которую этот скрипт
    СПОСОБЕН оставить, пока сервер соблюдает `atomic`: bump И ТЕГ на remote, не двинут
    `stable`. Тег и есть доказательство P2, поэтому осиротевший bump БЕЗ тега проходит P2
    честно и глотается зелёным ровно как до #740. Строится такая вершина не скриптом, а
    снаружи, и маршрута известно два: сервер, который `atomic` ОБЪЯВЛЯЕТ и не соблюдает (шим
    #723, он же в test_a_landed_push_without_its_tag_is_loud), и человек, стирающий версионный
    тег (`git push origin :refs/tags/vX.Y.Z` — ровно то лекарство, которое предписывает #769).

    Закрывать остаток НЕ стали, и причина измеренная, а не эстетическая: отличить осиротевший
    bump без тега от обычного таск-коммита можно только ПО ФОРМЕ вершины, а разбор темы
    коммита предыдущий круг уже отверг замером (control 0 failed; кандидат 5 failed, четыре
    падения — здоровые superseded-пути). Версия в файлах вершины не годится по той же причине
    с другой стороны: РУЧНОЙ minor/major-бамп — задокументированная процедура этого
    репозитория, он двигает версию, не будучи релизом, и стал бы ложным красным. Поэтому
    остаток НАЗВАН и ЗАПИНЕН — как названы (1) и (3), — а не закрыт наугад.
    """
    sibling = stand.land_sibling("sib")
    shim = stand.shim_push_drops_the_tag_and_lies()
    died = stand.release_job(stand.checkout("sib-job", sibling), sibling, path_prefix=shim)
    assert died.returncode != 0, died.stdout      # #723: сервер соврал, и это ЗАМЕЧЕНО

    orphan = stand.remote_main()
    assert orphan != sibling, "вершиной должен стать bump сиблинга, а не его таск-коммит"
    assert stand.remote_tags() == []              # ...но БЕЗ тега: сервер выбросил рефспек
    assert stand.remote_stable() is None

    done = stand.release_job(stand.checkout("mine", stand.c0), stand.c0)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "release skipped" in done.stdout       # P2 выполняется честно — и глотает
    assert stand.remote_main() == orphan
    assert stand.remote_tags() == []
    assert stand.remote_stable() is None


def test_a_rerun_over_its_own_orphan_bump_is_no_longer_green(stand):
    """Проглатывание (2) закрыто ТЕМ ЖЕ гейтом и в той же форме — ИЗМЕРЕНО (tracker #740).

    (2) — «любое полу-собранное состояние + ПЕРЕЗАПУСК = зелёный skip»: job, оставивший свой
    СОБСТВЕННЫЙ bump вершиной main, при `gh run rerun` читал эту вершину как «меня накрыли».
    С #723 форма такого полу-состояния ровно одна — bump и тег на remote, не двинут `stable`,
    — а её гейт честности и ловит: тег на вершине есть, канал меня не несёт. Отличие от (4)
    только в том, ЧЕЙ job умер; вопрос к состоянию один и тот же, поэтому и ответ один.

    Перезапуск при этом по-прежнему ничего не ЧИНИТ (канал так и стоит) — он перестаёт врать
    зелёным. Ровно этого от него и надо: `gh run rerun` ПЕРЕПИСЫВАЕТ вердикт того же прогона,
    поэтому зелёный перезапуск СТИРАЛ единственный красный, который у этого состояния был.

    Оговорка та же, что у (4): закрыта форма С ТЕГОМ. Полу-состояние без тега этот скрипт под
    `atomic` не строит, но снаружи оно достижимо, и тогда перезапуск снова зелен — см.
    test_an_untagged_orphan_bump_still_swallows.
    """
    hook = stand.origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read -r old new ref; do\n"
        '  case "$ref" in refs/heads/stable) echo "refused $ref" >&2; exit 1;; esac\n'
        "done\n"
        "exit 0\n"
    )
    hook.chmod(0o755)
    first = stand.release_job(stand.checkout("w", stand.c0), stand.c0)
    assert first.returncode != 0, first.stdout    # ПЕРВЫЙ прогон громкий и до #740
    hook.unlink()

    orphan = stand.remote_main()
    assert stand.remote_tags() == [NEXT_VERSION]  # МОЙ bump и МОЙ тег уехали атомарно
    assert stand.remote_stable() is None

    rerun = stand.release_job(stand.checkout("rerun", stand.c0), stand.c0)

    assert rerun.returncode != 0, rerun.stdout + rerun.stderr
    assert "release skipped" not in rerun.stdout
    assert "ALREADY TAGGED" in rerun.stderr
    assert stand.remote_main() == orphan
    assert stand.remote_stable() is None


def test_an_unanswerable_tag_read_is_never_a_skip(stand):
    """«Не смог спросить — не решай» у ЧЕТВЁРТОГО чтения — списка тегов (tracker #740).

    Вход тут БУКВАЛЬНО тот же, что у зелёного test_superseded_with_the_tag_still_free_also_
    skips: сиблинг приземлился, не релизясь, тегов на remote нет вовсе. Разница одна — шим,
    от которого не отвечает `git ls-remote`. Доказательство P2 («вершину ещё никто не
    выпускал») тогда НЕДОСТУПНО, а не опровергнуто, и скрипт обязан молчать красным, а не
    зеленеть на молчании: пустой ответ сломанного чтения выглядит ровно как «тегов нет».
    """
    sibling = stand.land_sibling("sib")

    work = stand.checkout("w", stand.c0)
    done = stand.release_job(work, stand.c0, path_prefix=stand.shim_ls_remote_fails())

    assert done.returncode != 0, done.stdout + done.stderr
    assert "release skipped" not in done.stdout
    # Текст ИМЕННО про список тегов: канал тут читался нормально и просто ответил «нет».
    assert "the tag list on origin could not be read" in done.stderr
    assert stand.remote_main() == sibling
    assert stand.remote_tags() == []
    assert stand.remote_stable() is None


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

    ПИН УЖЕ ОДНАЖДЫ ОСЛЕП НА СВОЁМ ЖЕ ПРЕДМЕТЕ, и починка — склейка `\\`-продолжений выше.
    #723 сделал первый push многострочным, и построчный скан перестал видеть строку с
    рефспеком тега вовсе. Свип по восьми написаниям (четыре формы × push тега и push
    канала), выборка — ЭТОТ ОДИН тест, возмущался МИР (`scripts/release.sh`), перед каждым
    раундом чистился `__pycache__` и стоял `PYTHONDONTWRITEBYTECODE=1`, печатался sha256:
    control 0 failed (1 passed) в обеих редакциях пина. ДО починки три написания на
    строке-ПРОДОЛЖЕНИИ (ведущий `+`, `-f` в конце, `--force`) — 0 failed КАЖДОЕ, то есть
    пропущены все три, при том что пять написаний на ОДНОСТРОЧНЫХ push'ах (main и канал)
    честно давали 1 failed. ПОСЛЕ починки все восемь дают 1 failed, а чистое дерево
    по-прежнему 0 failed — ложных срабатываний склейка не добавила.
    Цена пропуска измерена поведением, а не рассуждением: ведущий `+` на рефспеке тега при
    уже ОПУБЛИКОВАННОМ на remote `v0.2.171` даёт rc 0, молча перетирает этот тег с чужого
    коммита на свой bump и двигает канал — то есть ровно «зелёный job уничтожает
    неизменяемый тег», и всё это при зелёном тексте ЭТОГО теста.

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
    # ЛОГИЧЕСКИЕ строки, а не физические, и это не педантизм — это дыра, которую пин уже
    # имел. #723 сделал первый push МНОГОСТРОЧНЫМ (рефспек тега уехал на продолжение через
    # `\`), а скан шёл по ФИЗИЧЕСКИМ строкам и отбирал только те, где есть слова `git push`.
    # На строке-продолжении их нет, значит ни одно из четырёх написаний форса на ней не
    # проверялось вовсе. Измерено на стенде: ведущий `+` на рефспеке тега (форс БЕЗ флага,
    # написание №3 из тех, что этот пин обязан ловить) — job ЗЕЛЁНЫЙ, rc 0, уже
    # ОПУБЛИКОВАННЫЙ тег v0.2.171 молча перетёрт с чужого коммита на мой bump, канал двинут,
    # а этот тест при этом `1 passed`. Склейка продолжений возвращает пину его собственную
    # область. Побочно она же УСИЛИВАЕТ echo-гард: подсказка «Fix by hand: … git push -f …»
    # раньше отсекалась по открывающей кавычке, а теперь — по `echo` в начале той же
    # логической строки, то есть по признаку, который не зависит от переноса.
    logical = re.sub(r"\\\n\s*", " ", text)
    code = [ln for ln in logical.splitlines() if not ln.lstrip().startswith("#")]
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
    вернуло бы откат; после #737 откат ловит сам скрипт, а самые частые исходы гонок, которые
    группа предотвращает, стали ЗЕЛЁНЫМИ — skip «меня накрыли» и notice «канал уже впереди».
    Сказать «ни одного красного» нельзя (ветка «мой bump на main, а поверх легло новее»
    красная, и без группы она тоже учащается) — но НАДЁЖНОГО сигнала не оставалось, а
    ненадёжный сигнал не сигнал. Значит заметить удаление или переименование группы можно
    было ровно одним способом: спросить об этом текстом.

    С #740 обоснование ПОМЕНЯЛОСЬ, а пин остался: между атомарным push'ем и push'ем канала
    скрипт держит ровно ту форму, которую гейт честности читает как труп («bump с тегом,
    канал не двинут»), поэтому второй релизный job, попавший чтением в это окно, теперь
    краснеет — симптом у пропажи группы появился. Текстовый пин от этого не лишний: симптом
    ЛОЖНЫЙ (сосед достраивает релиз через секунду), а ловить пропажу конфигурации ложными
    красными на живых релизах — не то же самое, что спросить о ней прямо.

    Держит она две разные вещи: сериализацию (`group`) и то, что идущий
    релиз не отменяют на полпути между push'ями (`cancel-in-progress: false`), поэтому
    и спрашивается про обе — свип, выборка `tests/unit/test_release_script.py`,
    collected 25 во всех раундах: control 0 failed; убрать строку `group: release` из
    ci.yml 1 failed; убрать `cancel-in-progress: false` 1 failed, оба раза этот тест.
    """
    text = CI_YML.read_text()
    assert "concurrency:" in text
    assert "group: release" in text, "релизные job'ы обязаны сериализоваться одной группой"
    assert "cancel-in-progress: false" in text, (
        # Форму этого полу-состояния сузила #723: bump и тег теперь неделимы, поэтому
        # отмена между push'ями оставляет «bump и тег на main, канал не двинут», а не
        # «bump без тега». Само состояние никуда не делось — не делись и пин.
        "отмена идущего релиза оставит полу-состояние: bump и тег на main без канала"
    )


# --- VMCP-217 (760): the comment layer of release.sh, which nothing read ------------------------
#
# 81% of `scripts/release.sh` is prose — 627 comment lines of 772 — and until this pin NOTHING
# gated a byte of it. Measured on this tree, control 0 failed on the whole stand (44 selected):
# delete EVERY comment line and the same 44 are still 0 failed. The card filed against this
# measured a 96-line subset; the whole layer is the same answer, and a stronger one.
#   WHY THE EXISTING PINS CANNOT REACH IT. The stand next door mutates the script and RUNS it, so
# it reads exactly what the shell reads, and a comment is not that. `_mutated_release_sh` and its
# siblings deliberately assert their needles occur once so a mutation cannot go quiet — that is a
# guard on the CODE lines they rewrite. And the one text-shaped pin above it drops `#` lines BY
# DESIGN, because its subject is what the script does.
#   WHAT THIS PINS AND WHAT IT CANNOT. It is a PRESENCE check on five decisions a future editor
# could reverse in silence, chosen because each one is cross-referenced from CLAUDE.md's Releases
# section and each answers "why is it not written the obvious way?" — the questions whose answers
# get deleted first, precisely because the code looks fine without them. It cannot check that the
# prose is TRUE, or that it still matches the code beside it; a comment that goes stale in place
# passes this untouched. That bound is the same one test_repo_quotation_claims states about
# itself, and it is worth saying rather than leaving to be discovered: this closes "the layer can
# vanish", not "the layer is right".
#   NO LINE COUNT, deliberately. A floor like "at least N comment lines" is the shape this repo
# has been burned by twice — it moves with every landing and it is satisfied by 627 lines of
# anything. Naming the decisions is what cannot be satisfied by filler.
_RELEASE_PROSE_DECISIONS = (
    ("КАНАЛ ДВИГАЕТСЯ ТОЛЬКО ВПЕРЁД",
     "why the channel push carries no `-f` (#737). Without this, `-f` reads like an omission "
     "and the next editor restores it — which is the rollback the card was filed for"),
    ("fast-forward-only",
     "the MECHANISM behind the forward-only channel: git itself refuses, the check is the "
     "server's. Heading without mechanism is a slogan"),
    ("АТОМАРНОСТЬ — СВОЙСТВО СЕРВЕРА",
     "that `--atomic` is a dependency on receive-pack, not a client-side guarantee (#723)"),
    ("the receiving end does not support",
     "the MEASURED refusal proving that dependency fails safe — git pushes nothing rather than "
     "silently downgrading to a non-atomic push"),
    ("ПОЧЕМУ ДВА РЕФА В ОДНОМ PUSH'Е",
     "why bump and tag are one transaction and why `stable` is deliberately NOT a third refspec "
     "(#723/#737) — the paragraph that stops a well-meaning three-ref push"),
    ("ГЕЙТ ЧЕСТНОСТИ SKIP",
     "why being superseded is not by itself a reason to exit green (#740)"),
    ("ГЕЙТ ИМЕНИ ВЕРСИИ",
     "why the taken-tag check sits where it sits, wedged between two gates that both need it "
     "on their own side (#769)"),
)


def test_the_release_script_still_carries_the_decisions_only_its_comments_record():
    """The prose layer of `scripts/release.sh`, which no other pin reads — VMCP-217 (760).

    MUTATION-CHECKED, `__pycache__` deleted per round then `PYTHONDONTWRITEBYTECODE=1`, the whole
    release stand as the selection so the numbers are comparable with the round that motivated
    this, every round restored from a COPY with the restore confirmed by sha256 and every mutation
    asserted to have APPLIED. Control round: 0 failed.
      * delete every comment line in the script (627 of 772) -> 0 failed BEFORE this pin existed,
        which is the hole; with it, the same deletion is 1 failed and names the first decision
        that went missing
      * delete any ONE of the pinned decisions -> 1 failed, naming that decision and why it was
        written down. Checked for each of the seven rather than argued from the first
      * the literals are asserted to occur EXACTLY once, so a pin cannot be satisfied by a second
        copy left behind in a retraction — the failure mode #700 names and the sibling ref gate
        at the foot of test_repo_quotation_claims runs into from the other side
    """
    text = RELEASE_SH.read_text(encoding="utf-8")
    flat = " ".join(
        " ".join(line.strip().lstrip("#").strip() for line in text.splitlines()).split()
    )
    missing = [(needle, why) for needle, why in _RELEASE_PROSE_DECISIONS if needle not in flat]
    assert not missing, (
        "scripts/release.sh no longer records decisions that live NOWHERE else — not in the code, "
        f"which reads the same with or without them, and not in any other pin: {missing}. "
        "81% of that file is prose and this is the only thing that reads it; a measured round "
        "showed all 627 comment lines could be deleted with the whole release stand still green."
    )
    duplicated = [needle for needle, _ in _RELEASE_PROSE_DECISIONS if flat.count(needle) != 1]
    assert not duplicated, (
        f"these anchors no longer occur exactly once in scripts/release.sh: {duplicated}. Above "
        "one, this pin can no longer tell the live decision from a quotation of it — a second "
        "copy left behind by a retraction satisfies the check while the real paragraph is gone."
    )
