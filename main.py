from __future__ import annotations

import asyncio
import html
import os
import secrets
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from config import settings
from monitor import HealthMonitor
from repair import RepairEngine
from storage import init_db, add_event, open_incident, resolve_latest_incident, queue_fix, recent_incidents, recent_events, recent_fixes

TELEGRAM_TOKEN = os.getenv("GRU_BOT_TG_KEY") or settings.telegram_bot_token
bot = Bot(TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None
dp = Dispatcher()
monitor = HealthMonitor()
repair = RepairEngine()
last_state: dict[str, bool] = {}
DASHBOARD_TOKEN = os.getenv("GRU_GUARDIAN_DASHBOARD_TOKEN", "")


def is_admin(message: Message) -> bool:
    return bool(settings.telegram_admin_chat_id is not None and message.chat and message.chat.id == settings.telegram_admin_chat_id)


def render_snapshot(results) -> str:
    lines = [f"GRU Guardian • mode: {settings.mode}"]
    for item in results:
        if item.ok:
            lines.append(f"🟢 {item.target}: HTTP {item.status} • {item.latency_ms} ms")
        else:
            detail = f"HTTP {item.status}" if item.status else (item.error or "unreachable")
            lines.append(f"🔴 {item.target}: {detail}")
    return "\n".join(lines)


@dp.message(Command("start"))
async def start(message: Message) -> None:
    if settings.telegram_admin_chat_id is None:
        await message.answer(f"gru.guardian bootstrap mode.\nYour chat ID: {message.chat.id}\nSet GRU_GUARDIAN_TELEGRAM_ADMIN_CHAT_ID in Render and redeploy.")
        return
    if not is_admin(message):
        return
    await message.answer("gru.guardian online.\n/status /policy /incidents /updates /fix <request> /repair_backend /repair_edge /cabinet")


@dp.message(Command("status"))
async def status(message: Message) -> None:
    if is_admin(message):
        await message.answer(render_snapshot(await monitor.snapshot()))


@dp.message(Command("policy"))
async def policy(message: Message) -> None:
    if not is_admin(message):
        return
    await message.answer(
        f"Mode: {settings.mode}\n"
        f"Auto repair: {'enabled' if settings.can_repair else 'disabled'}\n"
        f"Code changes: {'enabled' if settings.can_code else 'disabled'}\n"
        f"Production changes: {'enabled' if settings.can_touch_production else 'disabled'}\n"
        f"AI key: {'configured' if settings.ai_key else 'missing'}"
    )


@dp.message(Command("incidents"))
async def incidents(message: Message) -> None:
    if not is_admin(message):
        return
    rows = recent_incidents(10)
    if not rows:
        await message.answer("No incidents recorded.")
        return
    lines = ["Recent incidents:"]
    for row in rows:
        state = "resolved" if row[5] else "open"
        lines.append(f"#{row[0]} • {row[2]} • {row[3]} • {state}\n{row[4]}")
    await message.answer("\n\n".join(lines)[:3900])


@dp.message(Command("updates"))
async def updates(message: Message) -> None:
    if not is_admin(message):
        return
    rows = recent_events(12)
    lines = ["Guardian updates:"] + [f"• {r[2]}: {r[3]}" for r in rows]
    await message.answer("\n".join(lines)[:3900])


@dp.message(Command("fix"))
async def fix_request(message: Message) -> None:
    if not is_admin(message):
        return
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Usage: /fix describe what should be investigated or changed")
        return
    request_id = queue_fix(text)
    add_event("fix_request", f"#{request_id}: {text}")
    await message.answer(
        f"🧠 Fix request #{request_id} queued.\n"
        "Guardian keeps it in the private operations log. Code/production changes require authenticated GitHub/Render access and your explicit command."
    )


@dp.message(Command("repair_backend"))
async def repair_backend(message: Message) -> None:
    if not is_admin(message):
        return
    ok, detail = await repair.safe_repair("backend")
    add_event("repair_backend", detail)
    await message.answer(("🟢 " if ok else "🟠 ") + detail)


@dp.message(Command("repair_edge"))
async def repair_edge(message: Message) -> None:
    if not is_admin(message):
        return
    ok, detail = await repair.safe_repair("edge")
    add_event("repair_edge", detail)
    await message.answer(("🟢 " if ok else "🟠 ") + detail)


@dp.message(Command("cabinet"))
async def cabinet_command(message: Message) -> None:
    if not is_admin(message):
        return
    base = os.getenv("RENDER_EXTERNAL_URL", "")
    if not base or not DASHBOARD_TOKEN:
        await message.answer("Private cabinet is not configured yet.")
        return
    await message.answer(f"Private cabinet:\n{base.rstrip('/')}/cabinet?token={DASHBOARD_TOKEN}")


def _authorized(request: web.Request) -> bool:
    supplied = request.query.get("token", "") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(DASHBOARD_TOKEN and secrets.compare_digest(supplied, DASHBOARD_TOKEN))


async def guardian_health(_: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "service": "gru.guardian",
        "mode": settings.mode,
        "telegram": "configured" if bot else "awaiting_secret",
        "ai": "configured" if settings.ai_key else "awaiting_secret",
    })


