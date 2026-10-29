# Step-by-step guide to run Satvik's job automation

## Current VM Status (2026-10-28)

The project is deployed on this Ubuntu host at `/opt/satvik-jobbot`. The
`jobbot-dispatch.timer` systemd timer is enabled and active; the worker runs
independently of VS Code and SSH sessions. `SEND_ENABLED=false` remains in
place. Gmail SMTP authentication and read-only Sent-folder access passed, but
no email was sent. The database integrity check passed; there are no application
attempts, queued jobs, or portal jobs. Two verified-employer career pages were
fetched and held because they were aggregate pages without a single individual
JobPosting JSON-LD. Three other career domains remain unverified. The official
Razorpay Greenhouse board is linked and loaded as 25 manual-review-only ATS
records; none has both a target software-engineering title and a post within the
configured 12-hour freshness window. No Google Alert messages were present in
the last 14 days. The report has 30 review rows (5 career-source records + 25
ATS candidates). Do not rerun optional VM installation steps against this
existing deployment.

For **Windows**, follow Steps 1-10 in order. For an **existing VM**, follow
Steps 1-8 to prepare and check the local files, keep `SEND_ENABLED=false`, skip
Windows activation/scheduling in Steps 9-10, then follow the optional VM section.
Enable sending only on the machine you choose to run. The Windows setup below
does not enable sending or register a Windows task. The Ubuntu VM deployment is
already installed as noted above.

## Before starting

You need:

- Python **3.11 or newer**, available as `python` in PowerShell.
- This complete project folder: `C:\Users\46sat\Desktop\jugad-main`.
- Internet access and your `satviksaini7777@gmail.com` account.
- Google 2-Step Verification and a **Gmail app password** supported by that account.
- Google Alerts delivered to your Gmail, or working publisher RSS/Atom feed URLs.
- Verified employer sources with office evidence and eligible individual job postings.
- For the Windows route: an **awake, powered-on computer with your user logged in**.

No paid API, n8n, Docker, OpenAI key or extra Python package is needed for the
default automation. PDF-authoring tools are optional and are not needed to send
the resume already supplied. The offline test suite uses mocked SMTP and never
sends real mail. Live application delivery has not been tested.

