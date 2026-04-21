## Summary

<!-- What does this PR do? 1-3 sentences. -->

## PR Type

<!-- Check all that apply. -->

- [ ] Bug fix
- [ ] Documentation
- [ ] Tests only
- [ ] Refactor / maintenance
- [ ] Feature / behavior change
- [ ] Breaking change

## Motivation / Context

<!-- Why is this change needed? What problem does it solve? -->

## Changes

<!-- Bullet points of what changed. -->

-

## Validation

<!-- How did you verify this works? Include the exact commands you ran. -->

```bash
# Example
ruff check src/ tests/
black --check src/ tests/
pytest tests/unit/ -q --ignore=tests/unit/test_file_sizes.py
pytest tests/unit/test_file_sizes.py -q
```

## Checklist

- [ ] I read `CONTRIBUTING.md`
- [ ] I linked the relevant issue(s) / discussion(s) below
- [ ] If this is a feature / behavior / API / architecture change, there was a pre-existing issue or discussion opened before this PR, and a maintainer explicitly approved it
- [ ] If this is not a feature PR, the change is limited to a bug fix, docs, tests, or a small non-behavioral refactor
- [ ] I kept this PR focused and did not include unrelated changes
- [ ] I updated docs if this changes user-visible behavior, config, CLI, APIs, or developer workflow
- [ ] I added or updated tests when needed
- [ ] `ruff check src/ tests/` passes
- [ ] `black --check src/ tests/` passes
- [ ] `pytest tests/unit/` passes locally, or I explained below why I could not run the full suite
- [ ] If frontend files changed, I ran `npm run format:check` and `npm run build` in `src/kohakuterrarium-frontend/`
- [ ] CI on my fork is green, or I explained the exception below

## Related Issues / Discussions

<!-- Required for feature PRs. Link issues/discussions explicitly.
Examples:
- Fixes #123
- Relates to #456
- Approved in #789
- Discord / QQ discussion approved by @maintainer on YYYY-MM-DD
-->

## Notes for Reviewers

<!-- Optional: call out risky areas, follow-ups, tradeoffs, or places where you'd like focused review. -->

## Screenshots / Logs (if applicable)

<!-- UI changes, CLI output, benchmark tables, stack traces, etc. -->

## Breaking Changes (if applicable)

<!-- Describe migration steps, compatibility impact, and what downstream users need to do. -->

## Exceptions / Not Run

<!-- If you skipped any checklist item, explain exactly why. -->