async def cabinet_page(request: web.Request) -> web.Response:
    if not _authorized(request):
        raise web.HTTPUnauthorized()
    results = await monitor.snapshot()
    cards = "".join(
        f"<div class='card'><b>{html.escape(r.target)}</b><br>{'🟢 healthy' if r.ok else '🔴 failed'}<br>HTTP {r.status or '-'} · {r.latency_ms or '-'} ms</div>"
        for r in results
    )
    inc = "".join(f"<li>#{r[0]} {html.escape(r[2])} · {html.escape(r[3])} · {'resolved' if r[5] else 'open'} — {html.escape(r[4])}</li>" for r in recent_incidents(20)) or "<li>No incidents</li>"
    fixes = "".join(f"<li>#{r[0]} {html.escape(r[3])} — {html.escape(r[2])}</li>" for r in recent_fixes(20)) or "<li>No fix requests</li>"
    page = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>GRU Guardian</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#09090b;color:#f5f5f5;margin:0;padding:28px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}}.card{{background:#151518;border:1px solid #2b2b31;border-radius:18px;padding:18px}}h1{{font-size:28px}}h2{{margin-top:28px}}li{{margin:10px 0;color:#c9c9d1}}.muted{{color:#888894}}</style></head><body><h1>gru.guardian</h1><p class='muted'>Private operations cabinet · mode {html.escape(settings.mode)}</p><div class='grid'>{cards}</div><h2>Incidents</h2><ul>{inc}</ul><h2>Fix requests</h2><ul>{fixes}</ul></body></html>"""
    return web.Response(text=page, content_type="text/html")


async def start_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", guardian_health)
    app.router.add_get("/", guardian_health)
    app.router.add_get("/cabinet", cabinet_page)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    return runner


async def watcher() -> None:
    while True:
        results = await monitor.snapshot()
        for result in results:
            failures = monitor.register(result)
            previous = last_state.get(result.target)
            last_state[result.target] = result.ok

            if result.ok:
                if previous is False:
                    resolve_latest_incident(result.target)
                    add_event("recovery", f"{result.target} recovered: HTTP {result.status}, {result.latency_ms} ms")
                    if bot and settings.telegram_admin_chat_id is not None:
                        await bot.send_message(settings.telegram_admin_chat_id, f"🟢 GRU recovery: {result.target} is healthy again. HTTP {result.status} • {result.latency_ms} ms")
                continue

            if failures == settings.failure_threshold:
                detail = f"{result.target} failed {failures} checks in a row; status={result.status}; error={result.error}"
                incident_id = open_incident(result.target, "critical", detail)
                add_event("incident", f"#{incident_id}: {detail}")
                if bot and settings.telegram_admin_chat_id is not None:
                    await bot.send_message(settings.telegram_admin_chat_id, f"🚨 GRU incident #{incident_id}\n{render_snapshot(results)}")
                    if settings.can_repair:
                        ok, repair_detail = await repair.safe_repair(result.target)
                        add_event("auto_repair", repair_detail)
                        await bot.send_message(settings.telegram_admin_chat_id, ("🛠 " if ok else "⚠️ ") + repair_detail)
        await asyncio.sleep(settings.poll_seconds)


async def main() -> None:
    init_db()
    add_event("startup", f"Guardian started in {settings.mode} mode")
    health_runner = await start_health_server()
    watcher_task = asyncio.create_task(watcher())
    try:
        if bot:
            await bot.delete_webhook(drop_pending_updates=False)
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        else:
            while True:
                await asyncio.sleep(3600)
    finally:
        watcher_task.cancel()
        await health_runner.cleanup()
        if bot:
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
