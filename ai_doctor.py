from __future__ import annotations

import json

import aiohttp

from config import settings


SYSTEM_PROMPT = """You are GRU Guardian Doctor, a defensive SRE assistant for the GRU messenger.
Analyze only the supplied operational evidence. Never invent logs, secrets, commands that destroy data, or successful repairs.
Return concise Russian text with exactly these sections:
1) Состояние
2) Вероятная причина
3) Риск
4) Что делать
5) Можно ли чинить автоматически
Production changes, destructive database actions, secret rotation, branch merges and environment changes always require explicit human approval.
If evidence is insufficient, say so clearly.
"""


class AIDoctor:
    async def diagnose(self, evidence: str) -> tuple[bool, str]:
        evidence = evidence[:18000]
        if not settings.ai_key:
            return True, self._fallback(evidence)

        payload = {
            "model": settings.ai_model,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": evidence},
            ],
            "max_output_tokens": 1200,
        }
        headers = {
            "Authorization": f"Bearer {settings.ai_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=45)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{settings.ai_base_url.rstrip('/')}/responses",
                    headers=headers,
                    json=payload,
                ) as response:
                    raw = await response.text()
                    if response.status >= 300:
                        return True, self._fallback(evidence, f"AI unavailable: HTTP {response.status}")
                    body = json.loads(raw)
        except Exception as exc:
            return True, self._fallback(evidence, f"AI unavailable: {type(exc).__name__}")

        text = body.get("output_text") if isinstance(body, dict) else None
        if not text and isinstance(body, dict):
            parts: list[str] = []
            for item in body.get("output", []):
                if not isinstance(item, dict):
                    continue
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                        value = content.get("text")
                        if isinstance(value, str):
                            parts.append(value)
            text = "\n".join(parts)
        if not text:
            return True, self._fallback(evidence, "AI returned no text")
        return True, text[:3900]

    def _fallback(self, evidence: str, note: str | None = None) -> str:
        low = evidence.lower()
        problems: list[str] = []
        actions: list[str] = []

        if "connection refused" in low or "unreachable" in low:
            problems.append("Есть признаки недоступности сервиса или сетевого endpoint.")
            actions.append("Проверить последний deploy, health endpoint и runtime logs.")
        if "failure" in low or "failed" in low or "🔴" in evidence:
            problems.append("Обнаружены failed/critical сигналы в переданной диагностике.")
            actions.append("Локализовать первый первичный failure до выполнения redeploy.")
        if "timeout" in low:
            problems.append("Есть признаки timeout; возможна зависимость, сеть или перегрузка.")
            actions.append("Сопоставить timeout с Mongo/Render/WebSocket и частотой повторов.")
        if "mongodb" in low or "mongo" in low:
            actions.append("Проверить Mongo connectivity отдельно от HTTP availability.")
        if "websocket" in low:
            actions.append("Проверить handshake /ws и reconnect storm, не только REST health.")
        if "github" in low and ("failure" in low or "failed" in low):
            actions.append("Не продвигать релиз до зелёного CI; изучить failed jobs/steps.")

        if not problems:
            problems.append("По собранным данным явная первопричина не подтверждена.")
        if not actions:
            actions.append("Продолжить наблюдение и собрать deploy/log/CI evidence при следующем отклонении.")

        state = "Есть отклонения." if any(x in low for x in ("failed", "failure", "unreachable", "🔴")) else "Критичных подтверждённых отклонений не видно."
        reason = " ".join(problems)
        plan = " ".join(actions)
        suffix = f"\n\nFallback note: {note}" if note else ""
        return (
            f"1) Состояние\n{state}\n\n"
            f"2) Вероятная причина\n{reason}\n\n"
            "3) Риск\nСредний до подтверждения первопричины; production-действия без подтверждения запрещены.\n\n"
            f"4) Что делать\n{plan}\n\n"
            "5) Можно ли чинить автоматически\nТолько безопасный redeploy в разрешённом режиме Repair/Code/Production. Merge, env, БД и destructive actions — только после явного подтверждения владельца."
            + suffix
        )[:3900]
