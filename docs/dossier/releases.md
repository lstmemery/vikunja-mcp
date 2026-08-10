# Канал stable — авторелиз, атомарный пуш, гонки джобов

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Релизы` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

**Patch releases are automatic** during active development. Every green push
to `main` fires the `release` job in `.github/workflows/ci.yml`
(`needs: [lint-and-unit, integration]`): it runs `scripts/bump_version.py`
(bumps the patch in ALL THREE version files — `pyproject.toml`,
`src/vikunja_mcp/__init__.py` and `uv.lock`'s self-entry; the lock is easy to
forget and it is a *dependency-resolution* file, so "version-only" does not mean
"touches nothing that matters"), commits `chore: vX.Y.Z [skip ci]`, tags
`vX.Y.Z`, and moves `stable` onto that bump commit — the commit/tag/push
half lives in `scripts/release.sh`, in a FILE rather than a `run: |` block
precisely so it can be RUN on a stand instead of reasoned about. The job holds
`permissions: contents: write` (least-privilege, that job only) and a `release`
concurrency group, which serializes the release JOBS **and nothing else**: it
does not move a queued job onto a newer base, so both jobs of two close landings
still compute the same next patch (see the tip guard below).

**The channel moves FORWARD ONLY, and that is a property of the script now, not of
ci.yml** (tracker #737). Until then the last action was an unconditional `git push
-f … stable`, and the window between a job DECIDING to release and that push was
closed by the concurrency group alone: a stand that runs a sibling's whole release
inside the window rolled `stable` BACK onto the earlier bump — job GREEN, both tags
present, channel a patch behind its own newest tag — identically on the guarded
path, the ordinary path and the pre-guard inline block, i.e. the property was
PRE-EXISTING rather than introduced by #716. The fix is the missing `-f`: a plain
push is fast-forward-only, so git itself refuses to point `stable` at a commit that
does not contain the channel's current head, and the refusal is then GRADED by the
same two questions the `main` push already asks — channel equals my bump (it landed,
the client lied) or channel CONTAINS my bump (a newer release already carried me):
green with a notice; **anything else, including "the channel could not be read", is
RED**. Both green branches need a proof that the channel carries my code, so
"channel not moved AND my code not in it" is green on no branch OF THIS STEP. **Wider
than this step that sentence is FALSE, and an attack pass built the counterexample
three ways**: the PRE-TAG gate still returns a green skip with the channel unmoved —
that is its documented swallowings. #740 narrowed that list without emptying it, and the
sentence survives: the most ordinary example used to be a runner killed between
pushes followed by a `gh run rerun`, and #740 reddens exactly that one — but (1) and (3)
below still build the counterexample outright, and so does (4)'s own residue, an orphan
bump with no tag on it. This fix removes the channel ROLLBACK, not
the whole class "green job, channel behind" (#723, #740), which it neither introduced
nor closes. A BARE `--force-with-lease` (no argument — it leases against
`refs/remotes/origin/stable`) was measured and rejected, and the FIRST of those
measurements runs the other way, so it goes first. **That ref IS created on the
runner**: read out of the `Run actions/checkout@v4` step of the very job that does the
pushing (job 91562691267 of run 30772730104 — the release of `ad41397`, this card's
first landing), where `fetch-depth: 0` makes checkout run `git -c protocol.version=2
fetch --prune --no-recurse-submodules origin +refs/heads/*:refs/remotes/origin/*
+refs/tags/*:refs/tags/*` and print `* [new branch] stable -> origin/stable`. Not one
run: the same line is in job 91515383221 of run 30754732335, which covers the SECOND
configuration, tip ≠ trigger sha — `origin/main` was `dff2def0` at fetch time while the
job's TRIGGER sha was `0664256f` (that job released nothing at all: it died on `fatal:
tag 'v0.2.171' already exists` before its first push — it is #716's own case), so
checkout runs a SECOND, targeted fetch, `git … fetch --no-tags --prune
--no-recurse-submodules origin +0664256f…:refs/remotes/origin/main`, which force-updates
`origin/main` ALONE and leaves the `origin/stable` the first fetch created — `--prune`
deleted no ref in that job (zero `[deleted]` lines). So on the runner the lease would
have a value and would have refused this very race, and the sentence that used to stand
here — "whether checkout creates that ref was NOT measured" — is false as of those runs.
Honest bound on the second witness: it adds a second CONFIGURATION, not a second action
VERSION — both runs pulled the same `actions/checkout@v4`,
`SHA:11d5960a326750d5838078e36cf38b85af677262`, on the same day, and versions are exactly
what the next sentence is about. It is still rejected, for two reasons the fact does not
touch. First, the refspec is the ACTION's, not ours, and `ci.yml` subscribes to the
FLOATING `@v4`: it can change with any move of that tag, and the one way to pin it —
nailing the action to a sha — is not what this repo does. The cost of it changing is not
"a race slips through" but "EVERY release is red": WITHOUT the ref a bare lease rejects
even a perfectly normal push, with no race in sight (`! [rejected] … (stale info)`, rc=1
on the stand). Second, WITH the ref a plain `git fetch origin` before the push resets the
lease and lets the rollback through (measured, rc=0), because a fetch updates exactly the
value the lease compares; today no such fetch precedes the channel push in `release.sh`
(the two earlier ones both read `refs/heads/main`, and the only `refs/heads/stable` read
sits AFTER the refusal), but that is a property of the current text, not a guarantee. The
EXPLICIT form (`--force-with-lease=refs/heads/stable:<sha>`) depends on no tracking ref
and neither reason reaches it; it is rejected by an argument rather than a stand — a lease
asks "has anything changed since I looked?" where the property wanted is "does the new
value contain the current one?", and those coincide only if you looked BEFORE the sibling.
Look after it and you lease an already-advanced channel, the lease agrees, and the force
performs exactly the rollback. A plain push asks the wanted question always: the comparison
is the server's, and the stand gives the same refusal with and without the tracking ref.
What this does NOT fix, measured on the same stand: a HUMAN's documented rollback
(`git branch -f stable vX.Y.Z && git push -f origin stable`) performed inside a live
release job's window is still silently undone, because the old tag is an ANCESTOR of
the new bump, so the job's push is an honest fast-forward. The same measurement is
what shows the rollback procedure itself still works — the release after a rollback
moves the channel forward again, rc=0. The group survives as defence in depth and is
now PINNED by a test, because after this fix its removal has no reliable
symptom: the common outcomes of the races it prevents are GREEN (the supersession
skip, and this section's notice). Not "never red" — the branch where my bump is on
`main` with something newer on top is red, and dropping the group makes exactly that
more frequent too; what is gone is any DEPENDABLE signal, which is what a pin is for.
The same fix has one named cost: a channel pointed by HAND at a commit off `main` is
no longer silently overwritten, it reddens every release until a human fixes it. The bump commit is
pushed with `GITHUB_TOKEN`, which by design does NOT re-trigger CI (plus
`[skip ci]` as a second belt). So `stable` always tracks the latest green `main`,
patch-bumped, hands-off.

**The bump and its tag are ONE server transaction, and the flag is what makes that
true** (tracker #723). They used to be two pushes in a row, so a refusal on the
SECOND (network, 5xx, ref protection) left the bump on `main` with no tag — measured
on the stand with a hook refusing only `refs/tags/*`: `tags = []` under
`__version__ = "0.2.171"` at the tip, exit 1. That state does not heal. A re-run of
the same job reads its own orphaned bump as "superseded" and exits GREEN having fixed
nothing, and the next landing bumps to the patch AFTER it, so the skipped version
never exists and `git branch -f stable vX.Y.Z` can never name it again. Both halves
are now one `git push --atomic origin HEAD:refs/heads/main refs/tags/vX.Y.Z:…`, and
the same input leaves the remote untouched. **"One command" and `--atomic` are not
the same guard, and which one does the work depends on the SHAPE of the server's
refusal** — measured on both hook kinds. A `pre-receive` is one hook per PUSH, so
exiting non-zero refuses the whole batch with or without the flag, and bundling alone
covers that input. An `update` hook is per REF — the shape a host's ref-protection
takes — and a non-atomic batch then takes what it can, in BOTH directions. Refuse the
TAG: `HEAD -> main` lands beside `! [remote rejected] v1 -> v1 (hook declined)`, i.e.
this card's own "bump without tag" is reachable from a SINGLE command too, and only
the flag stops it (same input, with it: `! [remote rejected] HEAD -> main (atomic push
failure)`, `main` untouched). So "bump without tag needs two separate pushes" would be
FALSE — it holds only for a whole-push refusal. **That input also came within one
measurement of being SILENT, and closing it added the second layer**: run whole, the
client reported failure, the recheck asked its first question, saw its own bump on
`main`, printed `finishing the release`, moved the channel and exited **0** — bump and
channel present, tag absent, job green, where even the pre-#723 shape went red under
`set -eu`. So the recheck's one GREEN branch now also asks the remote where the tag is,
instead of trusting that `--atomic` was honoured; the same input is red today
(`the push was accepted NON-atomically`). Two layers, and they are not the same thing:
`--atomic` stops the state from EXISTING, the tag check stops it from passing QUIETLY.
An attack pass built the case that needs the second — a server that ADVERTISES atomic,
takes the branch, drops the tag and lies — and before the check it went green with a
tagless release, a hole the #723 fix would itself have opened by removing the separate
tag push that used to re-establish it. Refuse `main` instead (non-ff while the
tag name is free) and the non-atomic push
creates the tag and rejects `main`. That orphan tag is strictly worse than the hole
it replaces: the version at `main`'s tip never advanced, so every later job computes
the SAME version and dies on `fatal: tag … already exists` — two consecutive
landings run, both rc 128 (the pin keeps one), and the mechanism says every later one
is the same — and the job that
created it is GREEN, because the recheck honestly sees a supersession. **Atomicity is
a SERVER capability, not a client flag**, and that dependency fails safe rather than
silently: with `receive.advertiseAtomic=false` git refuses to push anything at all
(`fatal: the receiving end does not support --atomic push`, rc 128, remote clean),
which lands in the recheck's red branch — there is no quiet downgrade to a
non-atomic push. GitHub advertises it, read two ways: the capability line at
`https://github.com/<repo>.git/info/refs?service=git-receive-pack` (the same HTTPS
`actions/checkout` pushes over) and a live `git push --atomic --dry-run` that cleared
the client-side capability check. That second read went over SSH, so together they say
"advertised on HTTPS" and "the client accepts it" — NOT "a full atomic push over HTTPS
was exercised", which no stand for this repo can do. **`stable`
is deliberately NOT in that bundle.** A third refspec is syntactically fine (same
remote, no force anywhere since #737), but with the channel pointed by HAND off
`main` — #737's named cost — a three-ref atomic push refuses EVERYTHING: `main` stays
put and no tag is cut, so one channel anomaly would freeze versions entirely and on
every following release. The residual half-state it buys instead — bump and tag
landed, channel not moved — heals only in ONE of its two forms, and the two are worth
keeping apart. When the channel is merely BEHIND (absent, or an ancestor of my bump)
the next landing catches it up: re-measured on both forms, the next job exits 0, `git
merge-base --is-ancestor <my bump> stable` returns 0 and BOTH tags are on the remote,
so no version is skipped. When the channel was pointed by HAND off `main`, nothing
heals it — ff-only refuses the next release too, and every one after that, until a
human fixes it; that is #737's named cost, not a new one. Either way the refusal is
loud and carries the fix command.

**Atomicity also removed an ACCIDENTAL ESCAPE, and that trade is deliberate rather than
overlooked.** If a version tag name is squatted on the remote by a foreign tag that appears
AFTER the job's checkout — so the job's own clone does not have it and `git tag -a` succeeds
locally — the separate pushes used to land the bump anyway, which advanced the version at
`main`'s tip past the squatter, so the next landing computed the NEXT patch and the wedge
healed after one red job. Measured on the stand, pre-#723 code: first job rc 1, then three
consecutive landings all rc 0, version reaching 0.2.174, tags v0.2.171..174, channel moved.
With the atomic push NOTHING lands, so the tip's version never advances, every later job
computes the SAME taken version and dies at `git tag -a`: the same three landings give rc
128, 128, 128, the version stays at 0.2.170 and the channel never moves again. The invariant
holds — every one of those runs is RED, there is no silent green here — but "one red, then it
heals itself" became "the channel stands until a human deletes the foreign tag". The trade
was taken knowingly: the half-state it replaces was QUIET and unrecoverable (the skipped
version never exists), while this is loud and one human command away (#769).

**#769 KEPT that trade and spent itself on LEGIBILITY instead — read it as a diagnosis, not a
cure.** A gate before `git tag -a` (and strictly AFTER the supersession gate, or it would redden
#716's lawful green skip) asks origin whether the version name is already taken, and — when origin
says free OR cannot be asked — the job's OWN checkout. That second source is not decoration:
`actions/checkout` pulls the
tags as of checkout time, so from the second landing on the name is already local, which is
exactly what makes `git tag -a` die 128 before the script can say anything. Taken either way, the
job refuses NAMING the tag, the object under it, the fact that nothing was pushed, the mechanism
(the version at the tip has not moved, so every later job computes the same name) and the one
command that clears it. **Its price is ZERO because it changes no verdict — by an EXHAUSTIVE SPLIT
rather than by enumerating states, which is the honest way to read it:** if the tag is already in
the checkout, `git tag -a` dies 128 before either push; if it is not, the atomic push must set
`refs/tags/V` to a NEW object, an un-forced push of that ref is refused, and under `--atomic`
nothing lands at all. There is no third case, and both halves are measured on the stand. What it
buys is that the FIRST red stops naming the wrong thing: that
job, whose checkout predates the squatter, used to reach the push and land in the generic
no-newer-landing branch, which reads like branch protection rather than a name collision. It
covers the orphan-tag flavour above too, which wedges identically. **Recomputing the version onto
the next free name was REJECTED, not overlooked**, and the reason is sharper than the general "no
version is skipped" line: here the skipped name stays NAMEABLE and points at a FOREIGN commit, so
the documented rollback would put the consumer channel on that commit while formally satisfying
the tagged-and-CI-green rule — a booby trap where today there is none — and routing around it
would silence the only signal that someone has put a foreign tag in this repo's version
namespace. Whether to skip a number anyway is a card for a human, not a default. One asymmetry to
keep straight: this read and `skip_or_refuse`'s tag-list read FAIL IN OPPOSITE DIRECTIONS on
purpose — that one guards a GREEN skip, so an unanswerable read must be red; this one is
diagnostics over a branch that is red at either answer, so an unanswerable read must NOT redden a
healthy release, and a fail-closed variant of it is the mutation that pins that polarity.

What this does not
touch: the pre-tag gate's swallows — #740 later reddened (2) and (4) in the shape where
the tip's bump carries its tag, and left the rest —
and the local `git branch -f stable
HEAD`, which is not a push at all — no remote sees it, and it can only fail locally
(the stand got it two ways: an unwritable ref, and `stable` checked out in some
worktree, which git refuses before writing anything — rc 128, loud under `set -eu`).

**The release belongs to the TIP of `main`; a superseded landing skips, green**
(tracker #716). `actions/checkout@v4` holds each job at its OWN trigger sha, so
two landings close enough together leave BOTH checkouts on the same version base
and both compute the same next patch. Measured on 2026-08-02, all times UTC that
day: run 30754732335 on `0664256f`
died on `fatal: tag 'v0.2.171' already exists`, and the tag's actual owner was
the run for `75a1e520` (`git rev-list -n1 v0.2.171` → `dff2def0`, whose parent is
`75a1e520`) — a run created 3 m 13 s LATER, 15:39:41Z against 15:36:28Z, which
released FIRST. Release order follows when each run's `needs` finish, not when
the run was created: the loser's `integration` job sat unstarted for five minutes
(started 15:41:31Z). And the concurrency group had nothing to serialize here —
the two `release` jobs never overlapped at all, the winner's running
15:40:34–15:40:44Z and the loser's 15:41:58–15:42:04Z, which is why the loser saw
the tag already on the remote at checkout. So `scripts/release.sh` asks, before
`git tag` and again after that rejected push (`git push --atomic origin
HEAD:refs/heads/main refs/tags/vX.Y.Z:…` since #723): is `main`'s tip
a DIFFERENT commit that CONTAINS `$GITHUB_SHA`? If yes, a newer landing is already
on top — **but since #740 that answer alone no longer buys a green exit**, because the
thing on top can be the CORPSE of a job that died mid-release. The skip now needs a
positive proof that the supersession is benign, and there are exactly two, either of
which will do, both read from the REMOTE: `stable` already contains `$GITHUB_SHA` (the
release that carried me reached consumers), or NO version tag points at the tip (nobody
has released the tip yet, so my skip is evidence of nothing). Neither one holding is
exit 1. **"Could not ask" is fail-closed for ONE of the two reads only — do not read it
as "any read"**: the proofs are tried in order, so an unreadable — or simply
absent — channel decides nothing by itself, it merely yields no P1, and P2 still answers.
An absent `stable` is the ordinary state of a young repo and still skips green. A broken
TAG list is different: its empty answer is indistinguishable from an honest "no tags", so
that is where the refusal sits (`test_an_unanswerable_tag_read_is_never_a_skip`).
**The red cannot freeze anything, and the reason is the
PLACE rather than the wording**: the branch it sits on pushes NOTHING, so no ref on the
remote moves whether the job goes green or red — tip, tags and channel are asserted
unchanged on the red side in
`test_a_foreign_orphan_bump_no_longer_swallows_an_earlier_landing` and on the green side
by its mutational neighbour, so it takes the PAIR to pin it. That is
what separates it from the reds the two neighbouring cards bought, and those two are not
the same as each other either: #723's foreign-tag squatter stops VERSIONS (every later
job dies at `git tag -a`), while #737's hand-pointed channel keeps cutting versions and
stops the CHANNEL. What this one costs is attention. Its PRECISION, not its safety, leans
on `concurrency: release`: with the group gone the red could fire on a tip whose own job
is still between its two pushes — and it would still cost attention and nothing else.
Only with a proof does the job print a notice and exit 0 — and the notice says only that,
never who will release the tip. Round 1's notice promised "releasing it is that
newer tip's job", which was false in THREE of the four swallows below: in two of them
the tip is a bump commit, and bump commits get no runs at all — by construction
(`GITHUB_TOKEN` does not re-trigger CI, plus the ci-skip marker) and re-measured on 60
consecutive bump shas, every one of which returns `[]` from `gh run list --commit
<full sha>`; in the third the tip has a run and that run releases nothing. Those first
two are the ones #740 closed, and only in the shape where the tip's bump CARRIES ITS TAG
— which is the only shape this script can leave while the server honours `atomic`, since
the tag is what fails the second proof. The tail is still not knowledge: it stays false
in (3), and false again on an orphan bump with NO tag, which the gate lets past honestly.
Do not put it back.

**After a rejected push that question is asked SECOND, and the order is
load-bearing** — the same order this file already prescribes to agents above
("First *did it land anyway?*"). Round 1 of #716 asked only the second question,
and lost whole releases to exactly the failure that rule exists for: a server can
take the ref update and still leave the client reporting failure, and then the tip
that "supersedes" the job is its OWN landed bump — a different commit that contains
`$GITHUB_SHA`, a perfect match for the condition. The job went GREEN having cut no
tag and moved no `stable`, with no second actor, no human and no re-run involved.
Constructed on the stand with a shim that performs the push and then reports the
hangup: round 1 gave `rc=0 tags=[] stable=none`, round 2 gives `rc=0 tag=v0.2.171
stable=<the bump>`. So the recheck now asks in three steps. My HEAD IS the tip →
the push landed, and since #723 the tag landed WITH it, so all that is left is
`stable`. My HEAD is ON `main` but something NEWER sits on top → LOUD, exit 1 —
and the REASON has been rewritten twice, each time because it went false. #737
killed the first ("a force-push would roll the channel back": there is no `-f` on
the channel any more), #723 killed the second ("the tag never reached the remote":
under `--atomic` it did, and the log line that said `tag … NOT pushed` was lying).
What survives is the repo's standing rule — sound beats silence: the channel is
unmoved, so the release is not fully assembled. Finishing it from there (while the channel has not passed my bump, fast-forwarding
`stable` onto it is a legal FORWARD move even with a newer tip on `main`; once it has,
the channel's own gradation says so) is deliberately NOT done: it would turn red into green on a state where
the script cannot know who will release the tip, which is the very prediction the
skip notice refuses to make. Only then, my HEAD is not on
`main` at all → the supersession question, whose "no" is exit 1.

Everything else proceeds exactly as it did before the guard — with qualifications
that sentence must not be read to cover, all checked rather than assumed. A `main`
force-pushed BACKWARDS is not superseded at all: the rollback tip is an ANCESTOR of
`$GITHUB_SHA`, so the bump is a fast-forward and the job pushes straight over the
rollback — measured on the stand, byte-for-byte the same outcome with the guard and
without it, so this is pre-existing behaviour rather than anything this card
introduced or fixed. And the guard's own cost was FOUR swallows, a number RECOUNTED at
every round rather than inherited: round 1 said two, round 2 fixed one (the landed push
above) and its second pass built two more, and the rework's own second pass then
built a fourth — which also DISPROVED the sentence (2) used to close on. #740 then
closed (2) and (4) with ONE gate — **but "closed" is about a SHAPE, not a class, so do
not restate the cost as a single smaller number**: what is closed is the tip-bump that
CARRIES ITS TAG, the only shape this script itself can leave; an orphan bump with no tag
(a server that lied about `atomic`, a human deleting the tag) still swallows, green, and
is pinned as the residue. (1) and (3) stay open outright. All four
are constructed, they do not sit at the same gate, and the two closed ones stay
enumerated below with their construction: what keeps them closed is a pin, not this
prose, and the next edit here has to be able to see what is load-bearing.

**(1) The post-push recheck** still never asks WHY git refused, so a non-race
refusal (permissions, branch protection) that COINCIDES with a sibling landing exits
green where it used to be red. The mitigation is real but weaker than round 1's "the
TIP has no sibling by definition": measured with a standing push denial and a
landing inside every job's window, a series of five gave FOUR green swallows and ONE
red — the last. So the surviving signal is one red per SERIES, not one per job. #740's
gate sits on this branch too (the recheck calls the same helper), and it does NOT close
this class — it asks about the TIP's release state, never about why git refused. **The
overlap is real, though, and must not be denied**: when the non-race refusal coincides
with a landing that is itself a half-release (bump + tag, channel behind), the job now
reddens. That is the tip's shape doing the work, not any new insight into git's refusal;
coincide with a plain task commit and it is a green skip exactly as before. What pins the
recheck's routing is a MUTATION, not a green neighbour: strip the routing and
`test_without_the_landed_question_the_release_is_lost` fails (control 0 failed; that
round 1 failed), while `test_lost_race_after_the_pre_push_check_still_skips` traverses
the branch and stays green, so it proves nothing about the routing.
**(2) CLOSED by #740 in the "bump WITH its tag" shape. The pre-tag gate, as a CLASS**:
any half-assembled state plus a
RE-RUN used to be a green skip. A job that left its own bump as the tip — killed
between the atomic push and the channel push, or between that and the local
`git branch -f`, which can fail too — re-runs, read the tip as "superseded" by its own
orphan, and went green. THAT job's first run is loud and only a hand `gh run rerun`
silenced it — but the qualifier rests on the tip being the job's OWN bump, and must not
be read as "a half-state is always loud first": a DIFFERENT landing under the same
half-state was swallowed on its first run, which is (4). Round
1 described the class narrower than it is ("without ever cutting the tag"), and #723
narrowed the class itself rather than the description: with bump and tag indivisible,
the ONLY shape this script can leave is "bump AND tag on the remote, `stable` not
moved" — "bump without tag" is no longer constructible from it, as long as the server
honours `atomic`. That single shape is precisely what fails BOTH of #740's proofs, which
is why one gate closed (2) and (4) together; the re-run is now red
(`test_a_rerun_over_its_own_orphan_bump_is_no_longer_green`). Take the tag away — a
server that lied about `atomic`, a human deleting it — and the re-run is green again
(`test_an_untagged_orphan_bump_still_swallows`). It still FIXES nothing —
the channel is as behind as it was — but it stops erasing the one red the state had,
since `gh run rerun` rewrites the conclusion of the same run in place.
The landed question does not rescue a re-run, and should not — a re-run commits its
OWN bump (fresh committer date, different sha), so "did MY push land?" is honestly
no. Before the #716 guard the re-run was red for a different reason
(`fatal: tag … already exists`), which fixed nothing but was visible. Class tracked
as #723.
**(3) The pre-tag gate again**: the tip that supersedes me will not release EITHER —
its run is red, its ci-skip marker was swallowed, or its job was cancelled. The skip
is then literally true and still leaves `stable` where it was. This is the stand's
CASE C, the "tag name still free" row of the card's own table: the same input was
`! [rejected] … (non-fast-forward)`, exit 1, before the guard. The script cannot
check it from here, which is exactly why the notice promises nothing — and it is not
rare: seven of fifteen consecutive `main` runs were red on the night of 31.07. #740
leaves this one open ON PURPOSE, and it is the reason the gate needed two proofs rather
than one: here the tip carries no tag, so "nobody has released the tip yet" is TRUE, and
demanding the channel as well would redden the ordinary race where the tip's own job
simply has not run yet. The green input is
`test_superseded_with_the_tag_still_free_also_skips`.
**(4) CLOSED by #740 in the "bump WITH its tag" shape. The pre-tag gate a third time,
and it was neither (2) nor (3)**:
the tip is ANOTHER job's orphaned bump, and what got swallowed is a DIFFERENT, EARLIER
landing on its FIRST and only run — no re-run, no second actor inside its own path.
Unlike (2), the hangup happened in someone else's run, so "the first run is loud" never
applies; unlike (3), the tip is a BUMP commit, which gets no run at all (re-measured:
60 of 60 consecutive bump shas return `[]`), so "its run is red" is not even a
question that can be asked — the job that owed that tip a release already ran and
died. One hangup swallowed as many landings as it buried: two, measured on three
commits. And this one the guard INTRODUCED rather than inherited — on identical
input the pre-716 inline gives `! [rejected] … (non-fast-forward)` and exit 1, round
1 gives rc=0 and round 2 still gives rc=0; today the same input is rc=1 with every ref
on the remote untouched. Pinned now by its inverse,
`test_a_foreign_orphan_bump_no_longer_swallows_an_earlier_landing`, whose mutational
neighbour brings the swallow back verbatim. #723
had rebuilt that pin's CONSTRUCTION without touching the property: the orphan used to be
made by refusing the TAG push, which no longer leaves a bump behind at all, so it is
now made by refusing the CHANNEL push — and the routes to a foreign orphaned bump are
still several (a refused or unreadable channel, a failed local `git branch -f`, a runner
killed between the two pushes), which is why the fix had to grade the STATE rather than
enumerate the routes. **What "closed" does NOT mean**, two things, both measured. The
channel is still behind at that moment and the red only says so out loud — "the next
landing carries the channel past it" holds when the channel is merely BEHIND, which the
pin runs rather than promises, and NOT when something is still refusing the channel push,
where the next landing hits the same refusal. And the closure is over the shape with a
TAG on the tip: strip the tag and the same input swallows green again
(`test_an_untagged_orphan_bump_still_swallows`, which also records why the residue was
named rather than guessed at — every discriminator left is the tip's FORM, and both
candidates for that were measured and rejected).

What that does NOT change is what reaches consumers ON THE SUPERSEDED PATH — and
the scope of that sentence matters, because on the landed-but-reported-failure path
above the fix deliberately pushes MORE than the pre-fix step did. Measured on a
bare-repo stand, the pre-fix superseded job dies at `git tag` when the sibling
already took the name and at `! [rejected] HEAD -> main (non-fast-forward)` when it
did not — and `git tag` sits BEFORE every push, so in both cases the remote's
`main`, tags and `stable` are unchanged before and after; there, and only there,
just the job's CONCLUSION moves, from a false red to a green no-op. Two readings
follow. N rapid landings can share ONE patch bump (already true before the fix),
and a green `release` job therefore no longer implies a new tag exists — the log
line `release skipped: …` is what tells the two apart. And what the guard
guarantees for the LAST landing of a session is that it is never SUPERSEDED, NOT
that it releases: nothing lands after it and an earlier job's bump can only be
pushed onto ITS OWN sha, so it is still the tip when its job runs — but that job
can fail to start at all (a swallowed ci-skip marker), go red, or be killed
mid-way, and each of those leaves the last landing unreleased with nothing later to
heal it. "Always DOES release" was the overclaim; "is never superseded" is the
measured part.

Two directions that look equivalent here and are not, both refuted by measurement
rather than argument. Recomputing the VERSION — from `origin/main`, from a retry,
or by treating a taken tag as "recompute" — fixes only the NAME: the bump still
sits on top of a non-tip, so the job dies at the main push instead, which the
stand reproduces as that same `non-fast-forward` rejection. And
`git describe --tags --abbrev=0` does not even fix the name: from `0664256f` the
nearest REACHABLE tag is `v0.2.170`, so it computes the same, taken `0.2.171`.
The stand is `tests/unit/test_release_script.py` — a real bare repo, real clones,
real pushes, and a `pre-receive` hook to land a sibling mid-push — because the
failure it guards against is a race between two jobs, and neither a fake nor a
reading of the diff can produce one.

**That bump commit is also a racer, and sizing the drain's retry loop is its
job.** Because it lands 37 s–2 m 55 s after the task commit that triggered it
(median 1 m 41 s; on 2026-07-30 **17 of the 46 commits that reached `main` were
this bot's**), a per-task agent's freshly-completed rebase goes stale within
about two minutes of *any* landing — so under a parallel drain a rejected `git
push origin HEAD:main` is the expected outcome, not an anomaly. The
`GITHUB_TOKEN`/`[skip ci]` property above is what BOUNDS it: the release never
triggers itself, so it never pushes twice in a row and can cost an agent at most
one round. That bound is what sizes SKILL.md's integration ceiling, and the
ceiling is a FORMULA, not a constant. Two steps, and the second is the one that
kept getting dropped: the worst purely MECHANICAL run at N racing agents is
2·(N−1) + 1 rounds — **5** at the default 3 — and the ceiling must sit STRICTLY
ABOVE that (otherwise it fires on arithmetic), i.e. one more round. So the
ceiling is **`2 × wip_limit`**: 2 at limit 1, **6** at this repo's default 3, 8
at 4, 10 at 5. **N is how many tasks are ACTUALLY in Design/Build — `wip.active`,
not the limit — and reading it off the limit fires the ceiling on exactly the
arithmetic it exists to prevent** (tracker #939): rework re-enters Build past the
`claim` gate, so `wip.active` legitimately EXCEEDS `wip_limit` (measured on this
board: 5–7 against a limit of 3, where card 851 spent all 6 rounds on pure
mechanics with green gates and no rebase conflict, then parked finished pushed
work in Your Call). So the operative formula is `2 × max(wip_limit, wip.active)`,
the `max` being what keeps the ceiling from DROPPING below the table above when
fewer tasks are in flight than the limit allows; the table itself is unchanged,
being a function of N. The worst run and the ceiling are DIFFERENT numbers — quoting the
first where the second belongs is what card 556 caught in this very paragraph.
The rulebook self-heals onto every consumer and `wip_limit` is per-project, so a
pinned constant would call a human onto pure arithmetic in any project running a
wider drain (card 550) — and an agent whose brief omits the limit does not guess
it either: `wip_limit` is repo-toml-only, the toml is committed and therefore
present even in a linked worktree, so it READS it, and the bare 6 survives only
for "there is no toml at all", which is exactly the state that means the default
(card 559). And the count is only the budget: what decides whether a round was
owed at all is asked in two steps, in this order. First *did it land anyway?* — a
server can take the ref update and still leave the client reporting failure, so
`git merge-base --is-ancestor HEAD origin/main` (after a fetch) comes first, and
exit 0 means the work is already on `main`: verify the sha and move on, never
wake anyone. Only exit 1 reaches the second question, *what won the race* (`git
log --oneline HEAD..origin/main` — empty means it was never a race, so retrying
is futile and the agent escalates without spending the budget). That order is
load-bearing rather than tidy: a landed push with a sibling on top shows a
NON-empty range, and the retry it invites rebases the already-upstream commit
away, after which `git rev-parse HEAD` names the SIBLING's commit as evidence and
both landing checks pass on it. See "Откуда потолок" there.

**Never let the literal ci-skip marker into a commit MESSAGE — quoting counts.**
Writing *about* the release is the trap: the marker is matched anywhere in the
message, body and code spans included, so a commit that merely quotes the bump
commit's subject cancels its own CI run — and does so silently. It is a family,
not one spelling: GitHub also honours `[ci skip]`, `[no ci]`, `[skip actions]`,
`[actions skip]` and a `skip-checks: true` trailer. The push
succeeds, both evidence-sha checks pass, and the task looks landed, but there is
no run, no auto-release, and the change never reaches `stable`, i.e. never
reaches consumers. Name the marker descriptively in messages (in a *file* the
literal is harmless), and after pushing confirm a run actually EXISTS for your
sha (`gh run list --commit "$(git rev-parse HEAD)"` — the FULL 40-char sha; an
abbreviated one returns `[]` and exit 0, which reads exactly like "no run" and
raises a false marker alarm) — "no run" and "green run" look identical from git.

**"No run" has a SECOND cause, and reading it as the marker is a false diagnosis
(tracker #937): GitHub creates one run per PUSH, attached to that push's TIP, so a
commit that arrives NON-TIP inside a multi-commit push gets no run and no
check-suite at all — while the work itself lands and reaches consumers.** Measured
here: `bc960b2` returns `[]` on the full sha and `check-suites` `total_count: 0`,
carries none of the six marker spellings, and is an ancestor of `origin/stable`,
while its child `b6c7502` has green run 31086601577 — 1 of the 21 task commits in
the last 40 landings arrived that way (~5%, and a wider drain makes it likelier,
not rarer). So one step goes in front of the alarm: ask whether a DESCENDANT on
`main` has a run — `git log --oneline <full sha>..origin/main`, then `gh run list
--commit <that full sha>` — and raise the marker alarm only when nothing is above
you, or the descendant has no run either. What that step does NOT buy back is the
gate: the tree AT your commit was never linted or tested, only the merged thread of
the next push was, so `git bisect` and a rollback onto it are not covered by the
neighbour's green. Why the push carried two task commits at all was NOT established
and is deliberately not guessed at here.

**And build the message with `git commit -F - <<'MSG'`, never `-m "…"`** (tracker #773).
Same family, same silence: inside double quotes a backtick is command substitution, and the
house style here wraps every identifier in backticks — so the more faithfully an agent follows
it, the likelier the shell eats part of the message. Measured on a live shell:
`-m "keeps \`blocked\` and \`epic\` and $HOME"` landed as `keeps  and  and /Users/…`; escaping
each backtick works but has to be done by hand every time; a heredoc with an UNQUOTED delimiter
substitutes just the same (`\`echo GONE\`` really runs); only the quoted `<<'MSG'` is verbatim,
`$HOME` and `$(date)` included. The quoting of the delimiter is load-bearing, not cosmetic. The
loss is not only omission — `$(…)` INSERTS foreign output into the message — which is also why
"count the backticks afterwards" is not a usable check: it asks the author to remember the text
they just lost, and it cannot see an insertion at all. Found on this repo's own `5389be0`, where
three words vanished from the body; the history was not rewritten, because a force-push to main
for a message is not worth it.

**A run that EXISTS is not a run that PASSED, and that gap silently cost seven
landings in one night** (tracker #614). Measured 2026-07-31 on this repo: 7 of 15
consecutive runs on `main` were red, every one of them `lint-and-unit` success +
`integration` failure + `release` **skipped**, so `stable` never moved — while
every agent had truthfully reported "a run exists". Seven is a FLOOR: that window
ended on its own last red, and the same night held at least one more (`d6195e1`).
The count is also read-at-the-time — `gh run list` reports a run's CURRENT verdict
and `gh run rerun` rewrites it in place, so `8b4bfa5`, one of the seven, reads
`success` today. The two checks are two because their DEADLINES differ: existence
asks about a fact that does not ripen — the run is created or it never will be —
while the outcome does. (How fast GitHub *creates* the run was NOT measured here,
so the rulebook says to ask a second time before raising the marker alarm on a
push that is seconds old, rather than assert a number it does not have.) Measured
over 40 runs timed on their FIRST attempt (two were later re-run by hand, and a
re-run's `updatedAt` carries the HUMAN's delay — 31 min and 3 h 26 min — not CI's;
the runner queue itself was 0 s on 35 of 38 and never above 80 s), a run concludes
42–120 s after it appears, median 60 s. So the outcome is read ONCE and LAST —
after `advance(to='review')` and `workspace --release`, which cost about that long
anyway — and never by waiting: `gh run view <id> --json status,conclusion,jobs`,
branching on `status` FIRST, because `conclusion` is meaningful only at
`status == "completed"` — an in-flight run renders it as the EMPTY STRING (caught
live: `{"conclusion":"","status":"in_progress"}`), which is not `null`, so a jq
`// "unknown"` fallback does not fire either. A still-running run is therefore
reported as UNKNOWN, never as green, and the card's independent reviewer is the
backstop — late by construction. The bias helps but does not SEPARATE: red runs
are 42–55 s (median 46) against 53–120 s for green (median 65), so the bands
overlap at 53–55 s. And the reason is not that `integration` fails early — per-job
timing says it is never the critical path (16–29 s against `lint-and-unit`'s
38–46 s); a run's length is set by `lint-and-unit`, and a GREEN run additionally
runs `release` (8–15 s), which a red one skips. Urgency is bounded but not zero: a later green
landing moves `stable` with the red commit already included (verified — red
`8fc53f8` is an ancestor of today's `stable`; that night the catch-up took
1–48 min), so what actually costs is the LAST landing of a session, which nothing
later heals and nobody can identify in advance.

Manual procedure remains for:
