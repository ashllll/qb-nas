# GitHub workflow

本仓库的 gh/git 操作约束。命令在仓库根目录执行。

## 分支与 PR

- 推送用 HTTPS（SSH 在本机不可用）：
  `git -c credential.helper='!gh auth git-credential' push https://github.com/ashllll/qb-nas.git HEAD:<branch>`
- PR body 先写文件，再 `gh pr create --body-file /tmp/prNN.md`；多行 `--body` 会被安全网拦截。
- 合并：`gh pr ready <n>` → `gh pr merge <n> --squash`。
- 删远端分支：`gh api -X DELETE repos/ashllll/qb-nas/git/refs/heads/<branch>`；不要用 `git push --delete`。
- 破坏性 git 命令（`git branch -D`、`git checkout --`、`git rebase --abort`、`git push --delete`）会被安全网拦截；先读状态，再用 `git restore` / `git worktree remove` 等非破坏性路径。
- `auto-merge.yml` 只处理非 draft、非 `agent` 标签、非 `factory/*` 分支，且 PR head SHA 必须等于触发 run 的 SHA。

## CI

- 看 CI 用 `gh run view <run-id>` 或 `gh run list`；`gh pr checks` 可能滞后。
- 三个 job：`gates`（ruff/format/pytest+coverage）、`docker-build`、`mac-agent-selftest`。

## Issue

- PR body 写 `Closes #N`。merge 后 issue 可能延迟关闭，先查 `closedByPullRequestsReferences`，不要看到 `OPEN` 就手动 close。
- 读完整票面用 `gh issue view N --json title,body,labels`；`deck_issue_get` 只是状态摘要，不替代票面。

## 环境变量

- 不读、不写任何 `.env*`（含 `.env.test`）。测试需要空环境时用 `ENV_FILE=/tmp/empty.env`。
