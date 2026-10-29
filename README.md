# Satvik's job application automation

Python 3.11+; standard-library runtime; no paid API or subscription. The project
supports Linux/Ubuntu and Windows. Keep `SEND_ENABLED=false` during setup. Gmail
authentication, discovery coverage and scheduling must be verified before
enabling sending. Explicit user approval is required before any test email and
again before unattended production sending. See [need.md](need.md) for the full setup and VM migration guide,
and [agent.md](agent.md) for the work record and known limitations.

## Filters and schedule

- Software engineer/developer, SWE/SDE, backend/Node.js and full-stack roles.
- Cities: Noida, Delhi, Gurugram/Gurgaon, Hyderabad, Bengaluru/Bangalore.
- Explicit fresher eligibility or 0-2-year requirements fitting your actual experience.
- Official individual JobPosting pages with a timezone-qualified timestamp at
  most 12 hours old, a checked employer/office and an explicit application email.
- Continuous 2-hour discovery cycle: Newly found jobs are instantly queued and dispatched
  without waiting for arbitrary batch minimums.
- Maximum 150 attempts per IST day and rolling 24 hours (09:00-20:00 IST).
- Sending reservations are spaced 4 to 7 minutes apart (240-420 seconds).
- **18-Hour Stale Eviction**: If a posting in the queue reaches older than 18 hours, it is
  automatically evicted from the queue so you never send outdated applications.
- **Priority Queue Ranking (Selection Chance & ₹40,000+ In-Hand / 6.0-7.0+ LPA CTC)**: The queue dynamically
  prioritizes the newest jobs (<4-8 hrs), highest skill overlap, junior/fresher fit, and
  target compensation (6.0 to 7.0+ LPA CTC yielding ₹40,000+ in-hand per month).
- **Application templates**: Several factual wording variants include the verified PDF, portfolio and GitHub.
  Template variation does not bypass spam controls or guarantee inbox placement.
- **Daily 6 AM & 6 PM Portal Digest**: For jobs requiring portal applications, the bot
  ranks fresh openings by skill match score & recency and emails you a summary with
  direct portal links, ready-to-use referral notes, and 1-click LinkedIn referral search links
  for technical recruiters, engineers, and engineering managers at that employer.

Unsupported, undated, expired, ambiguous and unverified pages are held for review. Search
snippets and generic HR lists never authorize emails. There is no guarantee of
inbox placement, an interview, lifelong free hosting or zero maintenance. The bot does not
fabricate dates, skills or experience.

## Ubuntu Quick Start

Run these commands from the project directory. The bot uses only Python's
standard library; no `pip install` step is needed.

```bash
python3 --version
python3 init_local.py
python3 -m unittest discover -s tests -v
python3 jobbot.py health
python3 jobbot.py status
```

Use Python 3.11 or newer. `init_local.py` creates only missing local files and
does not replace existing `.env`, `profile.json`, `sources.json`, or history.
`health` should be checked before live use; it does not send email. The checked-in
configuration currently keeps sending disabled. Do not enable it until Gmail,
the profile/resume, verified sources, drafts and readiness blockers are reviewed.

For an unattended Ubuntu deployment, install the project at
`/opt/satvik-jobbot` and use the included `jobbot-dispatch.service` and
`jobbot-dispatch.timer`. Follow the Ubuntu VM steps in [need.md](need.md), which
cover private file permissions, preserving the SQLite send history, and enabling
only the single dispatch timer. The service runs `tick` once a minute; `tick`
handles two-hour discovery itself. Do not also enable the separate discovery
timer, Windows task, or n8n workflow. No scheduler is enabled by these commands.

