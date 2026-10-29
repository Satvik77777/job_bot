# Satvik job automation work record

## Live Deployment Audit (2026-10-28)

This dated audit supersedes earlier deployment/status statements below where
they conflict. The active installation is `/opt/satvik-jobbot` on the current
Ubuntu VM session (the user identifies the provider as Oracle Cloud; provider
identity was not independently exposed by this environment). `jobbot-dispatch.timer`
is enabled and active; the one-shot worker runs as unprivileged `jobbot` with
`ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges=true`, and write
access limited to `data/` and `output/`. The service now pulls in
`network-online.target`. Closing VS Code or SSH does not stop the systemd timer;
the VM itself must remain available. No 24/7 availability guarantee is made.

Current live state: `SEND_ENABLED=false`, `PAUSE_SENDING=false`, Gmail Alerts
reading enabled, Gmail app password present in the protected `.env` (value not
recorded here). `check-mail` passed SMTP authentication and read-only Sent-folder
access, with zero newly verified Sent messages and no test email sent. A
read-only INBOX query found 0 Google Alert messages in the previous 14 days.
Database WAL integrity check passed. The database has 0 jobs, 0 portal jobs,
0 attempts, 0 suppressed addresses, and 30 review records (5 existing official
career-source results plus 25 Razorpay Greenhouse candidates). Live source
discovery fetched FNP and MTAP official career pages (both aggregate listings,
not individual JobPosting records), refused to fetch CodeHall/RR InfoTek/Yebelo
because their domains are not trusted, and queried Razorpay's public Greenhouse
board only after confirming its official careers page links the board. All 25 ATS
items are manual-review-only; none has both a target software-engineering title
and a posting timestamp within the configured 12-hour window. The “AI Builder”
listing is recent but describes a product/business AI builder rather than a
software-engineering vacancy. No application recipient was invented or approved.
Root-only backups are at `/var/backups/satvik-jobbot/20261028T140534Z-discovery`
and `/var/backups/satvik-jobbot/20261028T140737Z-discovery-export`.

Audit fixes in `jobbot.py`: every portal digest send now obeys `SEND_ENABLED`,
`PAUSE_SENDING`, persistent SMTP pause and sender-match checks; it reserves a
delivery slot and job IDs before SMTP, prevents repeated sends/previews, excludes
stale/future posts, marks interrupted/failed delivery uncertain without automatic
retry, sanitizes SMTP error output, and HTML-escapes scraped values. The test
suite now has 53 cases, including digest gate, idempotency, pause, failure,
freshness, review-only Greenhouse provenance, India-remote location handling,
structured review export and HTML-injection regressions. All 53 passed both
from the source tree and as `jobbot`; touched Python modules compile. The actual
systemd worker was executed successfully after deployment and logged
`dispatch: preview` and `portal_digest: no_jobs`; the database still has zero
attempts. `jobbot.py`, tests, README and active service hashes match between
source/deployment as checked; secrets/config/database were not overwritten.

Discovery extension: `sources.json` configures one public Greenhouse board
(Razorpay) whose token is linked from the official careers site. Board items are
exported to `output/needs_review.csv` with title, URL, location, first-published
timestamp, freshness, title-match, skill matches, experience snippets and portal
application method. This is manual-review evidence only, not automatic
eligibility approval. SmartRecruiters' API is not integrated because its
`api.smartrecruiters.com` robots policy disallows fetching from this VM. Workday
and dynamically rendered Swiggy listings are not currently automated sources.

The actual profile has `years_experience: 0` and no employment entries. Older
statements later in this historical work record about Calinfo employment,
0.75 years, 80-recipient minimum, 100-attempt cap, missing Gmail password, or no
VM are superseded. Current config allows a 1-recipient daily threshold, up to 150
attempts per IST day/rolling 24 hours, 09:00-20:00 IST, and 240-420 seconds
between reservations. Review this high daily cap before authorizing production
sending. The timer is running, but production sending is not enabled. No real
application or test email was sent. Explicit user approval and a user-authorized
test recipient are still required before any test message; a separate final
approval is required before enabling production sending.

