<!--
  Thanks for contributing to Nuvrail. Keep PRs focused — one logical change per PR is
  easier to review and revert. See CONTRIBUTING.md for local setup, test, and lint commands.
-->

## What & why

<!-- What does this change do, and what problem does it solve? Link the issue it closes. -->

Closes #

## How was it tested?

<!-- The commands you ran locally and their result. "Trust me" is not a test plan. -->

## Checklist

- [ ] The change is scoped to one logical concern (split unrelated changes into separate PRs).
- [ ] `python -m ruff check .` passes (lint).
- [ ] `python -m pytest --ignore=tests/integration --ignore=tests/e2e` passes locally.
- [ ] New/changed behavior is covered by tests (bug fixes include a regression test).
- [ ] `docker compose build` still succeeds if the build or dependencies changed.
- [ ] `gitleaks` is clean — no credentials, tokens, or real mailbox data committed
      (CI runs it informationally; treat any hit as blocking until reviewed).
- [ ] Docs updated if behavior/config changed, **and any ASCII diagrams touched by this
      change are updated in the same PR** (stale diagrams are worse than none).
- [ ] For changes to the write/approval path: I confirmed no destructive operation
      (delete/EXPUNGE) can bypass human approval.

## Notes for reviewers

<!-- Anything a reviewer should know: risk areas, follow-ups deferred, screenshots for UI. -->
