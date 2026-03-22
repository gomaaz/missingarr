---
name: debug
description: Systematic debugging for Missingarr. Use when a bug is reported, a feature behaves unexpectedly, searches don't trigger, or the UI shows wrong state. Guides through a structured diagnosis using the agent→skill→DB→API→UI data flow.
user-invocable: true
argument-hint: "[symptom description]"
allowed-tools: Read, Grep, Glob, Bash
---

# Missingarr Debugging

Argument: $ARGUMENTS

---

## Step 1 — Classify the symptom

Immediately categorize what was reported:

| Category | Examples |
|----------|---------|
| **Search not triggering** | Force does nothing, 0 triggered, skill skipped |
| **Duplicate searches** | Same series/movie searched multiple times |
| **UI wrong state** | Badge doesn't update, stats stale, toggle visual broken |
| **Settings not saving** | Form value ignored after save |
| **Agent not running** | Status always OFF, no activity log entries |
| **Connection error** | Always "unknown" or "error", health check fails |
| **Performance** | UI sluggish after trigger, large library issues |

---

## Step 2 — Read the activity log first

Before reading any code, check what the agent actually logged:

```bash
# Via SQLite directly
sqlite3 data/missingarr.db \
  "SELECT created_at, level, skill, message FROM activity_log
   WHERE instance_id=<ID>
   ORDER BY created_at DESC LIMIT 50;"
```

Look for:
- `warn` / `error` entries — something failed silently
- "Skipping — quiet hours" — quiet hours active
- "Rate cap reached" — rate cap hit
- "All results are too recent" — hours_after_release filtered everything
- "Already running — skipping" — concurrent run guard triggered
- "skill not registered" — build_skills() missing the skill

---

## Step 3 — Trace the relevant code path

**Force trigger path:**
```
app.js forceRun()
  → resp.redirected check  ← auth redirect trap
  → POST /api/instances/{id}/trigger
    → orchestrator.trigger()
      → agent.trigger_now()
        → _run_skill(force=True)
          → config refresh from DB
          → skill_enabled flag check      ← search_missing_enabled / search_upgrades_enabled
          → quiet hours check             ← skipped when force=True
          → concurrent run guard          ← waits up to 90s
          → skill.execute(force=True)
            → hours_after_release filter  ← skipped when force=True
            → seen_keys dedup             ← in-run deduplication
            → DB cache check              ← skipped when force=True
            → HTTP POST to *arr
```

**Scheduled run path:** same, but `force=False` — all checks apply.

**UI update path:**
```
htmx polls /api/instances/{id}/status every 5s
  → updateCardState(id, responseText) in app.js
    → finds card by #icard-{id}
    → updates [data-status-badge], [data-conn-badge]
    → updates Alpine countdownComponent (nextRun, status)
    → updates [data-rate-bar], [data-rate-used]
    → updates [data-stat="last_wanted/last_triggered/last_sync"]
```

---

## Step 4 — Known pitfalls checklist

### Search-related
- [ ] `search_missing_enabled` / `search_upgrades_enabled` flag in DB — is it actually `1`?
- [ ] `hours_after_release` > 0 and all items are fresh → scheduled runs return 0
- [ ] Cache key mismatch between modes (episode vs season_packs vs smart)
- [ ] `seen_keys` set present in candidate selection loop
- [ ] For Sonarr: `season_number` can be `None` → use `(season_number or 0):02d`
- [ ] Datetime comparison: naive vs aware → catch `(ValueError, TypeError)` together

### UI-related
- [ ] New CSS class used in template → check it exists in `static/css/app.css`
- [ ] Checkbox outside `<form>` element → `form.fieldName` is `undefined` in submit handler
- [ ] Alpine `x-init` missing → hardcoded defaults override real instance values
- [ ] `data-` attribute missing on element → `updateCardState()` finds nothing to update

### DB-related
- [ ] New column added → need `ALTER TABLE` migration in `database.py`, `CREATE TABLE IF NOT EXISTS` won't add it
- [ ] API key read as raw encrypted string → must go through `crypto.decrypt()`

---

## Step 5 — Diagnose template

Fill this out before touching any code:

```
SYMPTOM:    What exactly happens / doesn't happen?
EXPECTED:   What should happen?
REPRODUCED: Force run? Scheduled run? Always? Sometimes?
LOG ENTRY:  Relevant line from activity_log?
CODE PATH:  Which path in Step 3 is involved?
ROOT CAUSE: Exact file + line number
FIX:        Minimal change needed
ELSEWHERE:  Same pattern in other files? (grep first)
```

---

## Step 6 — Fix strategy

1. Minimal fix — one targeted change
2. Grep for the same pattern in both Sonarr and Radarr paths
3. Verify the fix doesn't break the opposite condition (e.g., non-force runs still respect cache)
4. If CSS changed: verify class exists in `app.css`
5. If DB schema changed: add migration to `database.py`
