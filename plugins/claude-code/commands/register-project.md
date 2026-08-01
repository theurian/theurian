---
description: Register the current Git repository as a Theurian project.
allowed-tools: Bash(theurian:*), Bash(git:*)
---

# /theurian:register-project

Register the current repository so Theurian can serve its knowledge.

## What to do

1. Confirm the working directory is inside a Git repository:

   ```sh
   git rev-parse --show-toplevel
   ```

2. Register it:

   ```sh
   theurian project register "$(git rev-parse --show-toplevel)" --json
   ```

3. If `.theurian/` does not yet exist, run `theurian init --json` and report the
   files it created, including the `.gitignore` entries.

## Rules

- One Git worktree is one project. If the user is in a worktree, register that
  worktree; do not substitute the main checkout. Two worktrees can be on
  different branches and therefore have different knowledge (FR-P5).
- Registering an already-registered project is a no-op. Report it as such rather
  than creating a duplicate.
- `theurian init` never overwrites an existing file.
