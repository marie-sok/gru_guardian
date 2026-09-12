from __future__ import annotations

import asyncio
import html
import os
import secrets

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ai_doctor import AIDoctor
from config import settings
from github_ops import GitHubOps
from monitor import HealthMonitor
from render_ops import RenderOps
from repair import RepairEngine
from storage import (
    add_event,
    init_db,
    open_incident,
    queue_fix,
    recent_events,
    recent_fixes,
    recent_incidents,
    resolve_latest_incident,
    storage_backend,
    update_fix,
)

TELEGRAM_TOKEN = os.getenv("GRU_BOT_TG_KEY") or settings.telegram_bot_token
bot = Bot(TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None
dp = Dispatcher()
monitor = HealthMonitor()
repair = RepairEngine()
render_ops = RenderOps()
github_ops = GitHubOps()
doctor = AIDoctor()
last_state: dict[str, bool] = {}
DASHBOARD_TOKEN = os.getenv("GRU_GUARDIAN_DASHBOARD_TOKEN", "")
ALLOWED_TELEGRAM_USERNAME = settings.allowed_username.lstrip("@").strip().lower()
ALLOWED_TELEGRAM_USER_ID = settings.allowed_user_id
runtime_lockdown = False


def _telegram_identity_ok(user) -> bool:
    if not user:
        return False
    username = (user.username or "").strip().lower()
    if not ALLOWED_TELEGRAM_USERNAME or username != ALLOWED_TELEGRAM_USERNAME:
        return False
    if ALLOWED_TELEGRAM_USER_ID is not None and user.id != ALLOWED_TELEGRAM_USER_ID:
        return False
    return True


def is_admin(message: Message) -> bool:
    return bool(
        message.chat
        and message.chat.type == "private"
        and _telegram_identity_ok(message.from_user)
    )


def is_admin_callback(query: CallbackQuery) -> bool:
    return _telegram_identity_ok(query.from_user)


def effective_mode() -> str:
    return "Observe (LOCKDOWN)" if runtime_lockdown else settings.mode


def render_snapshot(results) -> str:
    lines = [f"GRU Guardian • mode: {effective_mode()}"]
    for item in results:
        if item.ok:
            lines.append(f"🟢 {item.target}: HTTP {item.status} • {item.latency_ms} ms")
        else:
            detail = f"HTTP {item.status}" if item.status else (item.error or "unreachable")
            lines.append(f"🔴 {item.target}: {detail}")
    return "\n".join(lines)


def repair_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛠 Repair backend", callback_data="repair:backend"),
            InlineKeyboardButton(text="🛠 Repair edge", callback_data="repair:edge"),
        ],
        [InlineKeyboardButton(text="🔒 Lockdown", callback_data="lockdown:on")],
    ])


async def collect_evidence(include_logs: bool = True) -> str:
    chunks = [render_snapshot(await monitor.snapshot())]

    ok, ci = await github_ops.ci_summary()
    chunks.append("\n[CI]\n" + ci)

    for target in ("backend", "edge"):
        ok, deploys = await render_ops.deploys(target, 3)
        if ok:
            chunks.append(f"\n[RENDER {target.upper()} DEPLOYS]\n{deploys}")
        elif settings.render_api_key:
            chunks.append(f"\n[RENDER {target.upper()} DEPLOYS ERROR]\n{deploys}")

        if include_logs:
            ok, logs = await render_ops.logs(target, limit=18, minutes=60)
            if ok:
                chunks.append(f"\n[RENDER {target.upper()} LOGS]\n{logs}")
            elif settings.render_api_key:
                chunks.append(f"\n[RENDER {target.upper()} LOGS ERROR]\n{logs}")

    failed_jobs = await github_ops.failed_jobs_context()
    chunks.append("\n[FAILED CI CONTEXT]\n" + failed_jobs)
    return "\n".join(chunks)[:18000]


