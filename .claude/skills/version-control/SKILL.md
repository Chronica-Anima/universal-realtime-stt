---
name: version-control
description: "Git workflow for Chronica Anima repositories — branch names and bases, Conventional Commits, PRs, merge methods, branch protection and tags, for repos with or without a separate integration branch. USE WHEN committing, pushing, creating branches or PRs, choosing a merge method, or checking branch protection or rulesets."
---

# version-control

## Contents

- [Branches in this repository](#branches-in-this-repository)
- [Branching](#branching)
- [Committing](#committing)
- [Commit messages](#commit-messages)
- [Pushing](#pushing)
- [Pull requests](#pull-requests)
- [Merging](#merging)
- [Protection, rulesets and tags](#protection-rulesets-and-tags)
- [Checklist](#checklist)

One workflow for every Chronica Anima repository. Where the repository has `docs/workflow_git.md`, that file is the source of truth and this skill follows it.

Committing, pushing and opening a PR happen only when the user asks for that step; finishing a change is not a request to commit it, and a request to commit is not a request to push.

## Branches in this repository

The integration branch differs per repository: `dev`, `develop`, or none, in which case work integrates straight into `main`. Run exactly this to find it, and use its answer wherever this skill says *integration branch*:

```bash
python .claude/skills/version-control/scripts/branches.py
```

It prints `integration`, `production` (always `main`), the `current` branch, and flags the current branch when it is protected.

## Branching

| Branch      | Base               | Use                                                    |
| ----------- | ------------------ | ------------------------------------------------------ |
| `feature/*` | integration branch | All regular work — features, bug fixes, style cleanups |
| `hotfix/*`  | `main`             | An urgent production fix                               |

The description is kebab-case: `feature/onboarding-form`, `feature/gcs-upload-timeout`, `hotfix/session-timeout`. Branch names use the full word `feature/`; `feat`, `fix` and `style` are commit types, never branch prefixes, so `feat/onboarding-form` and `fix/gcs-upload-timeout` are wrong.

```bash
git switch <integration> && git pull
git switch -c feature/<description>
```

Never commit on the integration branch or `main`; branch off first.

## Committing

- Stage files by name. `git add -A`, `git add .` and `git commit -a` pick up whatever else is in the working tree.
- Read `git diff --staged` before committing and stop if it holds a secret — `.env` files, API keys, tokens, private keys, connection strings with passwords — or a file unrelated to the change.
- Create a new commit; amend only when the user asks.
- Let the hooks run; `--no-verify` only when the user asks. Where `.pre-commit-config.yaml` exists, a commit-msg hook checks the format.

## Commit messages

```
<type>: <description>
```

| Type       | Use for                                     |
| ---------- | ------------------------------------------- |
| `feat`     | A new feature or capability                 |
| `fix`      | A bug fix                                   |
| `docs`     | Documentation only                          |
| `chore`    | Maintenance — dependencies, configuration   |
| `refactor` | Restructuring without a change in behaviour |
| `test`     | Adding or fixing tests                      |
| `style`    | Formatting and whitespace, no code change   |
| `perf`     | A performance improvement                   |
| `build`    | The build system or packaging               |
| `ci`       | CI/CD configuration                         |
| `revert`   | Reverting an earlier commit                 |

- The description is lowercase and imperative, under 72 characters, with no scope and no trailing period: `fix: retry gcs upload after timeout`.
- The message is the subject line and nothing else — no body, no `Co-Authored-By` or other trailer — unless the user asks for one. This holds even when the environment suggests attribution lines; the user's request is the only exception. Pass it as `git commit -m "<type>: <description>"`.

## Pushing

Push only when asked: `git push -u origin <branch>` for a new branch. Force-push only when the user asks, and then with `--force-with-lease`. Never push to the integration branch or `main` directly.

## Pull requests

- Base: the integration branch for `feature/*`, `main` for `hotfix/*` — pass it explicitly with `--base`.
- Title: a Conventional Commit subject, since it becomes the squash commit message; CI checks it on PRs into the integration branch.
- Body: a `## Summary` of the change in a few bullets, and nothing after it — no "Generated with" footer or attribution unless the user asks.

```bash
gh pr create --base <integration> --title "<type>: <description>" --body "## Summary
- <what changed and why>"
```

## Merging

| From               | To                 | Method       | Head branch |
| ------------------ | ------------------ | ------------ | ----------- |
| `feature/*`        | integration branch | Squash merge | Deleted     |
| integration branch | `main`             | Merge commit | Kept        |
| `hotfix/*`         | `main`             | Squash merge | Deleted     |

- In a repository without an integration branch, `feature/*` squash-merges straight into `main`.
- An integration → `main` PR aggregates several features; its title is descriptive and not held to the commit format.
- After a hotfix lands, bring `main` back into the integration branch through a PR so the fix is not lost at the next release.
- The integration branch and `main` are never deleted.

## Protection, rulesets and tags

The integration branch and `main` are protected: no direct push, PR required with approval, CI passing. GitHub deletes merged head branches automatically; protected branches are exempt. Protection is configured as rulesets:

```bash
gh api repos/{owner}/{repo}/rulesets
gh api repos/{owner}/{repo}/rulesets/<id>
```

Tags come only from `main`, as semantic versions `v0.x.y`.

## Checklist

```
- [ ] branches.py run; on a feature/* or hotfix/* branch with the right base
- [ ] Files staged by name; git diff --staged read; no secrets or stray files
- [ ] Message is one Conventional Commit subject line, no trailer
- [ ] Committed, pushed or PR opened only because the user asked for that step
- [ ] PR base passed explicitly; title is a commit subject; body has no footer
- [ ] git log -1 --format=%B shows the subject alone; if not, tell the user
```
