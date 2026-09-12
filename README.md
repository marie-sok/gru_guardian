# gru.guardian

Private operations companion for GRU.

## Responsibilities

- 24/7 health monitoring for GRU backend and edge proxy.
- Telegram alerts for incidents and recoveries.
- Private operations cabinet with current health, incidents and requested fixes.
- Safe repair hooks for Render redeploys when Repair mode is explicitly enabled.
- Persistent SQLite incident/event/fix-request log.
- Fix requests from Telegram via `/fix ...`.
- Separation of Observe / Repair / Code / Production permissions.

## Telegram commands

- `/status` — live backend + edge snapshot.
- `/policy` — current permission mode.
- `/incidents` — recent incidents.
- `/updates` — recent Guardian events.
- `/fix <request>` — queue a requested investigation or code fix.
- `/repair_backend` — guarded backend repair request.
- `/repair_edge` — guarded edge repair request.
- `/cabinet` — private dashboard link.

## Safety model

The default mode is `Observe`. Guardian does not silently modify production code. Automatic repair is enabled only in `Repair`, `Code` or `Production` mode, and requires an authenticated Render API key. Code-changing automation additionally requires authenticated GitHub access and should create a branch/PR rather than writing directly to the release branch.

## Deployment

Render service: `gru_guardian` in the dedicated `gru_guardian` workspace.

Required secret for Telegram:

`GRU_GUARDIAN_TELEGRAM_BOT_TOKEN`

On first Telegram `/start`, if no admin chat ID is configured, Guardian returns the chat ID. Set that as:

`GRU_GUARDIAN_TELEGRAM_ADMIN_CHAT_ID`

For the private cabinet also set a long random:

`GRU_GUARDIAN_DASHBOARD_TOKEN`

Never commit any of these secrets.
