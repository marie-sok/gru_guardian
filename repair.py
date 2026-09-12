from __future__ import annotations

import time

import aiohttp

from config import settings


class RepairEngine:
    def __init__(self) -> None:
        self._last_action_at: dict[str, float] = {}

    def _cooldown_ok(self, key: str) -> bool:
        last = self._last_action_at.get(key, 0.0)
        return time.time() - last >= settings.recovery_cooldown_seconds

    async def redeploy_render_service(self, service_id: str) -> tuple[bool, str]:
        if not settings.can_repair:
            return False, "Guardian is in Observe mode"
        if not settings.render_api_key:
            return False, "Render API key is not configured"
        if not self._cooldown_ok(service_id):
            return False, "Recovery cooldown is active"

        url = f"https://api.render.com/v1/services/{service_id}/deploys"
        headers = {
            "Authorization": f"Bearer {settings.render_api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json={"clearCache": False}) as response:
                if 200 <= response.status < 300:
                    self._last_action_at[service_id] = time.time()
                    return True, f"Redeploy requested for {service_id}"
                body = await response.text()
                return False, f"Render returned HTTP {response.status}: {body[:300]}"

    async def safe_repair(self, target: str) -> tuple[bool, str]:
        service_id = {
            "backend": settings.render_backend_service_id,
            "edge": settings.render_edge_service_id,
        }.get(target)
        if not service_id:
            return False, f"No Render service id configured for {target}"
        return await self.redeploy_render_service(service_id)
