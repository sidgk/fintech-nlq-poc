# Google Sheets export — setup (one time, ~10 min)

When enabled, every Slack answer also lands in a **Google Sheet in your own Drive**,
and follow-up messages **in the same thread** modify the **same spreadsheet**:

| You say (in the thread) | The bot does |
|---|---|
| First question | Creates a new spreadsheet, writes a tab, replies with the link |
| A refinement ("now in euros", "sort by amount", "last 7 days") | **Overwrites** the current tab |
| A different question ("success rate by card brand") | **Adds a new tab** to the same sheet |
| "put that in a new spreadsheet" | Creates a **new** spreadsheet |

The bot understands which of these you mean via the LLM (`classify_intent` in
`bot/resolver.py`). All this is already coded — it just needs your Google credentials.

---

## Step 1 — Google Cloud Console (your action)

1. Go to https://console.cloud.google.com → create or pick a project.
2. **APIs & Services → Library** → enable **Google Sheets API** and **Google Drive API**.
3. **APIs & Services → OAuth consent screen**:
   - User type **External** → fill app name + your email → Save.
   - **Test users** → add **your own Gmail address** (required, or consent fails).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type **Desktop app** → Create → **Download JSON**.
5. Save that file as **`bot/credentials.json`** (exact path; it's gitignored).

## Step 2 — authorize once (your action, one command)

```bash
cd ~/Documents/fintech-nlq-poc
set -a; source .env; set +a
source venv/bin/activate
cd bot && python google_auth.py
```
A browser opens → pick your Google account → approve. This writes `bot/token.json`
(cached, auto-refreshing). You won't need to do this again.

## Step 3 — turn it on

`.env` already has `SHEETS_ENABLED=true`. Restart the bot:
```bash
pkill -f "python app.py"
cd ~/Documents/fintech-nlq-poc && set -a; source .env; set +a
source venv/bin/activate && cd bot && nohup python app.py > /tmp/slackbot.log 2>&1 &
```
Now ask a question in Slack — the reply includes a **📊 Google Sheets link**.

---

## Optional — mention-free follow-ups in channels

By default, the bot answers when you **@mention** it. To let it also pick up
**plain replies inside a thread it owns** (nicer for the "keep refining" flow),
add to the Slack app:
- **OAuth & Permissions → Bot Token Scopes**: add `message.channels`
- **Event Subscriptions → Subscribe to bot events**: add `message.channels`
- Reinstall the app when prompted.

(DMs already support mention-free follow-ups via the `message.im` scope you added.)

---

## Notes & guardrails

- **Reliability:** sheets are created in *your* Drive (OAuth), so there are no
  service-account quota issues. You own every file.
- **Security:** `credentials.json`, `token.json`, and `bot_state.db` are all
  gitignored — they never reach GitHub.
- **Failure is soft:** if the export ever errors, the Slack answer still arrives
  (the table), with a small "_sheet export skipped_" note. The bot never breaks.
- **Scopes are least-privilege:** `drive.file` lets the bot touch only files it
  created — it cannot see the rest of your Drive.
- **Thread state** lives in `bot/bot_state.db` (SQLite): `thread_ts → spreadsheet_id`.