@dp.message(Command("start"))
async def start(message: Message) -> None:
    if not is_admin(message):
        return

    id_hint = ""
    if ALLOWED_TELEGRAM_USER_ID is None and message.from_user:
        id_hint += (
            f"\n\n🔐 Второй фактор ещё не закреплён. Telegram user ID: {message.from_user.id}\n"
            "Set GRU_GUARDIAN_ALLOWED_USER_ID to this value in Render."
        )
    if settings.telegram_admin_chat_id is None:
        id_hint += (
            f"\n\n🔔 Alert chat ID: {message.chat.id}\n"
            "Set GRU_GUARDIAN_TELEGRAM_ADMIN_CHAT_ID to this value for incident alerts."
        )

    await message.answer(
        "gru.guardian online.\n"
        "/status /doctor /ci /deploys /logs [backend|edge] /report\n"
        "/incidents /updates /fix <request> /policy /lockdown /unlock /cabinet"
        + id_hint
    )


@dp.message(Command("status"))
async def status(message: Message) -> None:
    if not is_admin(message):
        return
    await message.answer(render_snapshot(await monitor.snapshot()))


@dp.message(Command("doctor"))
async def doctor_command(message: Message) -> None:
    if not is_admin(message):
        return
    await message.answer("🩺 Собираю health/deploy/log/CI evidence…")
    evidence = await collect_evidence(include_logs=True)
    _, diagnosis = await doctor.diagnose(evidence)
    add_event("doctor", diagnosis[:1000])
    await message.answer("🧠 GRU Doctor\n\n" + diagnosis, reply_markup=repair_keyboard())


@dp.message(Command("ci"))
async def ci_command(message: Message) -> None:
    if not is_admin(message):
        return
    ok, text = await github_ops.ci_summary()
    await message.answer(("" if ok else "⚠️ ") + text)


@dp.message(Command("deploys"))
async def deploys_command(message: Message) -> None:
    if not is_admin(message):
        return
    outputs: list[str] = []
    for target in ("backend", "edge"):
        ok, text = await render_ops.deploys(target, 5)
        outputs.append(("" if ok else "⚠️ ") + text)
    await message.answer("\n\n".join(outputs)[:3900])


@dp.message(Command("logs"))
async def logs_command(message: Message) -> None:
    if not is_admin(message):
        return
    target = (message.text or "").partition(" ")[2].strip().lower() or "backend"
    if target not in {"backend", "edge"}:
        await message.answer("Usage: /logs backend  or  /logs edge")
        return
    ok, text = await render_ops.logs(target, limit=35, minutes=60)
    await message.answer(("" if ok else "⚠️ ") + text)


@dp.message(Command("report"))
async def report_command(message: Message) -> None:
    if not is_admin(message):
        return
    snapshot = render_snapshot(await monitor.snapshot())
    ok, ci = await github_ops.ci_summary()
    incidents = recent_incidents(5)
    events = recent_events(8)
    lines = [
        "📋 GRU Guardian report",
        snapshot,
        "",
        ci,
        "",
        f"Audit storage: {storage_backend()}",
        f"Recent incidents: {len(incidents)}",
    ]
    for row in events[:6]:
        lines.append(f"• {row[2]}: {row[3][:180]}")
    await message.answer("\n".join(lines)[:3900])


@dp.message(Command("policy"))
async def policy(message: Message) -> None:
    if not is_admin(message):
        return
    await message.answer(
        f"Configured mode: {settings.mode}\n"
        f"Effective mode: {effective_mode()}\n"
        f"Auto repair: {'disabled by lockdown' if runtime_lockdown else ('enabled' if settings.can_repair else 'disabled')}\n"
        f"Code changes: {'disabled by lockdown' if runtime_lockdown else ('enabled' if settings.can_code else 'disabled')}\n"
        f"Production changes: {'disabled by lockdown' if runtime_lockdown else ('enabled' if settings.can_touch_production else 'disabled')}\n"
        f"AI: {'configured' if settings.ai_key else 'deterministic fallback'}\n"
        f"Render API: {'configured' if settings.render_api_key else 'missing'}\n"
        f"GitHub API: {'configured' if settings.github_token else 'public/read-only where possible'}\n"
        f"Audit storage: {storage_backend()}\n"
        f"Telegram ID lock: {'enabled' if ALLOWED_TELEGRAM_USER_ID is not None else 'username-only; bind ID recommended'}"
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
    evidence = await collect_evidence(include_logs=False)
    _, diagnosis = await doctor.diagnose(f"USER FIX REQUEST #{request_id}: {text}\n\n{evidence}")
    update_fix(request_id, "diagnosed", diagnosis)
    await message.answer(
        f"🧠 Fix request #{request_id} diagnosed.\n\n{diagnosis}\n\n"
        "No code, merge, environment or production change was applied automatically."
    )


async def _repair_target(target: str) -> tuple[bool, str]:
    if runtime_lockdown:
        return False, "Guardian is in LOCKDOWN; repair actions are disabled"
    return await repair.safe_repair(target)


@dp.message(Command("repair_backend"))
async def repair_backend(message: Message) -> None:
    if not is_admin(message):
        return
    await message.answer("Confirm backend redeploy?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm backend repair", callback_data="repair:backend")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="noop")],
    ]))


