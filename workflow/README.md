# Verified phase workflow source

`verified-phase-workflow/` is the versioned source of the reusable, project-neutral workflow. It is not a Paseo plugin and does not run a scheduler or change permissions.

Install by copying the complete skill folder to the host's skills directory, such as `D:/codex-home/skills/verified-phase-workflow` on this machine. `profile-notes/` contains project-neutral role descriptions that can be applied through Paseo's supported profile configuration. Provider, model, approval settings, and profile identifiers are not replaced by this source.

Validate both source and installed copies with the skill-creator `quick_validate.py` tool after changes. Compare their file hashes to detect a stale installation.

Project config and live phase state remain in their own repositories. The quant system's in-flight phase 2 preserves its existing counters, write ownership, and review history; no global-skill adoption may reset them.

Cross-project behavioral validation is deferred; the global skill and profile configuration do not claim that a second project has already exercised this protocol.