## Follow-up: operating guide

Added `need.md` at the user's request with ordered Windows setup steps, a table
of exact configuration/credential/resume locations, Gmail connection checks,
Alerts/RSS setup, verified employer-source examples, preview and readiness checks,
the 80-recipient release condition, activation/scheduling, logs/Sent checks,
pause/restart/removal, troubleshooting, SQLite backup and an optional existing
Ubuntu VM deployment/migration guide. Example passwords, hosts and domains are
placeholders; no secret values were copied into the guide. Instructions were
checked against the current scripts/configuration. This follow-up only changes
documentation; it does not send mail, change live configuration, register a task
or deploy a VM. Core tests were not rerun for this documentation-only change.

Updated: 2026-10-12. Workspace: `C:\Users\46sat\Desktop\jugad-main`.

## Current state and limits

This is a local implementation, not a deployed cloud service. No VM has been
created or connected. VM provider, IP, SSH user, SSH key and VM credentials are
**not configured**. No payment card was used, no paid subscription was created,
and no application email has been sent during this work.

The application has no paid runtime dependencies or search/LLM API. That does
not guarantee free hosting for life, zero electricity/internet cost, zero upkeep,
80 genuine matching vacancies every day, inbox placement or an interview.
Oracle's standard Free Tier signup requires card identity verification:
https://www.oracle.com/cloud/free/faq/ . Always Free instances can be reclaimed
and free services have no availability SLA. A Windows scheduler is prepared as
the no-cloud-account route. It requires this computer to be on and the user
logged in; installation is a separate explicit action. No task has been installed.

The hosting choice and Gmail setup question were asked early. No answer has
been received as of writing. Gmail's app password is empty in local `.env`.
Sending is disabled. Do not call this production-ready until authentication,
source coverage, scheduling and a real eligible application have been verified.

## Identity and resume

- Candidate: Satvik Saini. Sender: `satviksaini7777@gmail.com`.
- Portfolio: https://satvik-saini-portfolio.satviksaini7777.chatgpt.site/
- GitHub: https://github.com/Satvik77777
- Phone copied from the supplied resume: +91 7206178912.
- Original input: `resumes/Satvik_Saini_Resume.pdf`; one page. Its contact email
  differs from the requested sender, and extraction revealed spacing/icon issues.
- Resume-supported employment: Software Developer, Calinfo, January 2026-Present.
  Profile uses a conservative 0.75 years as of 2026-10-12. Projects are not counted
  as professional employment. Update this only when supported by actual facts.
- Profile facts were populated from the supplied PDF, not inferred from search.
  `confirmed: true` means compared to that supplied source, not independently
  certified employment. Portfolio/GitHub pages failed browser fetch; they were
  retained as the user's supplied links, without using them to invent claims.
- ATS draft: `output/pdf/Satvik_Saini_Resume.pdf`, generated from those facts.
  Its status and final attachment installation are recorded in validation below.
  No hidden keywords, fabricated metrics, new degrees or new employment added.
- Optional per-role tailoring reorders supplied skills/projects/bullets. It is
  off by default. Resume generation/rendering uses temporary free Python tools;
  the running bot itself uses Python's standard library only.

## Behavior implemented

`config.json` sets a daily minimum of 80 and maximum of 100 attempts, with a
rolling 24-hour cap as well as an IST calendar-day cap. Failed/uncertain attempts
count toward the cap. The bot never fills quotas with unverified HR lists.

1. A scheduled `tick` checks discovery when due and dispatches at most one email.
   Discovery retries every two hours. The SQLite schedule persists across restarts.
2. Only postings whose timezone-qualified `datePosted` is between now minus
   12 hours and now are accepted. Calendar dates alone and future timestamps
   are rejected. Crawl time and search-result time do not substitute for posting time.
3. Roles are software engineer/developer, SWE/SDE, backend/Node.js developer or
   full-stack developer in Noida, Delhi, Gurugram/Gurgaon, Hyderabad or
   Bengaluru/Bangalore. Senior/lead/staff/etc. roles are rejected by default.
