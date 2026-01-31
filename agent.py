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
        self.token = os.environ.get("GITHUB_TOKEN")
        self.repo = os.environ.get("REPO")
        self.run_id = os.environ.get("RUN_ID")
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
        # In workflow_run, we must find the PR number from the API
        run_url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}"
        run_data = requests.get(run_url, headers=self.headers).json()
        
        if not run_data.get("pull_requests"):
            print("No PR linked to this run.")
            return

        pr_number = run_data["pull_requests"][0]["number"]
        url = f"https://api.github.com/repos/{self.repo}/issues/{pr_number}/comments"
        requests.post(url, headers=self.headers, json={"body": body})

class CIFixAgent:
    def __init__(self):
        self.github = GitHubTool()

    def parse_logs(self, logs: str):
        # Specific search for: app/__init__.py:1: in <module> ... E ModuleNotFoundError: No module named 'requests'
        file_pattern = r'([\w/]+\.py):\d+: in <module>'
        module_pattern = r"E\s+ModuleNotFoundError: No module named ['\"](.*?)['\"]"
        
        files = re.findall(file_pattern, logs)
        module = re.search(module_pattern, logs)
        
        return {
            "file": files[-1] if files else "Unknown source",
            "module": module.group(1) if module else None
        }

    def run_diagnosis(self):
        logs = self.github.get_ci_logs()
        info = self.parse_logs(logs)

        if info["module"]:
            comment = (
                f"🤖 **CI Janitor: Issue Detected**\n\n"
                f"- **Missing Module:** `{info['module']}`\n"
                f"- **Found in:** `{info['file']}`\n\n"
                f"Should I add this to `requirements.txt`? Reply with **APPROVE**."
            )
            self.github.post_pr_comment(comment)
            print(f"Commented on PR for module: {info['module']}")
        else:
            print("Could not diagnose the specific ModuleNotFoundError.")

    def apply_fix(self, module_name):
        req_file = Path("requirements.txt")
        content = req_file.read_text() if req_file.exists() else ""
        
        if module_name not in content:
            new_content = content.strip() + f"\n{module_name}\n"
            req_file.write_text(new_content)
        
        run_git(["git", "config", "user.name", "ci-janitor-bot"])
        run_git(["git", "config", "user.email", "ci-janitor@users.noreply.github.com"])
        run_git(["git", "add", "requirements.txt"])
        run_git(["git", "commit", "-m", f"ci-fix: add missing dependency {module_name}"])
        
        branch = os.environ.get("PR_BRANCH")
        run_git(["git", "push", "origin", f"HEAD:{branch}"])

if __name__ == "__main__":
    agent = CIFixAgent()
    if os.environ.get("AGENT_MODE") == "APPLY":
        agent.apply_fix(os.environ.get("MODULE_NAME"))
    else:
        agent.run_diagnosis()
