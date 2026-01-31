import os
import requests
import zipfile
import io
import subprocess
import re
from pathlib import Path

def run_git(cmd):
    subprocess.run(cmd, check=True)

class GitHubTool:
    def __init__(self):
        self.token = os.environ["GITHUB_TOKEN"]
        self.repo = os.environ["REPO"]
        self.run_id = os.environ["RUN_ID"]
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json"
        }

    def get_ci_logs(self) -> str:
        url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}/logs"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        zip_file = zipfile.ZipFile(io.BytesIO(response.content))
        logs = ""
        for name in zip_file.namelist():
            logs += zip_file.read(name).decode("utf-8", errors="ignore")
        return logs

    def post_pr_comment(self, body: str):
        run_url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}"
        run = requests.get(run_url, headers=self.headers).json()
        if not run.get("pull_requests"):
            return
        pr_number = run["pull_requests"][0]["number"]
        comment_url = f"https://api.github.com/repos/{self.repo}/issues/{pr_number}/comments"
        requests.post(comment_url, headers=self.headers, json={"body": body})

class CIFixAgent:
    def __init__(self):
        self.github = GitHubTool()

    def parse_logs(self, logs: str):
        """
        Extracts module and the file that triggered the error.
        Matches: app/__init__.py:1: in <module> ... ModuleNotFoundError: No module named 'requests'
        """
        # Look for the last file mentioned before the error
        file_match = re.findall(r'([\w/]+\.py):\d+: in <module>', logs)
        module_match = re.search(r"ModuleNotFoundError: No module named ['\"](.*?)['\"]", logs)
        
        return {
            "file": file_match[-1] if file_match else "Unknown",
            "module": module_match.group(1) if module_match else None
        }

    def diagnose(self):
        logs = self.github.get_ci_logs()
        info = self.parse_logs(logs)

        if info["module"]:
            comment = (
                f"### 🤖 CI Janitor Diagnosis\n"
                f"- **Missing Module:** `{info['module']}`\n"
                f"- **Importing File:** `{info['file']}`\n\n"
                f"Reply with **APPROVE** to add this to `requirements.txt`."
            )
            self.github.post_pr_comment(comment)
        else:
            print("No actionable error found.")

    def apply_fix(self, module_name):
        req = Path("requirements.txt")
        content = req.read_text() if req.exists() else ""
        if module_name not in content:
            req.write_text(content.strip() + f"\n{module_name}\n")
        
        run_git(["git", "config", "user.name", "ci-janitor-bot"])
        run_git(["git", "config", "user.email", "ci-janitor@users.noreply.github.com"])
        run_git(["git", "add", "requirements.txt"])
        run_git(["git", "commit", "-m", f"ci-fix: add missing dependency {module_name}"])
        
        branch = os.environ.get("PR_BRANCH")
        run_git(["git", "push", "origin", f"HEAD:{branch}"])

if __name__ == "__main__":
    agent = CIFixAgent()
    mode = os.environ.get("AGENT_MODE")
    if mode == "APPLY":
        agent.apply_fix(os.environ.get("MODULE_NAME"))
    else:
        agent.diagnose()
