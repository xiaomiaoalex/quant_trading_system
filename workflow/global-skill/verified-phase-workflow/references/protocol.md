# Protocol and evidence contracts

Use these rules when the shared entrypoint requires exact state handling or handoffs. They supplement, rather than override, the repository and user authorization.

## State transitions

| State | Required event | Parent action |
|---|---|---|
| IMPLEMENTING | Engineer reports files, commands, unrun checks, and freezes | Verify the change set; hand test-only write ownership to the tester |
| TESTING | Tester finds a reproducible requirement failure | Preserve the test and freeze; authorize only the engineer's scoped fix |
| FIXING | Engineer freezes a scoped fix | Refresh the input fingerprint; return to the tester, not directly to review |
| TESTING | Tester passes all required independent checks and freezes | Revoke test write ownership; create a fresh read-only reviewer |
| REVIEWING | Any acceptance or generic gate fails | Record the real review round; if below the configured limit, return to FIXING |
| REVIEWING | Complete report has overall=pass for the same frozen fingerprint | Enter READY_TO_COMMIT; parent checks the staged set and evidence |
| READY_TO_COMMIT | Diff, source fingerprint, and required checks still match | Make only the authorized local commit; do not dispatch the next phase yet |
| COMMITTING | Commit succeeds and is verified | Record its full hash; mark the phase complete; only then dispatch the next phase |
| Any active state | Unexpected changes, missing evidence, permissions, or unavailable environment | BLOCKED; preserve ownership and evidence; resolve without fabricating success |
| Any repair cycle | Configured test-failure or review-round limit reached | PAUSED; report unresolved findings and wait for a user decision |

Keep test attempts and formal review rounds separate. Changing agents, models, sessions, or skill versions does not reset either count. An environment block is not a code failure, but it also cannot be counted as a pass. Treat duplicated agent-message/finished notifications as one report.

## Handoff packet

Every child starts with zero assumed conversational context. Include:

- Role, phase, attempt/round, exact branch/base/workspace/server/parent ID.
- Required repository and workflow files; acceptance IDs; objective expected observations.
- Current state, previous failures, exact remaining verification, and frozen input fingerprint.
- Writable paths and append-only paths; command and environment boundaries.
- Return recipient, completion/freeze behavior, and a prohibition on child dispatch or unapproved commits.

Do not include credentials or secret environment values in the prompt or report. Refer to pre-authorized environment variable names and project test helpers instead.

## Independent test requirements

The tester should preserve concrete examples and independently calculated expected outcomes. Select checks proportional to risk: output correctness, state invariants, permissions, persistence/rollback, concurrency, and recovery. Require at least one meaningful negative path for high-impact changes; use controlled fault injection to demonstrate that important tests can fail when their target invariant is broken.

Do not derive expected answers by calling the production calculation being tested. Existing fakes and test interfaces are acceptable when they isolate infrastructure without hiding the behavior under test. A failing test can be incorrect; requirement conflicts are returned to the parent, not silently resolved by editing either side.

## Generic review gates

- G1: Existing valid behavior and assertions do not regress.
- G2: Independent test expectations come from requirements, not the implementation's answer.
- G3: Required checks really ran; required skips/xfails are zero; no weakened assertions.
- G4: Report matches branch/base, full tracked/untracked change set, and frozen file fingerprint.
- G5: Write ownership, execution, review, external actions, and commits stayed within authorization.
- G6: Relevant interfaces, project docs, CI entrypoints, and the implementation agree.

An individual pass does not imply overall pass. Missing or mismatched evidence is `blocked` or `fail` according to the reported criterion, never inferred success.

## Report envelope

```json
{
  "report_id": "unique-report-id",
  "workflow_version": 1,
  "phase": "phase-id",
  "role": "tester-or-reviewer",
  "agent_id": "actual-agent-id",
  "round": 1,
  "branch": "actual-branch",
  "base_commit": "actual-full-base-hash",
  "source_digest": "actual-frozen-digest",
  "overall": "pass-fail-or-blocked",
  "items": [{"id": "acceptance-or-gate-id", "verdict": "pass-fail-or-blocked", "evidence": "test-name-or-file:line"}],
  "checks": [{"argv": ["actual", "command"], "exit_code": 0, "output_path": "raw-output", "required": true}],
  "authored_by": "actual-author-id",
  "executed_by": ["actual-executor-ids"],
  "analyzed_by": "actual-analyst-id",
  "changed_files": [],
  "blocking_findings": [],
  "limitations": [],
  "unexecuted_checks": [],
  "workspace_frozen": true
}
```

The tester's `round` is a test batch; the reviewer's `round` is the formal review round. Preserve raw stdout/stderr, exit codes, machine-readable test results, and a snapshot of the exact input files. A missing hash tool can be noted as pending parent verification, not replaced with a guessed value.

## Controlled execution fallback

When a child has working file tools but no command channel:

1. It authors tests and an execution request, then freezes all writes.
2. The request contains `request_id`, base/fingerprint, exact argv arrays, cwd, non-secret environment variable names, expected effects, artifact paths, and `ready_for_parent_execution=true`.
3. The parent reviews scope and side effects, including database isolation, processes, network access, and file writes. It may reject a command without changing the denied permission boundary.
4. The parent runs approved commands in its existing environment, captures raw output/exit/JUnit or equivalent, and records the exact source fingerprint at execution time.
5. The tester receives the unedited evidence, independently analyzes it, and issues the final report with separate authorship, execution, and analysis attribution.

If the child's editor is also unavailable, it returns a complete patch. The parent reviews and applies only the authorized paths; this is still attributed assistance, not independent execution by the child. A reviewer can request an additional controlled rerun when evidence is insufficient.

## Plugin boundary

The protocol can run through Paseo tools and notifications alone. Do not claim a daemon will continue transitions while the parent is stopped or a notification is missing. Only introduce a plugin for separately authorized deterministic enforcement, persistence, or UI. Plugins are trusted, unsandboxed code; inspect their global switch and obtain the required explicit permission before enabling or installing them. Never use a plugin to bypass approvals, mutate ACLs, auto-accept every permission, or grant deployment/real-account authority.