4. Require explicit fresher/junior/entry-level wording or a parsed requirement
   of no more than two years. Also reject a minimum exceeding Satvik's actual
   configured experience. Numeric text and structured months are checked;
   these are conservative heuristics, not full natural-language qualification checks.
5. Require a trusted employer with independently recorded identity evidence,
   official website, target-city office evidence, allowed posting/email domains,
   and matching employer name or a recorded alias.
6. Require a single JobPosting JSON-LD on an individual official job page and
   one explicit, unambiguous instruction to email an application/resume. Generic
   contact/support addresses, ambiguous recipients, payment indicators, unknown
   domains and unsupported formats go to `output/needs_review.csv`.
7. Count only fresh queued jobs with distinct recipients, excluding suppression
   and existing attempts. Do not release today's batch until at least 80 exist.
   Below 80, keep waiting and discovering every two hours. A released batch is
   valid for that IST day; each send still needs its own 12-hour age check.
   The threshold resets the next day. This deliberate threshold may result in
   zero sends when fewer than 80 qualify or jobs expire before release.
8. Send between 09:00 and 20:00 IST with 360-420 seconds between reservations.
   Minute scheduler granularity can add up to another minute. Oldest verified
   jobs are considered first. There is no guarantee all queued jobs remain fresh
   long enough to send, and no guarantee of reaching the daily target.
9. Re-fetch canonical evidence and check profile/resume/draft hashes before send.
   Refresh the Date header on transmission. Use Gmail SMTP port 587 with verified
   TLS, one recipient and a PDF attachment. There are three factual email templates
   chosen deterministically per opening, including portfolio and GitHub links.
   Variation and pacing are for relevance and rate control; they do not evade spam
   filtering or guarantee inbox delivery. No cold-email HR spreadsheet import.
10. Reserve in SQLite before SMTP. A crash after possible SMTP acceptance cannot
    cause an automatic duplicate. Never send twice to the same recipient, even
    for another opening. Preserve `data/jobbot.sqlite3` to preserve that protection.
    Any SMTP failure pauses future sending; SMTP-refused recipients are suppressed.
11. `check-mail` authenticates SMTP without sending, then reads Gmail's special
    Sent folder via read-only IMAP and searches saved Message-IDs. It records
    `sent_visible` in send history. It does not append extra Sent copies. Gmail
    SMTP acceptance is not proof of delivery. Later bounces/replies need checking;
    this bot does not yet read them or automatically classify all bounce messages.
12. Tick workers take an atomic six-hour lease; scheduler settings avoid overlap.
    A crashed worker can delay work until the lease expires. The API binds only
    to 127.0.0.1 and requires a random local bearer token.

## Sources and public evidence

Free discovery consumes configured official employer pages, publisher RSS/Atom,
or Google Alerts delivered by RSS/IMAP. `init_local.py` generates 25 role/city
queries. Set Alerts to 'As-it-happens' if offered; delivery itself is not guaranteed
within 12 hours. Search snippets are leads and cannot authorize a send.
Direct Bing search crawling stays disabled because robots.txt blocked it in the
earlier implementation. No paid search key is required. Unknown employers need
identity/office verification before they can be allowed to receive applications.
This is not a complete automatic search of every website.

Recorded trust seeds:

- FNP: https://www.lhfunds.com/consumer/ferns-n-petals/ and
  https://www.dpiit.gov.in/static/uploads/2025/08/061c2eaaa3518362093ea184239e473d.pdf
  provide independent identity evidence. Official office:
  https://www.fnp.com/corporate/contact-us ; official careers:
  https://www.fnp.com/info/careers . Some SDE roles have email instructions, but
  the observed page did not prove a matching opening younger than 12 hours.
- MTAP Technologies: https://www.tofler.in/mtap-technologies-private-limited/company/U72900TN2013PTC089299
  lists the company, CIN, active status and www.mtap.in; its public record was
  cross-checked with https://www.zaubacorp.com/MTAP-TECHNOLOGIES-PRIVATE-LIMITED-U72900TN2013PTC089299 .
  Official office evidence: https://www.mtap.in/contactus/ ; official careers:
  https://www.mtap.in/careers/ . Listed offices include Gurugram, Bengaluru and
  Hyderabad. No paid company report was purchased. No qualifying fresh vacancy
  was inferred from the age of the career page.

