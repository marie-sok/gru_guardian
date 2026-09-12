from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiohttp

from config import settings


class RenderOps:
    API = "https://api.render.com/v1"

    def _headers(self) -> dict[str, str]:
        if not settings.render_api_key:
            return {}
        return {
            "Authorization": f"Bearer {settings.render_api_key}",
            "Accept": "application/json",
        }

    def configured(self) -> bool:
        return bool(settings.render_api_key and settings.render_workspace_id)

    def service_id(self, target: str) -> str | None:
        return {
            "backend": settings.render_backend_service_id,
            "edge": settings.render_edge_service_id,
        }.get(target)

    async def deploys(self, target: str, limit: int = 5) -> tuple[bool, str]:
        service_id = self.service_id(target)
        if not settings.render_api_key:
            return False, "Render API key is not configured"
        if not service_id:
            return False, f"Render service id is not configured for {target}"

        url = f"{self.API}/services/{service_id}/deploys"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=self._headers(), params={"limit": max(1, min(limit, 20))}) as response:
                body = await response.json(content_type=None)
                if response.status >= 300:
                    return False, f"Render HTTP {response.status}: {str(body)[:500]}"

        rows = body if isinstance(body, list) else body.get("deploys", []) if isinstance(body, dict) else []
        lines = [f"Render deploys • {target}"]
        for item in rows[:limit]:
            deploy = item.get("deploy", item) if isinstance(item, dict) else {}
            commit = deploy.get("commit") or {}
            sha = (commit.get("id") or "")[:8]
            message = (commit.get("message") or "").splitlines()[0][:70]
            lines.append(f"• {deploy.get('status', 'unknown')} • {sha or '-'} • {message or deploy.get('id', '-')}")
        if len(lines) == 1:
            lines.append("No deploys returned.")
        return True, "\n".join(lines)

    async def logs(self, target: str, limit: int = 30, minutes: int = 60) -> tuple[bool, str]:
        service_id = self.service_id(target)
        if not settings.render_api_key:
            return False, "Render API key is not configured"
        if not settings.render_workspace_id:
            return False, "Render workspace id is not configured"
        if not service_id:
            return False, f"Render service id is not configured for {target}"

        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=max(5, min(minutes, 1440)))
        params = [
            ("ownerId", settings.render_workspace_id),
            ("resource", service_id),
            ("startTime", start.isoformat().replace("+00:00", "Z")),
            ("endTime", end.isoformat().replace("+00:00", "Z")),
            ("direction", "backward"),
            ("limit", str(max(1, min(limit, 100)))),
        ]
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.API}/logs", headers=self._headers(), params=params) as response:
                body = await response.json(content_type=None)
                if response.status >= 300:
                    return False, f"Render logs HTTP {response.status}: {str(body)[:500]}"

        entries = body.get("logs", []) if isinstance(body, dict) else body if isinstance(body, list) else []
        lines = [f"Render logs • {target} • last {minutes}m"]
        for entry in entries[:limit]:
            if not isinstance(entry, dict):
                continue
            ts = str(entry.get("timestamp") or entry.get("time") or "")
            level = str(entry.get("level") or "info").upper()
            text = str(entry.get("message") or entry.get("text") or entry.get("log") or "")
            text = " ".join(text.split())[:260]
            if text:
                lines.append(f"• {ts[-13:-5] if ts else '--:--'} {level}: {text}")
        if len(lines) == 1:
            lines.append("No log lines returned.")
        return True, "\n".join(lines)[:3900]
