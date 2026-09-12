from __future__ import annotations

import aiohttp

from config import settings


class GitHubOps:
    API = "https://api.github.com"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gru-guardian",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        return headers

    async def latest_commit(self) -> tuple[bool, dict | str]:
        repo = settings.github_repo
        branch = settings.github_release_branch
        url = f"{self.API}/repos/{repo}/commits/{branch}"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=self._headers()) as response:
                body = await response.json(content_type=None)
                if response.status >= 300:
                    return False, f"GitHub HTTP {response.status}: {str(body)[:500]}"
        return True, body

    async def ci_summary(self) -> tuple[bool, str]:
        ok, commit_or_error = await self.latest_commit()
        if not ok:
            return False, str(commit_or_error)
        commit = commit_or_error
        sha = commit.get("sha", "")
        if not sha:
            return False, "GitHub returned no commit SHA"

        url = f"{self.API}/repos/{settings.github_repo}/actions/runs"
        params = {"branch": settings.github_release_branch, "per_page": 20}
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=self._headers(), params=params) as response:
                body = await response.json(content_type=None)
                if response.status >= 300:
                    return False, f"GitHub Actions HTTP {response.status}: {str(body)[:500]}"

        runs = body.get("workflow_runs", []) if isinstance(body, dict) else []
        matching = [r for r in runs if r.get("head_sha") == sha]
        if not matching:
            matching = runs[:8]

        message = ((commit.get("commit") or {}).get("message") or "").splitlines()[0][:90]
        lines = [
            f"GitHub CI • {settings.github_release_branch}",
            f"Commit {sha[:8]} • {message or '-'}",
        ]
        if not matching:
            lines.append("• No workflow runs found.")
            return True, "\n".join(lines)

        for run in matching[:10]:
            name = run.get("name") or "workflow"
            status = run.get("status") or "unknown"
            conclusion = run.get("conclusion") or "pending"
            icon = "🟢" if conclusion == "success" else "🔴" if conclusion in {"failure", "cancelled", "timed_out", "action_required"} else "🟡"
            lines.append(f"{icon} {name}: {status}/{conclusion}")
        return True, "\n".join(lines)[:3900]

    async def failed_jobs_context(self) -> str:
        ok, commit_or_error = await self.latest_commit()
        if not ok:
            return str(commit_or_error)
        sha = commit_or_error.get("sha", "")
        if not sha:
            return "No commit SHA available."

        runs_url = f"{self.API}/repos/{settings.github_repo}/actions/runs"
        params = {"branch": settings.github_release_branch, "per_page": 20}
        timeout = aiohttp.ClientTimeout(total=20)
        excerpts: list[str] = []
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(runs_url, headers=self._headers(), params=params) as response:
                body = await response.json(content_type=None)
                if response.status >= 300:
                    return f"GitHub Actions HTTP {response.status}"
            runs = [r for r in body.get("workflow_runs", []) if r.get("head_sha") == sha and r.get("conclusion") not in {None, "success"}]
            for run in runs[:4]:
                jobs_url = run.get("jobs_url")
                if not jobs_url:
                    continue
                async with session.get(jobs_url, headers=self._headers()) as response:
                    jobs_body = await response.json(content_type=None)
                    if response.status >= 300:
                        continue
                for job in jobs_body.get("jobs", [])[:8]:
                    if job.get("conclusion") == "success":
                        continue
                    failed_steps = [s.get("name", "step") for s in job.get("steps", []) if s.get("conclusion") == "failure"]
                    excerpts.append(
                        f"workflow={run.get('name')} job={job.get('name')} conclusion={job.get('conclusion')} failed_steps={', '.join(failed_steps) or '-'}"
                    )
        return "\n".join(excerpts)[:5000] if excerpts else "No failed GitHub Actions jobs detected for the current release commit."
