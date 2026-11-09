#!/usr/bin/env python3
"""Run one WIP=1 GitHub PR factory iteration for qb-nas.

Claims one ready issue, lets OpenCode implement it in a throwaway worktree,
runs the repo gates, asks a fresh OpenCode session to review the diff, then
pushes a draft PR labelled agent. It never merges anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = "ashllll/qb-nas"
BASE = "main"
REMOTE_URL = os.environ.get("FACTORY_REMOTE_URL", f"https://github.com/{REPO}.git")
MODEL = os.environ.get("FACTORY_MODEL", "opencode-go/deepseek-v4.1-flash")
OPENCODE = os.environ.get("OPENCODE_BIN") or str(Path.home() / ".opencode/bin/opencode")
READY = "ready-for-agent"
AGENT = "agent"
RUNNING = "agent:running"
IN_REVIEW = "agent:in-review"
BLOCKED = "agent:blocked"
SKIP_TYPES = {"wayfinder:grilling", "wayfinder:research", "wayfinder:prototype", "wayfinder:map"}
FORBIDDEN_PREFIXES = (".env", ".github/", ".planning/", ".agents/", ".venv/", "data/", "qb-nas/", "research/")
FORBIDDEN_EXACT = {"pyproject.toml", "requirements.txt", "uv.lock"}
LOG_DIR = Path(tempfile.gettempdir()) / "qb-nas-factory" / "logs"
WORKTREE_ROOT = Path(tempfile.gettempdir()) / "qb-nas-factory" / "worktrees"
LOG = None


class FactoryError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)
    if LOG is not None:
        LOG.write(message + "\n")
        LOG.flush()


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired as exc:
        raise FactoryError(f"timeout after {timeout}s: {' '.join(cmd)}") from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    if output.strip():
        log(output.rstrip())
    if check and proc.returncode != 0:
        raise FactoryError(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def git(*args: str, cwd: Path | None = None, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", "-c", "credential.helper=!gh auth git-credential", *args], cwd=cwd, timeout=timeout, check=check)


def gh(*args: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["gh", *args], timeout=timeout, check=check)


def gh_json(*args: str, timeout: int = 120) -> Any:
    proc = gh(*args, timeout=timeout)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise FactoryError(f"gh returned non-JSON for {' '.join(args)}") from exc


def repo_root() -> Path:
    return Path(git("rev-parse", "--show-toplevel").stdout.strip())


def has_acceptance(body: str) -> bool:
    return bool(
        re.search(r"(?im)^#{1,3}\s*(验收|acceptance criteria)\b", body)
        or re.search(r"(?im)^\s*验收[:：]", body)
    )


def fetch_ready_issues() -> list[dict[str, Any]]:
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        issues(states: OPEN, labels: ["ready-for-agent"], first: 50) {
          nodes {
            number
            title
            body
            url
            labels(first: 20) { nodes { name } }
            blockedBy(first: 20) { nodes { number state } }
          }
        }
      }
    }
    """
    owner, name = REPO.split("/")
    data = gh_json("api", "graphql", "-f", f"query={query}", "-f", f"owner={owner}", "-f", f"name={name}")
    if data.get("errors"):
        raise FactoryError(f"GraphQL errors: {data['errors']}")
    return data["data"]["repository"]["issues"]["nodes"]


def issue_problem(issue: dict[str, Any]) -> str | None:
    labels = {node["name"] for node in issue["labels"]["nodes"]}
    blockers = issue["blockedBy"]["nodes"]
    if READY not in labels:
        return f"missing {READY} label"
    if labels & SKIP_TYPES:
        return f"decision ticket: {sorted(labels & SKIP_TYPES)}"
    if labels & {RUNNING, IN_REVIEW, BLOCKED}:
        return f"already claimed: {sorted(labels & {RUNNING, IN_REVIEW, BLOCKED})}"
    open_blockers = [str(item["number"]) for item in blockers if item["state"] != "CLOSED"]
    if open_blockers:
        return "blocked by open issue(s): " + ", ".join(open_blockers)
    if not has_acceptance(issue.get("body") or ""):
        return "missing '## 验收' / 'Acceptance criteria' section"
    return None


def choose_issue(explicit: int | None) -> dict[str, Any]:
    issues = sorted(fetch_ready_issues(), key=lambda item: item["number"])
    if explicit is not None:
        for issue in issues:
            if issue["number"] == explicit:
                problem = issue_problem(issue)
                if problem:
                    raise FactoryError(f"issue #{explicit} is not runnable: {problem}")
                return issue
        raise FactoryError(f"issue #{explicit} is not open with label {READY}")
    runnable = [(issue, issue_problem(issue)) for issue in issues]
    ready = [issue for issue, problem in runnable if problem is None]
    if not ready:
        details = "; ".join(f"#{issue['number']}: {problem}" for issue, problem in runnable[:8])
        raise FactoryError("no runnable issue" + (f" ({details})" if details else ""))
    return ready[0]


