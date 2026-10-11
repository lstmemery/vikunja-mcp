# Delegated moves on the evidence release line

The evidence releases retain fork inventory, dependency, and WIP controls. Release
`evidence-v0.2.372` also restores the delegated move behavior carried by upstream
`v0.2.373` and the rewritten September 18 feature commits
`075eda90f08cb1c2956218a12e9edf6c3920d64c` and
`630984dc49f7804c564e44e1a7136f5c7c096385`.

An armed delegated server accepts `move-stage` with its destination in the
per-card authorization record's `stage` field. It supports moves into and out of
Done and Icebox. Unknown destinations and moves to the current stage refuse.
Every move touching Done writes its audit comment before changing the stage;
a failed audit write prevents the move.

`VIKUNJA_DELEGATION_AGENT_MARK_DONE=1` opts out of the agent-authored-card review
verdict guard for delegated moves to Done only. It defaults off. The server still
requires its separate delegated token, an unambiguous live per-card record, the
user's quoted instruction, and an audit comment. `mark-done` requires evidence in
the record. Records may authorize a window of at most seven days.

The installed `delegated_move(task_id, action, instruction, label?, evidence?)`
calling convention remains supported. Authorization and audit text come from the
record; supplied instruction and evidence arguments cannot replace that record.
Ordinary claim, review, and Done gates retain their existing policies.
