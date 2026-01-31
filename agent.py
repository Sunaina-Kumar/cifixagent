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

    def get_pr_info(self):
        run_url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}"
        run = requests.get(run_url, headers=self.headers).json()
        if not run.get("pull_requests"):
            return None
        return run["pull_requests"][0]

    def post_pr_comment(self, pr_number, body: str):
        comment_url = f"https://api.github.com/repos/{self.repo}/issues/{pr_number}/comments"
        requests.post(comment_url, headers=self.headers, json={"body": body})

class CIFixAgent:
    def __init__(self):
        self.github = GitHubTool()

    def parse_logs(self, logs: str):
        # Regex to find: File "path/to/file.py", line X, in <module> \n ModuleNotFoundError: No module named 'xyz'
        pattern = r'File "(.*?)".*?ModuleNotFoundError: No module named [\'"](.*?)[\'"]'
        match = re.search(pattern, logs, re.DOTALL)
        
        if match:
            return {
                "file": match.group(1),
                "module": match.group(2)
            }
        return None

    def run_diagnosis(self):
        pr = self.github.get_pr_info()
        if not pr: return

        logs = self.github.get_ci_logs()
        info = self.parse_logs(logs)

        if info:
            comment = (
                f"🤖 **CI Janitor Diagnosis**\n\n"
                f"**Missing Module:** `{info['module']}`\n"
                f"**Importing File:** `{info['file']}`\n\n"
                f"Should I add `{info['module']}` to `requirements.txt`? \n"
                f"Reply with **APPROVE** to apply fix."
            )
            self.github.post_pr_comment(pr['number'], comment)
            print(f"Diagnosis posted for module: {info['module']}")
        else:
            print("No ModuleNotFoundError detected.")

    def apply_fix(self, module_name):
        req = Path("requirements.txt")
        content = req.read_text() if req.exists() else ""
        
        if module_name not in content:
            with open(req, "a") as f:
                f.write(f"\n{module_name}")
        
        # Git operations
        run_git(["git", "config", "user.name", "ci-janitor-bot"])
        run_git(["git", "config", "user.email", "ci-janitor@users.noreply.github.com"])
        run_git(["git", "add", "requirements.txt"])
        run_git(["git", "commit", "-m", f"ci-fix: add {module_name}"])
        
        branch = os.environ.get("PR_BRANCH")
        run_git(["git", "push", "origin", f"HEAD:{branch}"])
        print(f"Applied fix for {module_name}")

if __name__ == "__main__":
    agent = CIFixAgent()
    # If triggered by a comment (handled by env var)
    if os.environ.get("AGENT_MODE") == "APPLY":
        module = os.environ.get("MODULE_TO_FIX")
        agent.apply_fix(module)
    else:
        agent.run_diagnosis()
