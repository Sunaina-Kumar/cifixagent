import os
import io
import re
import zipfile
import subprocess
from pathlib import Path
from typing import Optional, Tuple

import requests


# =========================
# Helpers
# =========================
def run_git(cmd):
    subprocess.run(cmd, check=True)


def commit_and_push_fix(dep: str, branch: str):
    run_git(["git", "config", "user.name", "ci-janitor-bot"])
    run_git(["git", "config", "user.email", "ci-janitor@users.noreply.github.com"])

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True
    ).stdout.strip()

    if not status:
        print("No changes detected, skipping commit.")
        return

    run_git(["git", "add", "requirements.txt"])
    run_git(["git", "commit", "-m", f"ci-fix: add missing dependency {dep}"])
    run_git(["git", "push", "origin", f"HEAD:{branch}"])


# =========================
# Log Analysis
# =========================
def find_missing_dependency(logs: str) -> Optional[str]:
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", logs)
    return m.group(1).strip() if m else None


def find_python_version_conflict(logs: str) -> Optional[Tuple[str, str]]:
    """
    Detect errors like:
    - Package X requires Python < 3.9
    - Requires-Python >=3.8,<3.10
    """
    patterns = [
        r"Package\s+([^\s]+)\s+requires Python\s+([^\n]+)",
        r"Requires-Python\s+([^\n]+)"
    ]

    for p in patterns:
        m = re.search(p, logs)
        if m:
            if len(m.groups()) == 2:
                return m.group(1), m.group(2)
            else:
                return "unknown-package", m.group(1)

    return None


def make_log_excerpt(logs: str, max_lines: int = 30, max_chars: int = 1800) -> str:
    lines = logs.splitlines()
    idx = next((i for i, l in enumerate(lines) if "ERROR" in l or "ModuleNotFoundError" in l), 0)

    snippet = lines[max(0, idx - 10): idx + 10]
    text = "\n".join(snippet).strip()

    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"

    return text


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

    def _get_json(self, url: str) -> dict:
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def _post_json(self, url: str, payload: dict):
        r = requests.post(url, headers=self.headers, json=payload)
        r.raise_for_status()

    def get_ci_logs(self) -> str:
        run_id = self.run_id

        if not run_id:
            pr_url = f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}"
            pr = self._get_json(pr_url)
            head_sha = pr["head"]["sha"]

            runs_url = f"https://api.github.com/repos/{self.repo}/actions/runs?per_page=50"
            runs = self._get_json(runs_url)["workflow_runs"]

            for r in runs:
                if r["head_sha"] == head_sha and r["conclusion"] == "failure":
                    run_id = r["id"]
                    break

            if not run_id:
                raise RuntimeError("No failed CI run found for PR.")

            self.run_id = str(run_id)

        url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}/logs"
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()

        z = zipfile.ZipFile(io.BytesIO(r.content))
        return "".join(z.read(n).decode("utf-8", errors="ignore") for n in z.namelist())

    def get_pr_number(self) -> int:
        if self.pr_number:
            return int(self.pr_number)

        run = self._get_json(
            f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}"
        )

        return int(run["pull_requests"][0]["number"])

    def post_pr_comment(self, body: str):
        pr = self.get_pr_number()
        url = f"https://api.github.com/repos/{self.repo}/issues/{pr}/comments"
        self._post_json(url, {"body": body})


# =========================
# Filesystem Tool
# =========================
class FilesystemTool:
    def add_dependency(self, dep: str):
        req = Path("requirements.txt")
        content = req.read_text().splitlines()

        if dep not in content:
            content.append(dep)
            req.write_text("\n".join(content) + "\n")


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

        # ---- LEVEL 1: Missing dependency ----
        dep = find_missing_dependency(logs)
        if dep:
            if not approved:
                self.github.post_pr_comment(
                    f"""🤖 **CI Janitor**

**Issue detected**
• Missing Python dependency `{dep}`

**Proposed fix**
• Add `{dep}` to `requirements.txt`

Reply with `/ci-janitor approve` to apply this fix.
"""
                )
                return

            self.fs.add_dependency(dep)
            branch = os.environ.get("PR_BRANCH") or os.environ.get("GITHUB_HEAD_REF")
            commit_and_push_fix(dep, branch)
            self.github.post_pr_comment(f"✅ Added `{dep}` to `requirements.txt`.")
            return

        # ---- LEVEL 2: Python version conflict ----
        conflict = find_python_version_conflict(logs)
        if conflict:
            pkg, constraint = conflict
            py_ver = os.environ.get("PYTHON_VERSION", "unknown")

            self.github.post_pr_comment(
                f"""🤖 **CI Janitor — Version Conflict Detected**

**Problem**
• Package `{pk
