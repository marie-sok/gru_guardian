from __future__ import annotations

import base64
import re
from urllib.parse import quote

import aiohttp

from ai_doctor import AIDoctor
from config import settings


SAFE_PATH = re.compile(r"^[A-Za-z0-9._/\- ]{1,240}$")


class PRFixer:
    API = "https://api.github.com"

    def __init__(self, doctor: AIDoctor) -> None:
        self.doctor = doctor

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gru-guardian",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        return headers

    async def create_single_file_draft_pr(self, request_id: int, path: str, instruction: str) -> tuple[bool, str]:
        if not settings.can_code:
            return False, "Guardian must be in Code or Production mode to create code PRs"
        if not settings.github_token:
            return False, "GitHub token is not configured"
        if not settings.ai_key:
            return False, "AI key is not configured"
        if not SAFE_PATH.fullmatch(path) or path.startswith("/") or ".." in path.split("/"):
            return False, "Unsafe repository path"
        if path.startswith((".github/workflows/", ".github/actions/")):
            return False, "Workflow files are blocked from autonomous PR generation"
        if any(token in path.lower() for token in ("secret", ".env", "credential", "private_key")):
            return False, "Secret-like paths are blocked from autonomous PR generation"

        repo = settings.github_repo
        base_branch = settings.github_release_branch
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            ref_url = f"{self.API}/repos/{repo}/git/ref/heads/{quote(base_branch, safe='/')}"
            async with session.get(ref_url, headers=self._headers()) as response:
                base_ref = await response.json(content_type=None)
                if response.status >= 300:
                    return False, f"GitHub base ref HTTP {response.status}: {str(base_ref)[:400]}"
            base_sha = ((base_ref.get("object") or {}).get("sha") if isinstance(base_ref, dict) else None)
            if not base_sha:
                return False, "Could not resolve base branch SHA"

            content_url = f"{self.API}/repos/{repo}/contents/{quote(path, safe='/')}"
            async with session.get(content_url, headers=self._headers(), params={"ref": base_branch}) as response:
                file_obj = await response.json(content_type=None)
                if response.status >= 300:
                    return False, f"GitHub file HTTP {response.status}: {str(file_obj)[:400]}"

            if not isinstance(file_obj, dict) or file_obj.get("type") != "file":
                return False, "Requested path is not a regular file"
            if file_obj.get("size", 0) > 120000:
                return False, "File is too large for guarded single-file PR generation"
            encoded = file_obj.get("content") or ""
            try:
                current_content = base64.b64decode(encoded).decode("utf-8")
            except Exception:
                return False, "Target file is not UTF-8 text"

            ok, replacement = await self.doctor.rewrite_file(path, instruction, current_content)
            if not ok:
                return False, replacement

            branch = f"guardian/fix-{request_id}"
            create_ref_url = f"{self.API}/repos/{repo}/git/refs"
            async with session.post(
                create_ref_url,
                headers=self._headers(),
                json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            ) as response:
                ref_body = await response.json(content_type=None)
                if response.status == 422:
                    return False, f"Branch {branch} already exists; request id must be unique"
                if response.status >= 300:
                    return False, f"GitHub create branch HTTP {response.status}: {str(ref_body)[:400]}"

            payload = {
                "message": f"guardian: fix request #{request_id}",
                "content": base64.b64encode(replacement.encode("utf-8")).decode("ascii"),
                "sha": file_obj.get("sha"),
                "branch": branch,
            }
            async with session.put(content_url, headers=self._headers(), json=payload) as response:
                update_body = await response.json(content_type=None)
                if response.status >= 300:
                    return False, f"GitHub file update HTTP {response.status}: {str(update_body)[:400]}"

            pr_url = f"{self.API}/repos/{repo}/pulls"
            pr_payload = {
                "title": f"Guardian fix #{request_id}: {instruction[:80]}",
                "head": branch,
                "base": base_branch,
                "body": (
                    f"Automated draft PR prepared by GRU Guardian for fix request #{request_id}.\n\n"
                    f"Target file: `{path}`\n\n"
                    f"Requested change:\n{instruction[:3000]}\n\n"
                    "Safety: single-file scope only; no merge or production action was performed. Review and CI are required before merge."
                ),
                "draft": True,
            }
            async with session.post(pr_url, headers=self._headers(), json=pr_payload) as response:
                pr_body = await response.json(content_type=None)
                if response.status >= 300:
                    return False, f"GitHub PR HTTP {response.status}: {str(pr_body)[:400]}"

        number = pr_body.get("number")
        url = pr_body.get("html_url") or ""
        return True, f"Draft PR #{number} created on {branch}. Review + CI required before merge.\n{url}"
