# RHIT repository workflow

- The user authorizes committing and pushing substantial completed changes to
  `https://github.com/IanKelley05/Senior-Design-Software.git` after relevant
  verification. Examples include a feature, a meaningful fix, or a completed
  implementation stage. Do not push every small intermediate edit.
- Review the diff and staged files before committing. Keep `.env`, credentials,
  `.local/`, database contents, downloaded tools, and `.venv/` out of Git.
- Preserve unrelated user work. Commit the completed task with a descriptive
  message, then push to the configured upstream branch (currently `origin/main`).
- Do not force-push or overwrite remote changes. If the remote has advanced,
  integrate it safely before pushing. Report authentication or permission blocks
  honestly; do not claim a push succeeded without verifying the remote commit.
- Report the commit and push result to the user. Follow any later instruction to
  defer or skip committing/pushing for a particular task.