def wip_guard() -> None:
    prs = gh_json("pr", "list", "--repo", REPO, "--state", "open", "--label", AGENT, "--json", "number,title,isDraft")
    if prs:
        raise FactoryError("WIP=1: open agent PR(s): " + ", ".join(f"#{pr['number']}" for pr in prs))
    running = gh_json("issue", "list", "--repo", REPO, "--state", "open", "--label", RUNNING, "--json", "number,title")
    if running:
        raise FactoryError("WIP=1: issue(s) already claimed: " + ", ".join(f"#{item['number']}" for item in running))


def claim(issue: dict[str, Any]) -> None:
    number = issue["number"]
    gh("issue", "edit", str(number), "--repo", REPO, "--add-label", RUNNING)
    gh("issue", "comment", str(number), "--repo", REPO, "--body",
       "工厂接手：本机 OpenCode 在独立 worktree 实现，通过门禁和独立复核后开 draft PR；不会自动合并。")


def fail(issue: dict[str, Any] | None, message: str, log_path: Path | None = None) -> None:
    log("FACTORY FAILED: " + message)
    if issue is None:
        return
    number = str(issue["number"])
    body = f"工厂停止：{message}"
    if log_path:
        body += f"\n\n本机日志：{log_path}"
    for cmd in (
        ("issue", "comment", number, "--repo", REPO, "--body", body),
        ("issue", "edit", number, "--repo", REPO, "--remove-label", RUNNING),
        ("issue", "edit", number, "--repo", REPO, "--add-label", BLOCKED),
    ):
        try:
            gh(*cmd, check=False)
        except Exception:
            pass


def check_paths(worktree: Path) -> list[str]:
    changed = set(git("diff", "--name-only", "factory/main", cwd=worktree).stdout.splitlines())
    changed |= set(git("ls-files", "--others", "--exclude-standard", cwd=worktree).stdout.splitlines())
    bad = []
    for name in sorted(changed):
        if not name:
            continue
        if name in FORBIDDEN_EXACT or any(name == p or name.startswith(p) for p in FORBIDDEN_PREFIXES):
            bad.append(name)
    return bad


def implement_prompt(issue: dict[str, Any], worktree: Path) -> str:
    return f"""You are implementing GitHub issue #{issue['number']} in the qb-nas repository.

Worktree: {worktree}
Base branch: factory/main

Read AGENTS.md, CLAUDE.md, CONTEXT.md and docs/agents/development-workflow.md before editing.

Issue title: {issue['title']}
Issue body:
{issue['body']}

Constraints:
- Implement only this issue. Do not touch unrelated tickets.
- Do not edit .github/, .env, data/, pyproject.toml, requirements.txt, uv.lock.
- Do not add dependencies.
- Follow TDD where practical: add or adjust tests that prove the acceptance criteria.
- Run focused tests with the venv python; keep the full suite green if feasible.
- Do not commit, push, or open a PR. Leave changes in this worktree.
- If the issue is ambiguous or you are blocked, stop and explain instead of guessing.
- Finish with a line starting with SUMMARY: and a line starting with FILES:.
"""


