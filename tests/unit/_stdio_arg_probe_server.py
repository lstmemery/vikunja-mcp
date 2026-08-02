"""Probe server for test_advance_report_arguments.py — NOT a test module (the leading
underscore keeps pytest from collecting it); it is spawned as a SUBPROCESS.

It builds the REAL MCPServer with the REAL twelve tools and runs it over the REAL stdio
transport, replacing only the Workflow behind them with a stub that reports the length of
the argument that ARRIVED. That is the whole point: every other unit test in this repo
drives Workflow through FakeAPI and never crosses the MCP boundary, so nothing else here
can tell "the agent passed no worklog" from "the worklog was lost in serialisation" —
which is exactly the ambiguity tracker #657 was filed about.
"""
import sys

from vikunja_mcp import server as srv


class _ReportingWorkflow:
    """Answers with what reached the tool body instead of talking to Vikunja."""

    def advance(self, task_id, to, spec=None, worklog=None, evidence=None, root_cause=None):
        return {
            "task_id": task_id,
            "to": to,
            # -1 marks "arrived as None" so it is distinguishable from an empty string.
            "worklog_len": -1 if worklog is None else len(worklog),
            "spec_len": -1 if spec is None else len(spec),
            "evidence_len": -1 if evidence is None else len(evidence),
            "root_cause_len": -1 if root_cause is None else len(root_cause),
            "worklog_head": (worklog or "")[:24],
            "worklog_tail": (worklog or "")[-24:],
        }

    def review_task(self, task_id, verdict, report):
        return {
            "task_id": task_id,
            "verdict": verdict,
            "report_len": len(report),
            "report_head": report[:24],
            "report_tail": report[-24:],
        }


def main() -> None:
    srv._wf = lambda: _ReportingWorkflow()
    srv._server().run()


if __name__ == "__main__":
    sys.exit(main())