@dp.message(Command("repair_edge"))
async def repair_edge(message: Message) -> None:
    if not is_admin(message):
        return
    await message.answer("Confirm edge redeploy?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm edge repair", callback_data="repair:edge")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="noop")],
    ]))


@dp.callback_query(F.data.startswith("repair:"))
async def repair_callback(query: CallbackQuery) -> None:
    if not is_admin_callback(query):
        await query.answer()
        return
    target = (query.data or "").partition(":")[2]
    if target not in {"backend", "edge"}:
        await query.answer("Invalid target", show_alert=True)
        return
    await query.answer("Repair requested")
    ok, detail = await _repair_target(target)
    add_event(f"repair_{target}", detail)
    if query.message:
        await query.message.answer(("🟢 " if ok else "🟠 ") + detail)


@dp.callback_query(F.data == "lockdown:on")
async def lockdown_callback(query: CallbackQuery) -> None:
    global runtime_lockdown
    if not is_admin_callback(query):
        await query.answer()
        return
    runtime_lockdown = True
    add_event("lockdown", "Runtime lockdown enabled from Telegram")
    await query.answer("LOCKDOWN enabled", show_alert=True)
    if query.message:
        await query.message.answer("🔒 Guardian is now in runtime LOCKDOWN. Repair/code/production actions are blocked.")


@dp.callback_query(F.data == "noop")
async def noop_callback(query: CallbackQuery) -> None:
    if is_admin_callback(query):
        await query.answer("Cancelled")


@dp.message(Command("lockdown"))
async def lockdown_command(message: Message) -> None:
    global runtime_lockdown
    if not is_admin(message):
        return
    runtime_lockdown = True
    add_event("lockdown", "Runtime lockdown enabled")
    await message.answer("🔒 LOCKDOWN enabled. Repair/code/production actions are blocked until /unlock or restart.")


@dp.message(Command("unlock"))
async def unlock_command(message: Message) -> None:
    global runtime_lockdown
    if not is_admin(message):
        return
    runtime_lockdown = False
    add_event("lockdown", "Runtime lockdown disabled")
    await message.answer(f"🔓 Runtime lockdown disabled. Effective configured mode: {settings.mode}.")


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
        "mode": effective_mode(),
        "telegram": "configured" if bot else "awaiting_secret",
        "ai": "configured" if settings.ai_key else "fallback",
        "storage": storage_backend(),
        "id_lock": ALLOWED_TELEGRAM_USER_ID is not None,
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
    page = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>GRU Guardian</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#09090b;color:#f5f5f5;margin:0;padding:28px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}}.card{{background:#151518;border:1px solid #2b2b31;border-radius:18px;padding:18px}}h1{{font-size:28px}}h2{{margin-top:28px}}li{{margin:10px 0;color:#c9c9d1}}.muted{{color:#888894}}</style></head><body><h1>gru.guardian</h1><p class='muted'>Private operations cabinet · mode {html.escape(effective_mode())} · storage {html.escape(storage_backend())}</p><div class='grid'>{cards}</div><h2>Incidents</h2><ul>{inc}</ul><h2>Fix requests</h2><ul>{fixes}</ul></body></html>"""
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
                    await bot.send_message(
                        settings.telegram_admin_chat_id,
                        f"🚨 GRU incident #{incident_id}\n{render_snapshot(results)}",
                        reply_markup=repair_keyboard(),
                    )
                    if settings.can_repair and not runtime_lockdown:
                        add_event("auto_repair_skipped", "Auto repair requires explicit Telegram confirmation")
        await asyncio.sleep(settings.poll_seconds)


async def main() -> None:
    init_db()
    add_event("startup", f"Guardian started in {settings.mode} mode; storage={storage_backend()}")
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
