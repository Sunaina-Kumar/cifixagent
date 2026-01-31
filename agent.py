import os
import requests
import zipfile
import io
from pathlib import Path
import re
import subprocess

def run_git(cmd):
    subprocess.run(cmd, check=True)

# =========================
# GitHub Tool
# =========================
class GitHubTool:
    def __init__(self):
        self.token = os.environ["GITHUB_TOKEN"]
        self.repo = os.environ["REPO"]
        self.run_id = os.environ.get("RUN_ID")  # allow issue_comment trigger
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json"
        }

    def get_ci_logs(self) -> str:
        if not self.run_id:
            return ""

        url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}/logs"
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()

        zip_file = zipfile.ZipFile(io.BytesIO(r.content))
        logs = ""
        for name in zip_file.namelist():
            logs += zip_file.read(name).decode("utf-8", errors="ignore")
        return logs

    def get_pr_number(self):
        if not self.run_id:
            return None

        run_url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}"
        run = requests.get(run_url, headers=self.headers).json()
        prs = run.get("pull_requests", [])
        return prs[0]["number"] if prs else None

    def get_pr_comments(self):
        pr = self.get_pr_number()
        if not pr:
            return []

        url = f"https://api.github.com/repos/{self.repo}/issues/{pr}/comments"
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()
        return [c["body"] for c in r.json()]

    def post_pr_comment(self, body: str):
        pr = self.get_pr_number()
        if not pr:
            return

        url = f"https://api.github.com/repos/{self.repo}/issues/{pr}/comments"
        requests.post(url, headers=self.headers, json={"body": body})

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

    def has_approval(self):
        for c in self.github.get_pr_comments():
            if "APPROVE" in c.upper():
                return True
        return False

    def diagnose(self, logs: str):
        match = re.search(r"No module named ['\"]([^'\"]+)['\"]", logs)
        if not match:
            return None, None

        dep = match.group(1)

        file_match = re.search(r'File "([^"]+)", line', logs)
        file = file_match.group(1) if file_match else "unknown"

        return dep, file

    def act(self):
        logs = self.github.get_ci_logs()
        dep, file = self.diagnose(logs)

        if not dep:
            print("No actionable error found.")
            return

        # ---- DIAGNOSE ONLY ----
        if not self.has_approval():
            self.github.post_pr_comment(
                f"""❌ **CI Failure Detected**

**Reason**
• Python module `{dep}` is missing

**Detected In**
• `{file}`

**Suggested Fix**
• Add `{dep}` to `requirements.txt`

✋ **Approval Required**
Reply with **APPROVE** to apply this fix.
"""
            )
            print("Posted diagnosis, awaiting approval.")
            return

        # ---- APPLY FIX ----
        self.fs.add_dependency(dep)

        run_git(["git", "config", "user.name", "ci-janitor-bot"])
        run_git(["git", "config", "user.email", "ci-janitor@users.noreply.github.com"])

        run_git(["git", "add", "requirements.txt"])
        run_git(["git", "commit", "-m", f"ci-fix: add missing dependency {dep}"])

        branch = os.environ.get("PR_BRANCH")
        run_git(["git", "push", "origin", f"HEAD:{branch}"])

        self.github.post_pr_comment(
            f"""✅ **Fix Applied**

**Change Made**
• Added `{dep}` to `requirements.txt`

**Result**
• CI automatically re-triggered
"""
        )

    def run(self):
        self.act()

# =========================
# Entry
# =========================
if __name__ == "__main__":
    CIFixAgent().run()
