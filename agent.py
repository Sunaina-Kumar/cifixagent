import os
import requests
import zipfile
import io
from pathlib import Path
import subprocess
import re

# =========================
# Utilities
# =========================
def run_git(cmd):
    subprocess.run(cmd, check=True)


# =========================
# GitHub Tool
# =========================
class GitHubTool:
    def __init__(self):
        self.token = os.environ["GITHUB_TOKEN"]
        self.repo = os.environ["REPO"]
        self.run_id = os.environ["RUN_ID"]
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def get_ci_logs(self) -> str:
        url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}/logs"
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()

        zip_file = zipfile.ZipFile(io.BytesIO(r.content))
        logs = ""
        for name in zip_file.namelist():
            logs += zip_file.read(name).decode("utf-8", errors="ignore")
        return logs

    def get_pr_number(self):
        run_url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}"
        run = requests.get(run_url, headers=self.headers).json()
        prs = run.get("pull_requests", [])
        return prs[0]["number"] if prs else None

    def post_pr_comment(self, body: str):
        pr = self.get_pr_number()
        if not pr:
            print("No PR associated with this run.")
            return

        url = f"https://api.github.com/repos/{self.repo}/issues/{pr}/comments"
        requests.post(url, headers=self.headers, json={"body": body})

    def get_pr_comments(self):
        pr = self.get_pr_number()
        if not pr:
            return []

        url = f"https://api.github.com/repos/{self.repo}/issues/{pr}/comments"
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()
        return [c["body"] for c in r.json()]


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
# Core Agent
# =========================
class CIFixAgent:
    def __init__(self):
        self.github = GitHubTool()
        self.fs = FilesystemTool()

    # ---------- DIAGNOSIS ----------
    def diagnose(self, logs: str):
        match = re.search(r"No module named ['\"]([^'\"]+)['\"]", logs)
        if not match:
            return None

        return match.group(1)

    # ---------- APPROVAL CHECK ----------
    def has_human_approval(self):
        comments = self.github.get_pr_comments()
        return any(c.strip().upper() == "APPROVE" for c in comments)

    # ---------- APPLY FIX ----------
    def apply_fix(self, dep: str):
        self.fs.add_dependency(dep)

        run_git(["git", "config", "user.name", "ci-janitor-bot"])
        run_git(["git", "config", "user.email", "ci-janitor@users.noreply.github.com"])

        run_git(["git", "add", "requirements.txt"])
        run_git(["git", "commit", "-m", f"ci-fix: add missing dependency {dep}"])

        branch = os.environ["PR_BRANCH"]
        run_git(["git", "push", "origin", f"HEAD:{branch}"])

    # ---------- MAIN ----------
    def run(self):
        logs = self.github.get_ci_logs()
        dep = self.diagnose(logs)

        if not dep:
            print("No actionable error found.")
            return

        # If not approved → diagnose only
        if not self.has_human_approval():
            self.github.post_pr_comment(
                f"""❌ **CI Failure Detected**

**Reason**
• Python module `{dep}` is imported but not installed

**Root Cause**
• `{dep}` is missing from `requirements.txt`

**Suggested Fix**
• Add `{dep}` to `requirements.txt`

✋ **Approval Required**
Reply with **APPROVE** to apply this fix.
"""
            )
            print("Awaiting human approval.")
            return

        # Approved → apply fix
        self.apply_fix(dep)
        self.github.post_pr_comment(
            f"""✅ **Fix Applied**

**Change Made**
• Added `{dep}` to `requirements.txt`

**Result**
• CI re-triggered automatically
"""
        )
        print(f"Applied approved fix: {dep}")


# =========================
# Entry
# =========================
if __name__ == "__main__":
    CIFixAgent().run()