CodeHall, RR InfoTek and Yebelo remain leads, not automatically trusted employers.
Public evidence does not prove that every vacancy or company interaction is genuine.
No HR email has been scraped from third-party contact spreadsheets for sending.

## Credentials and deployment

Secrets belong in ignored `.env` or a secret manager, never this document or chat.
This file records credential locations/status, not passwords or private-key contents.

- `SMTP_EMAIL`: Satvik's Gmail address, configured.
- `GMAIL_APP_PASSWORD`: not configured; regular Gmail password is unsupported.
  Enable 2-Step Verification, then use https://myaccount.google.com/apppasswords .
  `setup_gmail.ps1` prompts with hidden input and saves locally without printing it.
- `JOBBOT_API_TOKEN`: existing local random token; not disclosed or copied here.
- `SEND_ENABLED=false`; `PAUSE_SENDING` is a manual stop; SMTP errors add a DB pause.
- `READ_GMAIL_ALERTS=false` until Google Alerts and Gmail IMAP are actually set up.
- VM credentials: none. No VM used. SSH key/location: none supplied.

Windows files: `deploy/run-jobbot.ps1`, `deploy/install-windows-task.ps1`,
`deploy/remove-windows-task.ps1`. A current-user scheduled task named
`SatvikJobApplications` uses hidden PowerShell, runs every minute, skips concurrent
runs and writes date-based logs under `data/logs`. It does not run when the computer
is off or the user is logged out. It has not been registered during this work.

Existing Ubuntu/systemd route: copy the project to `/opt/satvik-jobbot`, use an
unprivileged `jobbot` user, restrict `.env`/profile/source files to owner read/write,
install `jobbot-dispatch.service` and `.timer`. Dispatch now calls `tick`, so a
separate discover timer is unnecessary. Stop old discover timers to avoid duplicate
work. An existing free VM can use this, subject to provider network/quota rules.
No installation or remote connection was performed.

Optional n8n workflow stays inactive on disk. Its local authenticated HTTP calls
now use `/tick` so two-hour discovery is handled by Python. Do not run n8n and
systemd/Windows scheduling together. n8n live import has not been tested.

GitHub Actions was considered as a no-VM option but has not been provisioned.
Private repos have finite free minutes; public scheduled workflows can disable
after inactivity and schedules may be delayed. Durable private deduplication
history also requires care. No public upload of resumes, recipient history or
credentials has been made. It does not meet a guaranteed-lifelong-free requirement.

## Files changed and verification

- `jobbot.py`: strict timestamp and experience checks, Delhi support, office evidence,
  employer aliases, three templates, distinct-recipient batch gate, two-hour discovery,
  persisted worker lease, pre-network pacing/cap check, Gmail connection/Sent checks,
  health/status reporting and explicit connection closure.
- `config.json`: requested 12-hour/2-hour/80-100 policy and sending intervals.
- `profile.json`: factual profile based on the supplied resume; correct contact links.
- `sources.json`: two checked employer seeds with public identity/office evidence.
- `init_local.py` / `alerts_queries.txt`: five target-city groups, SDE/SWE-focused queries.
- Legacy mailer entry point now routes to status/tick instead of any old contact lists.
- `tailor_resume.py`: preserves factual content and supports achievements.
- `tools/build_resume.py`, `tools/render_resume.py`: reproducible optional PDF authoring
  and PDFium rendering. Runtime bot does not depend on those PDF libraries.
- Windows setup/Gmail scripts and existing systemd/n8n scheduling updated.
- `tests/test_jobbot.py`: expanded from 22 tests to cover freshness boundaries,
  missing timezone/date-only cases, Delhi, office evidence, experience ceiling,
  80-recipient threshold and deduplication, daily reset, expiry while queued,
  retry timing, leases, template facts/attachments and read-only Sent visibility.
  Existing tests cover mocked SMTP success/failure, preview, rolling caps,
  suppression, altered drafts, hidden scripts and safe feed parsing.
