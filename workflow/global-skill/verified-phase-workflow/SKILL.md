---
name: verified-phase-workflow
description: Coordinate high-impact implementation with separate engineer, independent tester, read-only reviewer, and parent-controlled phase commits. Use when the user explicitly selects staged development, independent acceptance tests, multi-agent handoffs, or review-gated commits; do not impose it on ordinary small tasks or use it to grant deployment or permission changes.
---

# Verified Phase Workflow

Use this skill to preserve evidence and responsibility across phases. It is a collaboration protocol, not a background scheduler or an expansion of user permissions.

## Start

1. Read the repository instructions, the user's task, and the project workflow config. Select high-impact work deliberately; routine edits can use normal engineering and review.
2. Use the available Paseo instructions. Read current profiles and notes before delegation. Resolve roles by config, not remembered model IDs. Check the target host and workspace, then materialize the actual profile into agent creation settings.
3. Record the exact branch, base commit, changed/untracked files, write owner, acceptance criteria, and command requirements. Keep active state in the project, never in the shared skill.
4. Reconcile in-flight work before changing the protocol. A new skill version does not reset existing test failures, review rounds, authorization, or ownership.

## Responsibility and gates

| Role | Owns | Must not |
|---|---|---|
| Parent / orchestrator | Acceptance work orders, dispatch, state, evidence integration, authorized local commit | Replace independent testing with engineer claims; silently change requirements; concurrently edit implementation |
| Engineer | Production changes, basic tests, affected contracts, defect fixes | Self-approve independent acceptance; weaken tests to fit implementation; commit or dispatch downstream |
| Independent tester | Contract-derived tests, counterexamples, failure evidence, independent test report | Edit production code or existing assertions to manufacture green; treat unrun/skip/xfail as pass |
| Read-only reviewer | Inspect the complete diff, tests, raw evidence, fingerprints, role boundaries | Write production/tests; infer pass from a finished notification; commit or advance the phase |

Use this sequence for high-impact phases:

`IMPLEMENTING -> TESTING -> REVIEWING -> READY_TO_COMMIT -> COMMITTING -> PHASE_COMPLETE`

A tester failure returns to the engineer while preserving the failing test. A review failure also returns to the engineer, followed by independent retest and a new review. Only the parent dispatches the next role. Only after all required acceptance and generic checks pass may the parent make the authorized local commit and verify its contents. Push, merge, deployment, external messages, destructive history, and production/real-account operations require their own authorization.

Choose the declared isolation mode before writing. Shared workspaces use one write owner at a time; independent parallel tasks use separate worktrees with explicit integration ownership. Do not create a worktree merely because a profile says so when the project has chosen serial editing.

## Evidence

- Acceptance criteria must be objective and traceable to test names or file lines. The number of phases or tests is not itself acceptance.
- The tester derives expectations from requirements, contracts, business invariants, and worked examples before reading the implementation. Test important side effects, error paths, persistence, and controlled fault injection when relevant.
- The reviewer independently checks actual tracked and untracked files, exit codes, full outputs, skip/xfail counts, unexecuted checks, and content fingerprints. Keep raw evidence in a declared artifact location.
- A `finished` notification is not a pass. Deduplicate explicit reports and finished notifications by phase, role, agent, and report ID. Environment failures are recorded separately from code/test failures.
- Each handoff is self-contained: task, context, files, current state, failed attempts, acceptance, constraints, and return conditions.

Read [references/protocol.md](references/protocol.md) for the state transitions, handoff/return schema, and controlled execution fallback. Read [references/project-config.md](references/project-config.md) when creating a project config or migrating an existing one.

## Permission and execution boundaries

Never repair ACLs, change sandbox/approval configuration, expand filesystem access, or switch to an unrestricted terminal to complete a normal test task. Evaluate permission requests individually and retain denial decisions.

If a child cannot run commands but its file tools work, the parent may execute reviewed, scoped commands under the parent's existing permissions. Freeze test writes first, require an execution request, and attribute `authored_by`, `executed_by`, and `analyzed_by` separately. If file tools also fail, return a patch for explicit parent review rather than bypassing the restriction. The reviewer must be able to inspect this collaboration and raw evidence.

## Reuse and scope

Project configs select profile names, phase acceptance, write paths, commands, artifact locations, and commit policy. Keep credentials, provider IDs, agent IDs, branch names, and project-specific business rules out of the shared skill.

Profiles are host-local launch bundles; skills are reusable instructions. Installing this skill on another Paseo host requires equivalent profiles or explicit provider discovery. Other harnesses need an adapter for their delegation and notification tools. A plugin is optional and must not be installed or enabled merely to use this protocol.