To configure Gmail on Linux, put an app password only in the ignored `.env`
file, then run `python3 jobbot.py check-mail`. This authenticates SMTP and checks
Gmail Sent via read-only IMAP without sending a test message. Never put secrets
in source control, documentation, or chat. See Google's
[app-password help](https://support.google.com/mail/answer/185833?hl=en).

Create Google Alerts using alerts_queries.txt. Choose As-it-happens if available.
Add RSS/Atom feeds to sources.json or deliver Alerts to your Gmail and set
READ_GMAIL_ALERTS=true after checking IMAP access. Delivery/coverage is controlled
by Google and cannot guarantee a 12-hour result. Only Alert messages are ingested,
read-only. Direct Bing search crawling remains disabled due to robots.txt restrictions.

Two employers have public identity/office evidence in sources.json. Add further
verified employers with company, identity_verified, company_homepage,
verification_note, office_cities, office_evidence_url, posting_domains,
email_domains and career_urls. Optional company_aliases are exact names only.
Unknown domains still require verification. This is not comprehensive web search.

One employer-linked public Greenhouse board is also configured as a
manual-review-only discovery source. Its records appear in
`output/needs_review.csv` with title, URL, location, posting time, experience
snippets, freshness and the official portal method. ATS records are not
automatically approved or placed in the verified queue. Gmail Alerts are read
only from Inbox; the current VM check found no Google Alert emails in the prior
14 days, so configure actual alerts/feed delivery if that source is expected to
produce leads.

```bash
python3 jobbot.py discover
python3 jobbot.py draft
python3 jobbot.py status
python3 tools/preview_emails.py
```

Review output/applications.csv, output/needs_review.csv and per-job evidence.json
and application.eml. The examples under output/previews are clearly marked
fictitious and are never queued. The current profile records 0 years of professional
experience and no employment entries; projects are not counted as employment.
The configured attachment is resumes/Satvik_Saini_Resume.pdf. Check the profile
and resume facts before enabling any real sending. No ATS-score or screening-pass claim.

## Windows Scheduling

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\install-windows-task.ps1
Get-ScheduledTask -TaskName SatvikJobApplications
```

The prepared Windows task runs once a minute with hidden PowerShell while this user is
logged in and the computer is powered on. It skips overlapping runs. Python's
tick handles discovery every two hours and the send gate/spacing. It is not
installed for the Windows route. Logs: data/logs. Your computer still consumes
electricity and internet access.

Set SEND_ENABLED=true in local .env only when credentials, profile/resume and
verified sources are ready. Set PAUSE_SENDING=true or SEND_ENABLED=false to stop.
deploy/remove-windows-task.ps1 removes only this task and preserves history.

The optional local n8n workflow stays inactive and its updated `/tick` calls
have not been tested on a live n8n instance. Use one scheduler.

The current Linux deployment on this host is `/opt/satvik-jobbot`, using the
`jobbot-dispatch.timer` systemd timer. It is enabled and active; `SEND_ENABLED`
remains false. SMTP authentication was checked without sending, and the current
database has no verified or queued jobs. The provider identity has not been
independently verified from inside this workspace.

Oracle's standard Free Tier signup requires card verification:
https://www.oracle.com/cloud/free/faq/ . It therefore conflicts with the requested
no-card signup. This project has not provisioned an Oracle VM or stored provider
credentials. GitHub Actions has quota, inactivity and scheduling limitations and
has not been provisioned.

## Checks and operations

```bash
python3 -m unittest discover -s tests -v
python3 jobbot.py tick
python3 jobbot.py digest
python3 jobbot.py check-mail
python3 jobbot.py suppress bounced-or-opted-out@example.com
python3 jobbot.py resume-sending
```

Resolve the SMTP issue before resume-sending. Failed/uncertain recipients are
not automatically retried. Check later bounces/replies; inbox-wide automated
bounce classification is not implemented. Keep/back up data/jobbot.sqlite3 to
preserve deduplication, using SQLite's backup API while WAL writes are active.

Template variety cannot guarantee inbox delivery. Official Gmail references:
https://support.google.com/mail/answer/185833?hl=en (app passwords),
https://support.google.com/mail/answer/22839?hl=en (limits),
https://support.google.com/mail/answer/81126?hl=en (sender guidelines).

All tests use synthetic employers and mocked SMTP; they do not send real emails.
See agent.md for the final validation results and remaining live prerequisites.