- Original suite passed 22/22. First expanded run found two test-clock setup errors,
  corrected by retaining the actual mock returned by `patch.start()`.
- Final verification: **37/37 offline tests passed**, including two final checks
  for `0 to 2 years` ranges and equivalent NodeJS/Node.js skill spellings. All
  four PowerShell scripts parsed with zero errors. Mocked SMTP tests sent no
  real emails. Results: `output/test-results.txt` and `output/validation.json`.
- Added checks preserve already-sent draft artifacts on rediscovery, reject a
  graduate-degree requirement as insufficient fresher evidence, and prevent
  transmission if a posting becomes stale during SMTP authentication.
- Final PDF is **one page**, visually reviewed with no clipping/overlap, correct
  requested Gmail address, 447 extracted words and zero replacement glyphs.
  The first render was two pages with excessive blank space; typography and
  spacing were tightened and the final page re-rendered and inspected.
- The original Satvik PDF is preserved as
  `resumes/Satvik_Saini_Resume.original.pdf`. The reviewed ATS PDF was copied to
  `resumes/Satvik_Saini_Resume.pdf`; SHA-256 confirms it equals the reviewed file
  in `output/pdf`. Default original mode now attaches that checked file without
  needing reportlab on the running machine.
- Removed the obsolete other-candidate PDF and both legacy spreadsheets after
  confirming that both spreadsheets contained the old candidate's name. A
  source/config/text scan found no remaining old candidate name. Obsolete Python
  caches were cleared. Satvik's original backup was retained.
- Three professional preview templates with the reviewed PDF are in
  `output/previews/template-1.eml` through `template-3.eml` and companion `.txt`
  files. Their employer/recipient are fictitious, clearly labelled, never queued
  and never sent. `tools/preview_emails.py` reproduces them.
- `tools/verify_outputs.py` verified all three distinct preview bodies and that
  every attachment equals the reviewed PDF. It normalized the Windows UTF-16
  test capture to readable UTF-8 and retained `output/test-results.raw.txt`.
  Windows PowerShell had labelled unittest's normal stderr `NativeCommandError`;
  the test process exited 0 with 37 tests passing in 11.612 seconds. Final checks
  confirmed all three obsolete files are absent and the original backup remains.
- Live discovery: two checked employer career pages fetched, **zero verified
  eligible openings**, five review rows. FNP/MTAP aggregate career pages lacked
  a single individual JobPosting with reliable fresh evidence; three other
  employer domains remained unverified. No freshness or recipients were invented.
  The persisted next-discovery time is visible in `python jobbot.py status`.
- Final health blockers: Gmail app password missing, broad Alerts/feed discovery
  not set up, and SMTP disabled. **Zero real SMTP attempts.** Live SMTP/Sent,
  scheduler installation, remote deployment and real end-to-end sending remain
  untested; no VM, hostname, SSH key or VM credentials exist for this deployment.
- Temporary authoring tools were installed only under
  `C:/Users/46sat/AppData/Local/Temp/satvik-jobbot-pdf-tools`: pypdf 6.20.0,
  reportlab 5.0.1, pypdfium2 5.14.0, Pillow 12.3.0 and charset-normalizer 3.5.2.
  Poppler was unavailable; PDFium rendered PNGs for visual QA. The PDF skill's
  artifact-operation marker was invoked once before PDF authoring. These tools
  are optional authoring dependencies, not required by the default SMTP bot.
- README was refreshed to remove stale claims that the supplied resume/profile
  were missing. `tools/refresh_docs.py` can reproduce the short README. API-token
  changes require restarting the optional HTTP server. Logs/artifacts and history
  are retained; long-term backups/retention and factual profile updates still need
  an operator. Windows scheduling requires a powered-on, awake, logged-in PC.

## Operator steps

1. `python jobbot.py health` reports unmet prerequisites without revealing secrets.
2. Set up Gmail locally with `powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\setup_gmail.ps1`.
3. `python jobbot.py check-mail` checks authentication/Sent access without sending.
4. Create Google Alerts, add feed URLs to `sources.json` or set
   `READ_GMAIL_ALERTS=true` after IMAP is checked.
