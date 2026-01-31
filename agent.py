import os
import io
import re
import zipfile
import subprocess
from pathlib import Path
from typing import Optional

import requests


# =========================
# Helpers
# =========================
def run_git(cmd):
    subprocess.run(cmd, check=True)


def commit_and_push_fix(dep: str, branch: str):
    run_git(["git", "config", "user.name", "ci-janitor-bot"])
    run_git(["git", "config", "user.email", "ci-janitor@users.noreply.github.com"])
    run_git(["git", "add", "requirements.txt"])
    run_git(["git", "commit", "-m", f"ci-fix: add missing dependency {dep}"])
    run_git(["git", "push", "origin", f"HEAD:{branch}"])


# =========================
# Log analysis
# =========================
def find_missing_dependency(logs: str) -> Optional[str]:
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", logs)
    return m.group(1).strip() if m else None


def find_python_constraint(logs: str) -> Optional[str]:
    patterns = [
        r"Requires-Python\s*([^\s,;]+)",
        r"requires Python\s*([^\n]+)",
    ]
    for p in patterns:
        m = re.search(p, logs, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def make_log_excerpt(logs: str, max_lines: int = 25) -> str:
    lines = logs.splitlines()
    for i, l in enumerate(lines):
        if "ModuleNotFoundError" in l or "Requires-Python" in l:
            start = max(0, i - 8)
            end = min(len(lines), i + 8)
            return "\n".join(lines[start:end])
    return "\n".join(lines[:max_lines])


# =========================
# GitHub Tool
# =========================
class GitHubTool:
    def __init__(self):
        self.token = os.environ["GITHUB_TOKEN"]
        self.repo = os.environ["REPO"]
        self.run_id = os.environ.get("RUN_ID")
        self.pr_number = os.environ.get("PR_NUMBER")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def _get_json(self, url: str):
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def post_pr_comment(self, body: str):
        pr = self.get_pr_number()
        url = f"https://api.github.com/repos/{self.repo}/issues/{pr}/comments"
        requests.post(url, headers=self.headers, json={"body": body})

    def get_pr_number(self) -> int:
        if self.pr_number:
            return int(self.pr_number)
        run = self._get_json(f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}")
        return int(run["pull_requests"][0]["number"])

    def get_ci_logs(self) -> str:
        run_id = self.run_id
        if not run_id:
            pr = self._get_json(f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}")
            sha = pr["head"]["sha"]
            runs = self._get_json(f"https://api.github.com/repos/{self.repo}/actions/runs")["workflow_runs"]
            for r in runs:
                if r["head_sha"] == sha and r["conclusion"] == "failure":
                    run_id = r["id"]
                    break
        url = f"https://api.github.com/repos/{self.repo}/actions/runs/{run_id}/logs"
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        return "".join(z.read(n).decode("utf-8", errors="ignore") for n in z.namelist())


# =========================
# Filesystem Tool
# =========================
class FilesystemTool:
    def add_dependency(self, dep: str):
        req = Path("requirements.txt")
        lines = req.read_text().splitlines()
        if dep not in lines:
            lines.append(dep)
            req.write_text("\n".join(lines) + "\n")


# =========================
# Agent Core
# =========================
class CIFixAgent:
    def __init__(self):
        self.github = GitHubTool()
        self.fs = FilesystemTool()

    def run(self):
        logs = self.github.get_ci_logs()
        approved = os.environ.get("CI_JANITOR_APPROVED") == "1"
        comment_body = os.environ.get("COMMENT_BODY", "").lower()
        py_version = os.environ.get("PYTHON_VERSION", "unknown")

        # -------- Missing dependency (UNCHANGED) --------
        dep = find_missing_dependency(logs)
        if dep:
            if not approved:
                self.github.post_pr_comment(
                    f"""🤖 **CI Janitor**

Missing dependency `{dep}`.

**Proposed change**
• Add `{dep}` to `requirements.txt`

Reply with `/ci-janitor approve` to apply.
"""
                )
                return

            if "/ci-janitor approve" in comment_body:
                branch = os.environ.get("PR_BRANCH")
                self.fs.add_dependency(dep)
                commit_and_push_fix(dep, branch)
                self.github.post_pr_comment(f"✅ Added `{dep}` to `requirements.txt`.")
                return

        # -------- Python version conflict (NEW) --------
        constraint = find_python_constraint(logs)
        if constraint:
            if not approved:
                self.github.post_pr_comment(
                    f"""🤖 **CI Janitor — Python Version Conflict**

A dependency requires Python `{constraint}`  
CI is currently using Python `{py_version}`.

**Proposed change**
• Update CI Python version to a compatible release.

Reply with `/ci-janitor approve-python` to apply.
"""
                )
                return

            if "/ci-janitor approve-python" in comment_body:
                self.github.post_pr_comment(
                    f"""⚠️ **Python Version Change Approved**

Please update `actions/setup-python` to a version compatible with:
• `{constraint}`

(No automatic change was made.)
"""
                )
                return

        self.github.post_pr_comment("🤖 CI Janitor: CI failed, but no known fix was detected.")


# =========================
# Entry Point
# =========================
if __name__ == "__main__":
    CIFixAgent().run()
