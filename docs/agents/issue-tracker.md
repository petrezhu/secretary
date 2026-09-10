# Issue Tracker

Issues and specs live in the repo's issue tracker (GitHub or Forgejo).

## Conventions

- **Create an issue**: `POST` to the issues API with title, body, and labels
- **Read an issue**: `GET` the issue by number
- **List issues**: `GET` the issues list (optional query: state/labels)
- **Comment**: `POST` a comment on the issue
- **Close**: `PATCH` the issue with `{"state": "closed"}` — post the closing explanation as a comment first

## Label IDs

Labels take **integer IDs**, not name strings. Always fetch the labels list first, then pass numeric IDs.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests.)_
