"""Git operations: branch, commit, push, PR creation.

Handles git operations for the "View PR" delivery button: create branch,
commit, push, create PR via GitHub REST API.

Uses urllib.request (stdlib) instead of httpx/aiohttp to avoid adding
a dependency for a single GitHub API call. run_in_executor makes it async-safe.

Security: The PAT is NEVER written to .git/config. It's constructed in
memory, passed as a positional argument to git push, and discarded.
"""

import asyncio
import json
import logging
import os
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


class GitManager:
    """Manages git operations for project delivery."""

    def __init__(self, workspace_path: str, github_pat: str | None = None):
        self.workspace = workspace_path
        self.github_pat = github_pat

    async def create_feature_branch(
        self, project_name: str, branch_name: str,
    ) -> bool:
        """Create and checkout a feature branch."""
        project_dir = os.path.join(self.workspace, project_name)
        if not os.path.isdir(os.path.join(project_dir, ".git")):
            logger.warning("No git repo in %s", project_dir)
            return False

        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", "-b", branch_name,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("git checkout -b failed: %s", stderr.decode())
            return False
        return True

    async def commit_all(self, project_name: str, message: str) -> bool:
        """Stage all changes and commit."""
        project_dir = os.path.join(self.workspace, project_name)

        # git add -A
        proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # git commit
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", message,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            # Return code 1 with "nothing to commit" is not an error
            if b"nothing to commit" in stdout or b"nothing to commit" in stderr:
                logger.info("Nothing to commit")
                return True
            logger.error("git commit failed: %s", stderr.decode())
            return False
        return True

    async def push(self, project_name: str, branch_name: str) -> bool:
        """Push branch to origin."""
        if not self.github_pat:
            logger.error("Cannot push: GITHUB_PAT not configured")
            return False

        project_dir = os.path.join(self.workspace, project_name)

        env = os.environ.copy()
        env["GIT_ASKPASS"] = "echo"
        env["GIT_TERMINAL_PROMPT"] = "0"

        # Get the current remote URL
        proc = await asyncio.create_subprocess_exec(
            "git", "remote", "get-url", "origin",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        original_url = stdout.decode().strip()

        if not original_url:
            logger.error("No git remote 'origin' configured")
            return False

        # Construct authenticated URL — PAT in memory only, never on disk
        # https://github.com/user/repo.git -> https://PAT@github.com/user/repo.git
        auth_url = original_url.replace("https://", f"https://{self.github_pat}@")

        proc = await asyncio.create_subprocess_exec(
            "git", "push", auth_url, branch_name,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("git push failed: %s", stderr.decode()[:500])
            return False
        return True

    async def create_pull_request(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
        title: str,
        body: str,
        base: str = "main",
    ) -> str | None:
        """Create a GitHub PR via the REST API. Returns PR URL or None on failure."""
        if not self.github_pat:
            return None

        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls"
        data = json.dumps({
            "title": title,
            "head": branch_name,
            "base": base,
            "body": body,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.github_pat}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req),
            )
            result = json.loads(response.read().decode())
            pr_url = result.get("html_url", "")
            logger.info("PR created: %s", pr_url)
            return pr_url
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode() if e.fp else ""
            logger.error("GitHub API error %d: %s", e.code, resp_body[:500])
            return None
        except Exception as e:
            logger.error("PR creation failed: %s", e)
            return None

    async def get_repo_info(self, project_name: str) -> tuple[str, str] | None:
        """Extract repo_owner and repo_name from git remote URL.

        Returns (owner, repo) or None if not a GitHub remote.
        """
        project_dir = os.path.join(self.workspace, project_name)
        proc = await asyncio.create_subprocess_exec(
            "git", "remote", "get-url", "origin",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        url = stdout.decode().strip()

        if not url:
            return None

        # Parse https://github.com/owner/repo.git or git@github.com:owner/repo.git
        if "github.com" not in url:
            return None

        # HTTPS format
        if url.startswith("https://"):
            parts = url.replace("https://github.com/", "").rstrip(".git").split("/")
            if len(parts) >= 2:
                return parts[0], parts[1]

        # SSH format
        if url.startswith("git@"):
            parts = url.split(":")[-1].rstrip(".git").split("/")
            if len(parts) >= 2:
                return parts[0], parts[1]

        return None