5. Add independently verified employer sources with office evidence. Run
   `python jobbot.py discover` and review CSVs/evidence/drafts. At least 80 fresh,
   distinct, eligible recipients are required before today's batch is released.
6. Choose the hosting route. For local Windows, run the prepared installer only
   when ready; check `Get-ScheduledTask -TaskName SatvikJobApplications`.
7. Set `SEND_ENABLED=true` locally only when profile, resume, credentials and
   sources are ready. The user's request authorizes eligible automated applications;
   this work has not enabled them because prerequisites are missing.
8. `python jobbot.py check-mail` verifies accepted messages in Sent. Review later
   bounces/replies and `python jobbot.py suppress address@example.com` as needed.
9. To stop: `PAUSE_SENDING=true`, `SEND_ENABLED=false`, or remove/stop the scheduler.
   Keep the SQLite history; never delete it merely to retry an uncertain send.

Windows installer captures the actual `sys.executable` path for the task, avoiding
dependence on shell-profile PATH activation. The worker adds that interpreter's
existing DLL/Library directories to its own process PATH for Conda compatibility.
The task and documented commands select RemoteSigned for that PowerShell process
only; they do not change the machine/user execution policy. Administrative group
policy restrictions still take precedence. The updated scripts were checked for
PowerShell parse errors; actual registration/run remains pending the hosting choice.

Official references checked: Gmail app passwords
https://support.google.com/mail/answer/185833?hl=en ; Gmail limits
https://support.google.com/mail/answer/22839?hl=en ; sender guidance
https://support.google.com/mail/answer/81126?hl=en ; GitHub schedule limitations
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule ;
GitHub public inactivity disabling
https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows .

## Phase 1 Implementation Record (2026-10-28)

Completed Phase 1 minimal surgical improvements and validation without altering existing systemd, rate limits, or sending safeguards:

1. **Experience Evaluation Defect Corrected**:
   - Implemented `evaluate_experience_fit()` in `jobbot.py`.
   - Distinguishes mandatory experience requirements from preferred sub-skill experience.
   - Evaluates mandatory minimum entry experience against profile `years_experience` without disqualifying 0-year candidates when preferred skills mention 1+ years.
   - Preserves maximum role experience ceiling (`maximum_role_experience_years`: 2) and fresher eligibility requirements.

2. **Posting Date Handling & Portal Freshness Separation**:
   - Implemented `parse_portal_job_date()` supporting both ISO timestamps and standard calendar dates (`YYYY-MM-DD`). Rejects future and invalid dates.
   - Direct email applications retain the strict 12-hour timezone-aware posting window.
   - Portal opportunities adopt a configurable 7-day calendar window (`portal_max_post_age_days: 7` in `config.json`), supported in `evaluate_portal_job()`, `greenhouse_review_candidates()`, and `send_portal_digest()`.

3. **Greenhouse ATS Portal Routing**:
   - In `discover()`, when `review_only: false`, Greenhouse candidates are parsed through `evaluate_portal_job()`.
   - Verified candidates are inserted into `portal_jobs` with status `pending`. Direct email queue (`jobs`) remains unaffected.
   - Preserves `reviews` audit trail for all ATS candidates.

4. **Testing and Verification**:
   - Expanded unit test suite from 53 to 57 tests in `tests/test_jobbot.py`. All 57 tests pass in 0.28s.
   - Isolated end-to-end integration test (`scratch/test_portal_flow.py`) confirmed: synthetic portal job evaluation, priority scoring, duplicate prevention, HTML/TXT digest preview generation (`output/portal_digests/`), and 0 jobs in the email queue.
   - Production backup taken at `/var/backups/satvik-jobbot/*-pre-phase1/`.
   - Verified files deployed to `/opt/satvik-jobbot` with matching SHA-256 hashes.
   - Systemd background timer `jobbot-dispatch.timer` confirmed active and healthy. Production `SEND_ENABLED=false` preserved.
