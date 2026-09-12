from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import aiohttp

from config import settings


@dataclass(slots=True)
class ProbeResult:
    target: str
    ok: bool
    status: int | None
    latency_ms: int | None
    error: str | None = None


class HealthMonitor:
    def __init__(self) -> None:
        self._failures: dict[str, int] = {"backend": 0, "edge": 0}

    async def probe(self, name: str, base_url: str) -> ProbeResult:
        url = base_url.rstrip("/") + settings.health_path
        started = time.perf_counter()
        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers={"Accept": "application/json"}) as response:
                    latency = int((time.perf_counter() - started) * 1000)
                    ok = 200 <= response.status < 300
                    return ProbeResult(name, ok, response.status, latency)
        except Exception as exc:
            return ProbeResult(name, False, None, None, type(exc).__name__)

    async def snapshot(self) -> list[ProbeResult]:
        backend, edge = await asyncio.gather(
            self.probe("backend", settings.backend_url),
            self.probe("edge", settings.edge_url),
        )
        return [backend, edge]

    def register(self, result: ProbeResult) -> int:
        if result.ok:
            self._failures[result.target] = 0
        else:
            self._failures[result.target] = self._failures.get(result.target, 0) + 1
        return self._failures[result.target]
