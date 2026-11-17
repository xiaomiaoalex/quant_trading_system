# Project configuration

Keep project state and requirements out of the shared skill. The repository's normal instructions take precedence, and the user can select a lighter workflow for low-risk work.

## Minimal example

```json
{
  "schema_version": 1,
  "workflow": "verified-phase-workflow",
  "profiles": {
    "orchestrator": "architect",
    "engineer": "engineer",
    "tester": "tester",
    "reviewer": "reviewer"
  },
  "isolation": {
    "mode": "single-writer-shared-workspace",
    "state_path": ".paseo/workflow-state.json",
    "artifact_directory": "artifacts/workflow/"
  },
  "limits": {
    "max_review_rounds": 3,
    "max_consecutive_test_failures": 3
  },
  "commit_policy": {
    "authorized_committer": "parent",
    "automatic_push": false,
    "automatic_merge": false,
    "automatic_deploy": false
  },
  "phases": [
    {
      "id": "example",
      "status": "active",
      "base_commit": "replace-with-the-real-full-base-hash",
      "acceptance": [
        {"id": "1.1", "criterion": "An objectively testable observable behavior", "evidence": "test-name-or-file-line"}
      ],
      "write_paths": ["replace-with-scoped-paths"],
      "checks": [{"argv": ["replace-with-project-command"], "required": true, "required_skipped": 0}]
    }
  ]
}
```

This is a template, not a runnable phase. Replace every placeholder with project-specific values before dispatch. Use `parallel-worktrees` only when tasks can be independently integrated; do not change an in-flight shared-workspace task merely to match a profile preference.

## Migration

- Read the current state and preserve its actual phase, base, write owner, review/test counters, permissions, processed notification IDs, and historical evidence.
- Point the existing project entrypoint at the shared skill and retain only its local acceptance, paths, gates, and exceptions.
- Do not rewrite history to pretend the old protocol used the new one. Record the adoption version and explicitly state whether in-flight evidence remains under the previous protocol or needs revalidation.
- Freeze business implementation while changing workflow metadata if another role has test-only ownership. Refresh any affected review fingerprint and disclose the metadata change.

## Host and skill distribution

Paseo profiles are saved on a host, not in a project. Materialize their current provider/model/mode/thinking/features after reading `list_profiles`; there is no `profile` argument to `create_agent`. Export role semantics and reviewed settings, not credentials. On another host, install the versioned skill and configure equivalent profiles, or use provider discovery with an explicit fallback report.

The versioned source can be distributed as a small skills repository or packaged skill directory. Prefer copying a release into the host's skill library over making every project depend on a mutable absolute source path. Verify installed and source copies match. Project instructions need only select the shared workflow and its local config.
