# JOBBOT V5 — PRODUCTION LAUNCH VALIDATION CHECKLIST

Run these safe, non-destructive commands in your VM SSH terminal after executing `bash scripts/deploy_jobbot_release.sh`:

---

### Check 1: Timer & Service Active
```bash
sudo systemctl list-timers jobbot-dispatch.timer
sudo systemctl status jobbot-dispatch.service -n 5 --no-pager
```
*Expected*: `jobbot-dispatch.timer` is `active (waiting)`; next trigger listed within 60s.

---

### Check 2: Production Service Dispatch Logs
```bash
sudo journalctl -u jobbot-dispatch.service -n 25 --no-pager
```
*Expected*: Clean execution of `jobbot.py tick` with return mode `'preview'` or `'skipped'`. Zero Python tracebacks.

---

### Check 3: Production Health & Database Counts
```bash
sudo -u jobbot python3 /opt/satvik-jobbot/jobbot.py health
```
*Expected*:
- `ready_for_live_sending`: `false` (safety switch disabled as required)
- `credentials_printed`: `false`
- `blockers`: `SMTP sending is disabled`
- `source_health`: Source counts for healthy/cooldown sources
- `backups`: count >= 1

---

### Check 4: Source Health & Registered Endpoints
```bash
sudo -u jobbot python3 -c "
import sqlite3
conn = sqlite3.connect('/opt/satvik-jobbot/data/jobbot.sqlite3')
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT source_url, source_type, health_status, last_http_status, consecutive_failures, recent_job_count FROM source_health').fetchall()
print(f'Total Registered Source Health Records: {len(rows)}')
for r in rows:
    print(f\"  - [{r['health_status']}] {r['source_url']} (type={r['source_type']}, HTTP={r['last_http_status']}, jobs={r['recent_job_count']})\")
conn.close()
"
```
*Expected*: Shows registered status for Greenhouse boards and career pages. Failures or cooldowns are visible and not hidden.

---

### Check 5: Next Scheduled Discovery Time
```bash
sudo -u jobbot python3 -c "
import sqlite3
conn = sqlite3.connect('/opt/satvik-jobbot/data/jobbot.sqlite3')
for r in conn.execute(\"SELECT key, value FROM meta WHERE key IN ('next_discovery_at', 'last_backup_at', 'tick_lease_until', 'smtp_paused')\").fetchall():
    print(f\"{r[0]}: {r[1]}\")
conn.close()
"
```
*Expected*: `next_discovery_at` has an ISO timestamp in `+05:30` (IST); `tick_lease_until` is None or expires within 15 minutes.

---

### Check 6: Category A & Category B Digest Preview
```bash
sudo -u jobbot python3 /opt/satvik-jobbot/jobbot.py digest
```
*Expected*: Output shows `mode: "preview_saved_sending_disabled"`. HTML file generated in `/opt/satvik-jobbot/output/portal_digests/`.
Category A is 0 (or confirmed eligible). Category B shows only authentic manual review candidates (e.g. Plane, HackerRank), with senior/lead/unmet mandatory roles excluded.

---

### Check 7: Database Integrity & Backup Verification
```bash
sudo -u jobbot python3 -c "
import sqlite3
conn = sqlite3.connect('/opt/satvik-jobbot/data/jobbot.sqlite3')
res = conn.execute('PRAGMA integrity_check').fetchone()
print(f'Active DB Integrity: {res[0]}')
conn.close()
"
ls -la /opt/satvik-jobbot/data/backups/
```
*Expected*: `Active DB Integrity: ok`; at least 1 backup file exists with permissions `0600`.

---

### Check 8: Sending Switches Verification
```bash
grep -E "(SEND_ENABLED|DIGEST_SEND_ENABLED)" /opt/satvik-jobbot/.env
```
*Expected*: Both flags are set to `false`. Zero live applications or emails transmitted without separate explicit authorization.