def review_prompt(issue: dict[str, Any], diff: str) -> str:
    return f"""You are an independent code reviewer for qb-nas issue #{issue['number']}.

Do not edit files, do not run git write commands, and do not approve without evidence.

Issue title: {issue['title']}
Issue body:
{issue['body']}

Diff:
--- begin diff ---
{diff}
--- end diff ---

Check correctness, edge cases, security, test coverage, and whether the change stays in scope.
Findings must cite file:line. End with exactly one verdict line:
VERDICT: PASS
or
VERDICT: FAIL
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", type=int, help="explicit issue number")
    parser.add_argument("--dry-run", action="store_true", help="select and print the issue without claiming")
    parser.add_argument("--implement-timeout", type=int, default=1800)
    parser.add_argument("--review-timeout", type=int, default=900)
    parser.add_argument("--check-timeout", type=int, default=1800)
    args = parser.parse_args()

    global LOG
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"{stamp}.log"
    LOG = log_path.open("a", encoding="utf-8")

    issue: dict[str, Any] | None = None
    worktree: Path | None = None
    try:
        if not shutil.which("gh"):
            raise FactoryError("gh is not installed")
        if not Path(OPENCODE).exists():
            raise FactoryError(f"opencode not found: {OPENCODE}")
        root = repo_root()
        wip_guard()
        issue = choose_issue(args.issue)
        log(f"selected #{issue['number']}: {issue['title']}")
        if args.dry_run:
            blockers = [item["number"] for item in issue["blockedBy"]["nodes"]]
            print(json.dumps({"number": issue["number"], "title": issue["title"], "url": issue["url"], "blockers": blockers, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return 0

        claim(issue)
        number = issue["number"]
        branch = f"factory/{number}"
        worktree = WORKTREE_ROOT / str(number)
        if worktree.exists():
            raise FactoryError(f"worktree already exists: {worktree}")
        git("fetch", REMOTE_URL, f"{BASE}:refs/remotes/factory/main", cwd=root, timeout=300)
        git("worktree", "add", "-b", branch, str(worktree), "factory/main", cwd=root, timeout=300)
        if not (worktree / ".venv").exists():
            run(["uv", "sync", "--extra", "dev", "--locked"], cwd=worktree, timeout=600)

        log("=== implementation ===")
        run([OPENCODE, "run", "--model", MODEL, "--auto", implement_prompt(issue, worktree)], cwd=worktree, timeout=args.implement_timeout)

        if git("rev-parse", "HEAD", cwd=worktree).stdout.strip() != git("rev-parse", "factory/main", cwd=worktree).stdout.strip():
            git("reset", "--soft", "factory/main", cwd=worktree)
        if not git("status", "--porcelain", cwd=worktree).stdout.strip():
            raise FactoryError("agent left no changes")
        bad = check_paths(worktree)
        if bad:
            raise FactoryError("agent touched forbidden paths: " + ", ".join(bad))

        log("=== gates ===")
        run(["npm", "run", "check"], cwd=worktree, timeout=args.check_timeout)
        git("diff", "--check", cwd=worktree)
        git("add", "-A", cwd=worktree)
        git("diff", "--cached", "--check", cwd=worktree)
        status_before_review = git("status", "--porcelain", cwd=worktree).stdout

        log("=== independent review ===")
        diff = git("diff", "--cached", "factory/main", cwd=worktree).stdout
        if len(diff) > 140_000:
            raise FactoryError(f"diff too large for review ({len(diff)} bytes)")
        review = run([OPENCODE, "run", "--model", MODEL, "--auto", review_prompt(issue, diff)], cwd=worktree, timeout=args.review_timeout)
        review_text = (review.stdout or "") + (review.stderr or "")
        if re.search(r"VERDICT:\s*FAIL", review_text, re.I):
            raise FactoryError("independent review returned FAIL: " + review_text[-1200:])
        if not re.search(r"VERDICT:\s*PASS", review_text, re.I):
            raise FactoryError("independent review returned no PASS verdict: " + review_text[-1200:])
        if git("status", "--porcelain", cwd=worktree).stdout != status_before_review:
            raise FactoryError("reviewer modified the worktree")

        log("=== commit and draft PR ===")
        git("add", "-A", cwd=worktree)
        commit_message = f"fix: {issue['title']} (#{number})"
        git("commit", "-m", commit_message, "-m", "Generated by the local agent factory; verified with npm run check and an independent review.", cwd=worktree, timeout=120)
        git("push", REMOTE_URL, f"HEAD:refs/heads/{branch}", cwd=worktree, timeout=300)

        stat = git("diff", "--stat", "factory/main", "HEAD", cwd=worktree).stdout.strip()
        pr_body = f"""Closes #{number}

## 变更
{stat}

## 验证
- npm run check 通过（lock + ruff + coverage，fail_under 79）
- 独立 OpenCode 复核：PASS

> 本 PR 由 agent 工厂生成，保持 draft，等待人工审阅；auto-merge 已跳过 agent 标签和 factory 分支。
"""
        pr = gh("pr", "create", "--repo", REPO, "--draft", "--base", BASE, "--head", branch,
                "--title", commit_message, "--body", pr_body, "--label", AGENT, "--label", IN_REVIEW, timeout=120)
        pr_url = pr.stdout.strip().splitlines()[-1]
        gh("issue", "edit", str(number), "--repo", REPO, "--remove-label", RUNNING, "--add-label", IN_REVIEW)
        gh("issue", "comment", str(number), "--repo", REPO, "--body",
           f"工厂产出 draft PR：{pr_url}\n\n门禁：npm run check 全绿；独立复核 PASS。等你审阅合并。")
        log("PR: " + pr_url)
        print(pr_url)
        return 0
    except Exception as exc:  # noqa: BLE001 - factory boundary
        fail(issue, str(exc), log_path)
        if worktree is not None:
            log("worktree kept for diagnosis: " + str(worktree))
        return 1
    finally:
        if LOG is not None:
            log("log: " + str(log_path))
            LOG.close()
            LOG = None


if __name__ == "__main__":
    sys.exit(main())