Running on your existing computer requires no cloud signup or card, but uses
your electricity and internet. A new
[Oracle standard Free Tier signup requires card verification](https://www.oracle.com/cloud/free/faq/).
This guide does not promise free hosting for life, zero upkeep, inbox placement,
screening success or a guaranteed number of qualifying vacancies every day.

## Where to put each required item

All relative paths in this guide are inside the project folder.

| Item | File/location | What to do |
| --- | --- | --- |
| Gmail sender and app password | `.env` | Configure locally in Step 4. Never put passwords in `need.md`, `agent.md` or chat. |
| Sending and Alerts switches | `.env` | Keep sending off during setup; enable in Step 9. |
| Name, email, skills, real experience, portfolio, GitHub | `profile.json` | Check the populated facts against your current resume. |
| PDF attached to applications | `resumes/Satvik_Saini_Resume.pdf` | Keep this exact filename and path. The checked ATS version is already here. |
| Your original PDF backup | `resumes/Satvik_Saini_Resume.original.pdf` | Preserve it; this is not the configured attachment. |
| Trusted employers, official posting URLs, RSS feeds | `sources.json` | Add real evidence and sources in Steps 5-6. |
| Google Alerts search queries | `alerts_queries.txt` | Copy queries into Google Alerts. |
| Timing, freshness and daily limits | `config.json` | Keep the supplied values for your requested policy. |
| Preview email formats | `output/previews/` | Read the `.txt` files; `.eml` files include the attachment. These are fictitious samples, not real applications. |
| Actual prepared applications | `output/<job-id>/application.eml` | Review after eligible jobs are discovered. |
| Evidence for each application | `output/<job-id>/evidence.json` | Check the official posting and recipient evidence. |
| Review, application and sending reports | `output/*.csv` | Inspect status and rejection reasons. |
| Persistent deduplication and schedule | `data/jobbot.sqlite3` | Preserve and back up; deleting it loses the record of contacted recipients. |
| Windows automation scripts | `deploy/` | Install the task in Step 10. |
| Windows worker logs | `data/logs/` | Created once the scheduled worker runs. |

**Do not replace your existing `.env`, `profile.json` or `sources.json` with an
example file.** Edit the existing files so you retain credentials, facts and trust records.

## Step 1 - Open PowerShell in the project

Open PowerShell and run:

```powershell
Set-Location -LiteralPath 'C:\Users\46sat\Desktop\jugad-main'
python --version
```

Expected: `Python 3.11.x` or a newer version. If Python is missing or too old,
install a supported version from [python.org](https://www.python.org/downloads/windows/),
enable the installer option to add Python to PATH if offered, then reopen
PowerShell and repeat these commands.

Run the remaining Windows commands from this folder. If you move the folder
after installing the scheduled task, reinstall that task from the new location.

## Step 2 - Initialize the local files

```powershell
python init_local.py
python jobbot.py health
python jobbot.py status
```

`init_local.py` creates missing private configuration/folders and the Alerts query
file. It does not overwrite your existing profile or sources. It also prepares
the optional API token. The ordinary Windows scheduler does not need the HTTP API.

During setup, these health blockers are expected until you finish later steps:

- `Gmail app password is missing`
- `Broad discovery needs Google Alerts feeds or Gmail Alerts access`
- `SMTP sending is disabled`

`health` reports prerequisites; it does **not** prove SMTP authentication or
adequate vacancy coverage. Also, a command exiting successfully is not the same
as `ready_for_live_sending: true`.

## Step 3 - Check your profile and resume

Open `profile.json` in a text editor. These values must be correct:

```json
{
  "name": "Satvik Saini",
  "email": "satviksaini7777@gmail.com",
  "portfolio": "https://satvik-saini-portfolio.satviksaini7777.chatgpt.site/",
  "github": "https://github.com/Satvik77777",
  "resume_mode": "original",
  "resume_pdf": "resumes/Satvik_Saini_Resume.pdf"
}
```

This is a **partial reference**, not a replacement for the complete profile.
Keep its other fields, including skills, summary, education, projects and experience.

Check that:

1. `resumes/Satvik_Saini_Resume.pdf` opens and contains your correct details.
2. `phone`, `summary`, `skills` and employment facts are accurate.
3. `years_experience` reflects actual professional experience. The current
   profile value is `0`, with no employment entries. Change it only when supported
   by accurate employment facts; projects are not employment years.
4. `confirmed` is `true` only after checking those facts.
5. `allow_senior_roles` stays `false`.

The bot targets 0-2-year roles **and** checks the advertised minimum against your
actual experience. It will not claim one or two years if your profile has less.

Keep `resume_mode: "original"` for the dependency-free run. Here, "original"
means attaching the PDF at the configured path, which now contains the checked
ATS version. Per-job PDF generation in `tailored` mode is optional and requires
additional local authoring tools and layout checks.

## Step 4 - Connect Gmail without sending anything

1. Sign in to **satviksaini7777@gmail.com** in your browser.
2. Enable [Google 2-Step Verification](https://myaccount.google.com/security).
3. Open [App passwords](https://myaccount.google.com/apppasswords).
4. If available, create an app password named `Satvik Jobbot` and copy it.
5. Back in PowerShell, run:

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\setup_gmail.ps1
```

Enter the app password at the hidden prompt. The script saves it in `.env`
without printing it. Use an **app password**, not your normal Google password.
If Google does not offer app passwords for your account, stop this step and
resolve that account restriction; OAuth authentication is not implemented here.
See [Google's app-password help](https://support.google.com/mail/answer/185833?hl=en).

Open `.env` locally and check the following lines. Edit only those lines; retain
the existing `JOBBOT_API_TOKEN` and any saved password. The password text below
is a placeholder, not a usable credential:

```dotenv
SMTP_EMAIL=satviksaini7777@gmail.com
GMAIL_APP_PASSWORD=YOUR_16_LETTER_APP_PASSWORD
SEND_ENABLED=false
PAUSE_SENDING=false
READ_GMAIL_ALERTS=false
```

Now run:

```powershell
python jobbot.py check-mail
```

Expected response includes:

```json
{
  "smtp_authenticated": true,
  "gmail_sent_accessible": true,
  "email_sent_by_check": false
}
```

This command authenticates Gmail SMTP and opens the Sent folder over read-only
IMAP. It does not send a test email. Before any real applications, a count of
zero newly verified Sent messages is normal. SMTP uses port **587** and IMAP
uses port **993**; the computer/VM must be able to reach both.

## Step 5 - Set up free discovery

Choose **A or B**. Existing verified career pages can be checked without Alerts,
but those few pages alone are not broad daily web discovery.

### A. Google Alerts delivered to Gmail

1. Open `alerts_queries.txt` from this folder.
2. Visit [Google Alerts](https://www.google.com/alerts) while signed in to the
   same `satviksaini7777@gmail.com` account.
3. Copy a query into the Alerts search box. Open its options before creating it.
4. Choose **As-it-happens** and **All results** if offered. Choose English/India
   where those options are available. Deliver to your Gmail address.
5. Create the alert. Repeat for the other queries; the file contains 25 searches
   covering the requested role/city groups.
6. Keep the Alert messages in your Inbox: this implementation searches `INBOX`,
   not arbitrary labels or archived mail.
7. Edit this line in `.env`:

```dotenv
READ_GMAIL_ALERTS=true
```

The bot reads only Google Alert messages with read-only IMAP/BODY.PEEK. It does
not mark messages read, delete them or ingest your entire inbox. It reads up to
100 recent Alert emails from the last 14 days, then checks the linked employer
pages against the strict 12-hour posting rule.

New alerts may need time to produce messages. Google controls delivery timing
and coverage; an alert's arrival time is not proof of a fresh job posting.

### B. Google Alerts RSS or publisher RSS/Atom feeds

If your account offers RSS delivery, create alerts with RSS delivery and copy
their actual feed URLs. Publisher-provided job RSS/Atom feeds are also supported.

In `sources.json`, replace the empty `alert_feeds` list with real feed URLs:

```json
"alert_feeds": [
  "https://www.google.com/alerts/feeds/REPLACE_WITH_YOUR_REAL_FEED_PATH"
]
```

This is a placeholder. Do not paste it unchanged or use a Google Alerts management
page URL as a feed. Keep the other `sources.json` fields and existing records.
For this route, leave `READ_GMAIL_ALERTS=false`; Gmail SMTP credentials are still
needed for applications. Treat private feed URLs as private account information.

Leave `public_search_enabled` as `false` in `config.json`. Direct Bing search
crawling was disabled because of robots.txt restrictions; turning it on does
not provide reliable free web search.

## Step 6 - Add verified employer sources

Open `sources.json`. Its three main sections are:

- `trusted_companies`: checked employer identities, domains and office evidence.
- `posting_urls`: official career pages or individual opening URLs to inspect.
- `alert_feeds`: real RSS/Atom feeds, if using Step 5B.

FNP and MTAP already have public identity/office evidence recorded. **That does
not mean their current openings qualify.** The last live check found zero eligible
openings, and their aggregate career pages were held for review.

For each additional employer:

1. Check its identity through independent public evidence, such as registry
   records or a credible investor/customer source that identifies its real website.
2. Check its official website and evidence of an office in a requested city.
3. Confirm the official career page and exact hiring-organization name.
4. Record its real domains and evidence in a new `trusted_companies` object.
5. Add its official career URLs and relevant individual posting URLs.

Example **record shape** below. Replace all placeholders with checked facts.
Start with `identity_verified: false`; change it to `true` only after doing the
checks and writing the real verification evidence:

```json
{
  "company": "REPLACE_WITH_EXACT_EMPLOYER_NAME",
  "company_aliases": [],
  "identity_verified": false,
  "company_homepage": "https://employer.example/",
  "verification_note": "REPLACE_WITH_DATE_AND_INDEPENDENT_EVIDENCE_URLS",
  "office_cities": ["Noida"],
  "office_evidence_url": "https://employer.example/contact",
  "posting_domains": ["employer.example"],
  "email_domains": ["employer.example"],
  "career_urls": ["https://employer.example/careers"]
}
```

Append that object **inside** the existing `trusted_companies` array. Preserve
the existing entries; separate entries with commas and avoid trailing commas.
Domain entries are hostnames only, without `https://` or a path. Do not broadly
trust an entire shared job-board/ATS host merely because one employer uses it.

For an individual job URL, add a string inside `posting_urls`, for example:

```json
"https://employer.example/careers/REPLACE_WITH_REAL_JOB_PAGE"
```

Validate the JSON syntax after editing:

```powershell
python -m json.tool sources.json > $null
python -m json.tool profile.json > $null
```

No output normally means valid JSON; an error identifies the line to fix.

Automatic sending requires an **individual** job page with one `JobPosting`
JSON-LD, a timezone-qualified `datePosted`, matching employer/city/experience,
at least two relevant confirmed skills, and one explicit application-email
instruction on an allowed employer domain. A date such as `2026-10-12` alone
cannot establish a 12-hour age. Search snippets, generic HR lists, social posts
and unsupported formats do not become applications automatically.

## Step 7 - Discover and review while sending is disabled

Confirm `.env` still has `SEND_ENABLED=false`, then run:

```powershell
python jobbot.py discover
python jobbot.py status
python tools/preview_emails.py
```

With a confirmed profile, discovery prepares drafts for qualifying jobs. The
preview command writes only fictitious examples and does not queue or send them.

Review:

| File | What you should inspect |
| --- | --- |
| `output/previews/template-1.txt` through `template-3.txt` | General wording, your contact details, portfolio and GitHub. |
| `output/applications.csv` | Real discovered jobs and their current status. |
| `output/needs_review.csv` | Why pages failed checks or need supported/verified sources. |
| `output/<job-id>/application.eml` | Actual recipient, subject, body and PDF attachment. |
| `output/<job-id>/evidence.json` | Official URL, posting timestamp and employer evidence. |

If you corrected your profile/resume after drafts were prepared, keep sending
disabled and regenerate the unsent drafts:

```powershell
python jobbot.py draft
```

This does not remove the requirement to recheck the posting before sending.
Editing a CSV row does not approve or release an application. Fix the actual
source/configuration/evidence, then run discovery again.

The current `config.json` release threshold is 1 fresh, verified, distinct
recipient, with a maximum of 150 attempts per IST day and rolling 24 hours. The
scheduled worker retries discovery every two hours. The threshold resets each IST
day. Job age is rechecked before transmission, so even a released batch can
produce fewer sends if postings expire or fail later checks. Review policy before
changing these values.

## Step 8 - Run the offline checks

```powershell
python -m unittest discover -s tests -v
python jobbot.py health
```

The tests use synthetic data and mocked SMTP, so they do not send real emails.
The suite uses synthetic data and mocked SMTP; it does not send real mail. Run
the tests and inspect any failure before enabling live sending.

At this stage, fix every unexpected health blocker. `SMTP sending is disabled`
is still expected because you deliberately left it off. An empty review queue
or a successful health command is not proof that eligible vacancies exist.

## Step 9 - Production sending requires explicit approval

Do not perform this step yet. The live VM currently has `SEND_ENABLED=false` and
no verified application recipients. Before changing it, present the actual
recipient, subject, body, attachment, selection evidence and limits to the user.
First obtain explicit approval for one test email and use only the exact address
the user authorizes. After that test succeeds, obtain a separate explicit final
approval before enabling unattended production delivery. A successful SMTP
acceptance is not proof of inbox delivery. Never test against a recruiter or
employer unless that recipient has explicitly invited the application and passed
the configured source/recipient checks.

Only after those approvals, profile/resume review, Gmail connection checks, and
verified discovery sources are complete, edit the VM's `.env`:

```dotenv
SEND_ENABLED=true
PAUSE_SENDING=false
```

Keep `READ_GMAIL_ALERTS` set according to your chosen discovery route, and keep
the existing password/token lines. Save the file. Commands reload `.env`.

```powershell
python jobbot.py health
python jobbot.py discover
python jobbot.py tick
```

`tick` can send **at most one actual eligible application per run** if the
configured daily threshold has been met, sending hours permit it, and all other
checks pass. The current cap is 150 attempts/day, which should be explicitly
reviewed and approved before production use; do not treat it as a target or
guaranteed quota.
`health` should report `ready_for_live_sending: true` when its prerequisite
checks pass, but that field does not guarantee a released batch or delivery.

The supplied policy is:

| Setting | Current behavior |
| --- | --- |
| Posting age | At most 12 hours, checked again at send time. |
| Discovery retry | Every 2 hours after a discovery run. |
| Daily release threshold | 1 fresh verified distinct eligible recipient (`config.json`). |
| Cap | 150 attempts per IST day and rolling 24 hours; uncertain/failed attempts count. |
| Sending hours | 09:00 to before 20:00 IST. |
| Pacing | 240-420 seconds between reservations; scheduler granularity can add delay. |
| Repeated recipients | The same recipient is never contacted again automatically. |

Possible `tick`/dispatch results include `preview`, `outside sending hours`,
`waiting for at least 1...`, `paced/capped`, `paused`, `sent`, and
`no eligible queued jobs`. Waiting or pacing is normal under these rules.
One `tick` command is one check, not a continuously running background loop.

## Step 10 - Install automatic Windows scheduling

Once the manual checks above are complete, run:

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\install-windows-task.ps1
Get-ScheduledTask -TaskName SatvikJobApplications
Get-ScheduledTaskInfo -TaskName SatvikJobApplications
```

Expected: a task named **SatvikJobApplications**, normally `Ready` or `Running`.
It runs hidden PowerShell every minute. The script captures your actual Python
interpreter path. Python handles the two-hour discovery schedule, daily gate,
pacing and suppression. You do not need to leave a PowerShell window open.

Keep this computer **awake, connected to the internet and logged in**. The task
cannot run when the computer is off, asleep or your user is logged out. The
installer's successful execution does not by itself prove that sending is ready;
it does not enforce every `health` blocker as an installation failure.

The commands use RemoteSigned for that PowerShell process only. They do not
change the machine's execution policy; administrative group policy can still
restrict script execution. If registration says access is denied, rerun the
installer in an Administrator PowerShell on your own machine, using the same
Windows user. Do not create a second scheduler under a different account.

Do not run Windows scheduling, VM scheduling and n8n scheduling simultaneously.
Separate installations with separate databases cannot share deduplication.

## Check the sent messages and logs

Open Gmail's **Sent** section to inspect recipient, subject, body and attachment.
To verify accepted messages against their stored Message-IDs, run:

```powershell
python jobbot.py check-mail
python jobbot.py status
Get-Content -LiteralPath output/send_history.csv -Tail 10
Get-ChildItem -LiteralPath data/logs -Filter '*.log'
```

Open the newest file in `data/logs/` to see the worker's results. Those logs are
created by scheduled runs, so the folder may not exist before the first run.

`sent_visible=1` in history means the checker found that message in Gmail Sent.
The check is **manual**, not automatically invoked on every scheduled tick.
SMTP acceptance and Sent visibility do not prove delivery to the employer's inbox.
Check for later bounces and replies; automatic inbox-wide bounce handling is
not implemented. Do not manually resend an uncertain application without
investigating whether Gmail already accepted it.

## Pause, restart, or remove the automation

### Pause sending

Edit `.env` and save:

```dotenv
PAUSE_SENDING=true
```

For setup/preview mode, also set `SEND_ENABLED=false`. These switches prevent
future dispatches; they cannot recall an email already in transmission.

### Stop the whole Windows worker

```powershell
Disable-ScheduledTask -TaskName SatvikJobApplications
Stop-ScheduledTask -TaskName SatvikJobApplications
```

Stopping only the current task without disabling it permits the next scheduled
minute to launch it again. Stopping a worker during SMTP can leave an uncertain
attempt; preserve its history and investigate it before any manual retry.

### Restart after fixing an issue

If a Gmail/SMTP failure created a persisted pause, resolve the cause, then run:

```powershell
python jobbot.py check-mail
python jobbot.py resume-sending
```

Set `PAUSE_SENDING=false` and `SEND_ENABLED=true` in `.env` when ready, then:

```powershell
Enable-ScheduledTask -TaskName SatvikJobApplications
Start-ScheduledTask -TaskName SatvikJobApplications
```

`resume-sending` only clears the persisted SMTP pause. It does not toggle `.env`
switches, unsuppress addresses or retry previously failed/uncertain recipients.

Suppress a bounced or opted-out recipient using its real email address:

```powershell
python jobbot.py suppress real-recipient@real-company-domain.com
```

Replace that example; it is not a real recipient to use unchanged.

### Remove the Windows task

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\remove-windows-task.ps1
```

This preserves the project and SQLite history. Do not delete the database to
force another application to the same recipient.

## Troubleshooting

| Result/problem | Action |
| --- | --- |
| `preview` | `.env` still has `SEND_ENABLED=false`; this is expected during setup. |
| Missing app password or Gmail check fails | Check the account, app password, 2-Step Verification, and outbound ports 587/993. Do not substitute your ordinary password. |
| Broad discovery blocker | Finish Step 5A with `READ_GMAIL_ALERTS=true`, or add working feed URLs in Step 5B. |
| Zero jobs or waiting below configured minimum | Read `output/needs_review.csv`. Add genuinely verified sources/eligible postings; no quota is guaranteed. |
| Untrusted domain | Verify the employer and office evidence before adding a trust record. |
| Single JobPosting required / date-only / no reliable date | The page does not provide the supported evidence. A career listing or snippet cannot substitute for an individual freshly dated opening. |
| Required experience exceeds profile | Keep your real experience; this opening is not eligible under the current filters. |
| Outside sending hours | Wait until the 09:00-20:00 IST window. |
| `paced/capped` | The next-send time or attempt cap has been reached; wait. |
| `paused` | Inspect `python jobbot.py status`, resolve the cause and follow the restart steps. |
| Profile or draft changed | Disable sending, correct your files and regenerate unsent drafts with `python jobbot.py draft`. |
| Worker already running after a crash | A six-hour lease can remain until expiry. Wait and inspect the worker/logs; do not delete the database. |
| Windows task does not run | Check login/awake state, task status, logs, Python location and whether the project folder moved. |
| Invalid JSON | Correct the reported syntax error in `profile.json`, `sources.json` or `config.json`; avoid trailing commas. |

## Back up before moving the project or restoring it

Keep `.env`, `profile.json`, `sources.json`, `resumes/` and the database private.
Preserve `config.json`, the Python files and `deploy/` with them. Back up the
SQLite history using its backup API rather than copying only the main file while
WAL writes are active:

```powershell
python -c "import sqlite3; source=sqlite3.connect('data/jobbot.sqlite3'); target=sqlite3.connect('data/jobbot.backup.sqlite3'); source.backup(target); target.close(); source.close(); print('Backup saved to data/jobbot.backup.sqlite3')"
```

For a migration, stop the old scheduler before taking the final backup and
switching machines. A backup while workers continue can miss attempts made
after its snapshot. Never operate two independent copies as active senders.

## Optional - Install on a Different or Rebuilt Ubuntu VM

Skip this section for Windows. This section is for a new/replacement VM; the
current host is already deployed as described at the top. You
need an existing host whose ongoing resource/network costs you have confirmed,
an SSH username, a local private-key file, sudo access, systemd, Python 3.11+,
and outbound access to HTTPS, Gmail SMTP 587 and IMAP 993. Ubuntu 24.04's default
Python is suitable; check your actual installed version before proceeding.

Do not put a private key or password in this document. If requesting deployment
help, supply the VM hostname/IP, username and **local key-file path**, not its
contents. Use a single deployment. Stop Windows/n8n scheduling first if either
is active, and preserve any existing send history.

### VM A - Connect and create the installation directory

In local PowerShell, replace the placeholders:

```powershell
ssh -i 'C:\PATH\TO\YOUR_KEY' VM_USER@VM_HOST
```

On the VM, run these **Bash** commands:

```bash
python3 --version
sudo install -d -m 700 -o "$USER" -g "$(id -gn)" /opt/satvik-jobbot
```

Continue only with Python 3.11+. The directory command is for a **new** deployment;
do not overwrite a running installation or its database. Exit the SSH session:

```bash
exit
```

### VM B - Upload the runtime files securely

From local PowerShell in the project folder, replace `VM_USER`, `VM_HOST` and
the key path in each command:

```powershell
scp -i 'C:\PATH\TO\YOUR_KEY' jobbot.py init_local.py tailor_resume.py config.json profile.json sources.json .env profile.example.json sources.example.json .env.example VM_USER@VM_HOST:/opt/satvik-jobbot/
scp -i 'C:\PATH\TO\YOUR_KEY' -r resumes deploy VM_USER@VM_HOST:/opt/satvik-jobbot/
```

Set `SEND_ENABLED=false` before uploading `.env`. It contains credentials, so
transfer it with SSH/SCP, not a public repository. The uploaded profile/source
files should already be configured using Steps 3-6.

If migrating an existing sender, first stop its scheduler and create the final
SQLite backup as described above. Reconnect to the new VM with the SSH command
from VM A. In that remote Bash session, create `data/` while the installation
directory is still owned by your SSH user, then return to local PowerShell:

```bash
install -d -m 700 /opt/satvik-jobbot/data
exit
```

Transfer the backup from local PowerShell:

```powershell
scp -i 'C:\PATH\TO\YOUR_KEY' data/jobbot.backup.sqlite3 VM_USER@VM_HOST:/opt/satvik-jobbot/data/jobbot.sqlite3
```

For a new installation with no previous attempts, no database transfer is needed.
For an existing VM installation, reconcile/preserve its own history rather than
overwriting it with a different machine's database.

### VM C - Prepare files and the service account

Reconnect with SSH. On a **new** deployment, run:

```bash
cd /opt/satvik-jobbot
umask 077
python3 init_local.py
sudo useradd --system --user-group --home-dir /opt/satvik-jobbot --shell /usr/sbin/nologin jobbot
sudo chown -R jobbot:jobbot /opt/satvik-jobbot
sudo chmod 700 /opt/satvik-jobbot
sudo chmod 600 /opt/satvik-jobbot/.env /opt/satvik-jobbot/profile.json /opt/satvik-jobbot/sources.json
sudo -u jobbot /usr/bin/python3 /opt/satvik-jobbot/jobbot.py check-mail
sudo -u jobbot /usr/bin/python3 /opt/satvik-jobbot/jobbot.py discover
sudo -u jobbot /usr/bin/python3 /opt/satvik-jobbot/jobbot.py health
```

If the `jobbot` account already exists, check it rather than creating a second
one. After ownership changes, the SSH user may need `sudo` to access the directory.
Review actual evidence/drafts and finish the same prerequisite checks as Windows.

When ready, edit the VM's **own** configuration:

```bash
sudo nano /opt/satvik-jobbot/.env
```

Set `SEND_ENABLED=true` and `PAUSE_SENDING=false`, preserving its password/token
and chosen Alerts setting. Editing Windows `.env` later will not update the VM.

### VM D - Enable the systemd timer

```bash
sudo cp /opt/satvik-jobbot/deploy/jobbot-dispatch.service /etc/systemd/system/
sudo cp /opt/satvik-jobbot/deploy/jobbot-dispatch.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jobbot-dispatch.timer
sudo systemctl list-timers jobbot-dispatch.timer
sudo journalctl -u jobbot-dispatch.service -n 50 --no-pager
```

Install **only the dispatch timer** for this route. Its `tick` already handles
two-hour discovery. Do not also enable `jobbot-discover.timer` or the n8n workflow.
If a separate discovery timer was enabled previously, stop/disable that timer.
The supplied service uses `/usr/bin/python3`; ensure that interpreter is 3.11+
or update its `ExecStart` to the actual supported interpreter before enabling it.
If your timer starts while another discovery is due/running, its logs explain the
lease or scheduling delay; there is no guarantee of an immediate application.

To disable future VM runs, then stop a currently running worker if needed:

```bash
sudo systemctl disable --now jobbot-dispatch.timer
sudo systemctl stop jobbot-dispatch.service
```

Stopping during SMTP can leave an uncertain attempt, just as on Windows. Use
`PAUSE_SENDING=true` for a sending pause and investigate ambiguous history.
No HTTP API port needs to be exposed for these timers. `JOBBOT_API_TOKEN`, the
HTTP server and n8n are optional and are not needed for either route above.

## Final pre-run checklist

- [ ] Python is 3.11+ and commands run from the right project/install location.
- [ ] Your checked resume is at `resumes/Satvik_Saini_Resume.pdf`.
- [ ] Your profile, skills, contact links and experience are accurate and confirmed.
- [ ] Gmail app password is saved only in local `.env`; `check-mail` passes.
- [ ] Gmail Alerts or actual RSS/Atom feeds are configured.
- [ ] Real employer identity/office evidence and supported postings are configured.
- [ ] You reviewed sample emails and actual drafts/evidence, when any qualify.
- [ ] Offline tests pass; no unexpected health blockers remain.
- [ ] You understand that fewer than the configured daily minimum means no sending.
- [ ] Live `.env` has `SEND_ENABLED=true` and `PAUSE_SENDING=false` when ready.
- [ ] Exactly one scheduler/deployment is active, with preserved history.
- [ ] You can find logs, Gmail Sent, pause controls and the database backup.

For the detailed implementation and previous validation record, see [agent.md](agent.md).
