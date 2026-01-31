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


# =========================
# GitHub Tool (HARDENED)
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

    def _get(self, url):
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def post_pr_comment(self, body: str):
        pr = self.get_pr_number()
        url = f"https://api.github.com/repos/{self.repo}/issues/{pr}/comments"
        requests.post(url, headers=self.headers, json={"body": body})

    def get_pr_number(self) -> int:
        # 1️⃣ explicit PR_NUMBER
        if self.pr_number:
            return int(self.pr_number)

        # 2️⃣ workflow_run pull_requests
        run = self._get(f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}")
        prs = run.get("pull_requests", [])
        if prs:
            return int(prs[0]["number"])

        # 3️⃣ fallback: search PR by head SHA
        sha = run["head_sha"]
        prs = self._get(
            f"https://api.github.com/repos/{self.repo}/pulls?state=open&per_page=50"
        )
        for pr in prs:
            if pr["head"]["sha"] == sha:
                return int(pr["number"])

        raise RuntimeError("CI Janitor: could not determine PR number")

    def get_ci_logs(self) -> str:
        url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}/logs"
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
        py_version = os.environ.get("PYTHON_VERSION", "unknown")

        # ---- Missing dependency (UNCHANGED) ----
        dep = find_missing_dependency(logs)
        if dep:
            if not approved:
                self.github.post_pr_comment(
                    f"""🤖 **CI Janitor**

**Error**
• Missing Python dependency `{dep}`

**Proposed fix**
• Add `{dep}` to `requirements.txt`

Reply with `/ci-janitor approve` to apply.
"""
                )
                return

            branch = os.environ.get("PR_BRANCH")
            self.fs.add_dependency(dep)
            commit_and_push_fix(dep, branch)
            self.github.post_pr_comment(f"✅ Added `{dep}` to `requirements.txt`.")
            return

        # ---- Python version conflict (LEVEL 2) ----
        constraint = find_python_constraint(logs)
        if constraint:
            self.github.post_pr_comment(
                f"""🤖 **CI Janitor — Python Version Conflict**

A dependency requires Python `{constraint}`  
CI is running Python `{py_version}`

⚠️ No automatic fix applied.
"""
            )
            return

        # ---- Fallback ----
        self.github.post_pr_comment(
            "🤖 CI Janitor: CI failed, but no supported fix was detected."
        )


# =========================
# Entry
# =========================
if __name__ == "__main__":
    CIFixAgent().run()
