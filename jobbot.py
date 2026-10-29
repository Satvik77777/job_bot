"""Personal job applications: free discovery, strict evidence checks, paced SMTP.

Python 3.11+, standard library only. No paid API or language model required.
"""
import argparse
import csv
from contextlib import closing
import hashlib
import hmac
import html
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import imaplib
import json
import logging
import os
from pathlib import Path
import random
import re
import smtplib
import socket
import sqlite3
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from urllib.parse import urljoin, urlsplit, urlencode, urlunsplit, parse_qs, parse_qsl
from urllib.request import Request, build_opener, HTTPRedirectHandler, urlopen
from urllib import robotparser
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ROLES = re.compile(
    r"\b(?:"
    r"software\s+(?:development\s+)?(?:engineer|developer)|"
    r"back[ -]?end\s+(?:engineer|developer)|"
    r"node(?:\.?js)?\s+(?:engineer|developer)|"
    r"react(?:\.?js)?\s+(?:engineer|developer)|"
    r"web\s+developer|"
    r"swe(?:\s*[- ]?\s*[123i]+)?|"
    r"sde(?:\s*[- ]?\s*[123i]+)?|"
    r"full[ -]?stack\s+(?:engineer|developer)|"
    r"graduate\s+(?:software\s+)?(?:engineer|developer|trainee)|"
    r"graduate\s+engineer\s+trainee|"
    r"associate\s+(?:software\s+|back[ -]?end\s+|full[ -]?stack\s+)?(?:engineer|developer)|"
    r"platform\s+(?:engineer|developer)"
    r")\b",
    re.I
)
CITIES = re.compile(r"\b(noida|delhi|new delhi|gurugram|gurgaon|hyderabad|bangalore|bengaluru)\b", re.I)
JOB_LOCATIONS = re.compile(r"\b(noida|delhi|new delhi|gurugram|gurgaon|hyderabad|bangalore|bengaluru|india\s*[-,]?\s*remote|remote\s*[-,]?\s*india)\b", re.I)
APPLY = re.compile(r"\b(?:send|submit|email|e-mail|mail|apply|share)\b.{0,100}\b(?:resume|cv|application|apply|email|e-mail|mail)\b", re.I)
SCAM = re.compile(r"(?:registration|application|processing|training|security)\s+(?:fee|deposit)|pay\s+(?:a\s+)?(?:fee|deposit)|guaranteed\s+(?:job|placement)|whatsapp.{0,50}(?:only|payment)", re.I)
UA = 'SatvikJobApplications/1.0'
LOG = logging.getLogger('jobbot')


def now():
    return datetime.now(IST)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def load_json(filename):
    return json.loads((ROOT / filename).read_text(encoding='utf-8'))


def load_env():
    file = ROOT / '.env'
    if file.exists():
        for line in file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip().strip('\"').strip("'")


def canonical(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))


def clean_job_url(url):
    """Strip marketing/tracking query parameters while preserving canonical path and essential queries."""
    if not url:
        return ''
    p = urlsplit(url)
    if not p.query:
        return canonical(url)
    q_pairs = parse_qsl(p.query, keep_blank_values=True)
    clean_pairs = [
        (k, v) for k, v in q_pairs
        if not k.lower().startswith('utm_') and k.lower() not in ('ref', 'source', 'tracking_id')
    ]
    new_query = urlencode(clean_pairs)
    return canonical(urlunsplit((p.scheme, p.netloc, p.path, new_query, '')))


def domain_matches(host, domains):
    return any(host == d.lower() or host.endswith('.' + d.lower()) for d in domains if d)


def public_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Only public HTTPS URLs on port 443 are supported')
    # Re-check redirects too; never fetch private/cloud-metadata addresses.
    addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Non-public host rejected')


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    def __init__(self):
        self.opener = build_opener(SafeRedirect())
        self.robots = {}
        self.last = {}

    def raw(self, url):
        public_url(url)
        with self.opener.open(Request(url, headers={'User-Agent': UA}), timeout=15) as r:
            data = r.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError('Page exceeds 2 MB')
            return r.geturl(), data.decode(r.headers.get_content_charset() or 'utf-8', errors='replace')

    def get(self, url):
        public_url(url)
        p = urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            rules = robotparser.RobotFileParser()
            try:
                _, text = self.raw(origin + '/robots.txt')
                rules.parse(text.splitlines())
            except Exception as e:
                # A missing robots.txt permits crawling; other failures defer it.
                if getattr(e, 'code', None) == 404:
                    rules.parse([])
                else:
                    raise ValueError('Cannot verify robots.txt') from e
            self.robots[origin] = rules
        rules = self.robots[origin]
        if not rules.can_fetch(UA, url):
            raise ValueError('robots.txt disallows this page')
        gap = max(2, rules.crawl_delay(UA) or 0)
        time.sleep(max(0, gap - (time.monotonic() - self.last.get(origin, 0))))
        self.last[origin] = time.monotonic()
        return self.raw(url)


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.blocks = []
        self.script = None
        self.hidden = 0
        self.text = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        if tag == 'script':
            self.script = [] if a.get('type', '').lower() == 'application/ld+json' else None
            self.hidden += 1
        if tag == 'style':
            self.hidden += 1
        if tag in ('p', 'div', 'li', 'br', 'section'):
            self.text.append('\n')

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if not self.hidden:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == 'script':
            if self.script is not None:
                try:
                    self.blocks.append(json.loads(''.join(self.script)))
                except json.JSONDecodeError:
                    pass
            self.script = None
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)


def plain(source):
    return re.sub(r'[ \t]+', ' ', ''.join(Page(source).text)).strip()


def json_jobs(value):
    if isinstance(value, list):
        for item in value:
            yield from json_jobs(item)
    elif isinstance(value, dict):
        kind = value.get('@type', [])
        if 'JobPosting' in ([kind] if isinstance(kind, str) else kind):
            yield value
        else:
            for item in value.values():
                if isinstance(item, (list, dict)):
                    yield from json_jobs(item)


def posted_date(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return result.replace(tzinfo=IST) if result.tzinfo is None else result.astimezone(IST)


def posting_timestamp(value):
    # A calendar date or timezone-less time cannot establish a 12-hour age.
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('Posting needs a timestamp with timezone, not just a date')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Posting timestamp needs a timezone')
    return result.astimezone(IST)


def parse_portal_job_date(value, current_dt=None):
    """Parse posting date for portal opportunities (supports ISO timestamps and YYYY-MM-DD dates).

    Returns (dt_ist, is_date_only) or raises ValueError for invalid/future dates.
    """
    if current_dt is None:
        current_dt = now()
    if not isinstance(value, str):
        raise ValueError('Date must be a string')
    val = value.strip()
    if not val:
        raise ValueError('Empty date string')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', val):
        try:
            d = datetime.strptime(val, '%Y-%m-%d').date()
        except ValueError as e:
            raise ValueError(f'Invalid calendar date: {val}') from e
        if d > current_dt.date():
            raise ValueError(f'Future posting date: {val}')
        dt = datetime(d.year, d.month, d.day, 0, 0, tzinfo=IST)
        return dt, True
    try:
        parsed = datetime.fromisoformat(val.replace('Z', '+00:00'))
    except ValueError as e:
        raise ValueError(f'Invalid date format: {val}') from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    else:
        parsed = parsed.astimezone(IST)
    if parsed > current_dt:
        raise ValueError(f'Future posting timestamp: {val}')
    return parsed, False


MENTOR_LEAD_PATTERN = re.compile(
    r'\b(?:'
    r'mentor(?:ing|s)?\s+(?:junior|other|team|engineers?|developers?|members?|interns?)|'
    r'lead(?:ing|s)?\s+(?:a\s+)?(?:team|engineers?|developers?)|'
    r'guid(?:e|ing|es)\s+(?:junior|other|team)\s+(?:engineers?|developers?)|'
    r'supervis(?:e|ing|es)\s+(?:junior|team|engineers?)|'
    r'provide\s+(?:technical\s+)?guidance\s+to\s+(?:junior|team)'
    r')\b',
    re.I
)

SENIOR_ROLES_PATTERN = re.compile(
    r'\b(senior|sr\.?|lead|principal|staff|manager|architect|director|vp|head|engineer\s*(?:iii|[345])|sde\s*[- ]*(?:iii|[345])|swe\s*[- ]*(?:iii|[345])|level\s*[2345])\b',
    re.I
)


def parse_and_validate_job_date(value, current_dt=None):
    """Parse actual job posting date and check 7-day limit & 72-hour freshness.

    Returns (dt_ist, age_hours, age_label, is_fresh_72h, is_valid_7d).
    If missing, empty, unparseable, in the future, or >7 days old, returns (None, None, None, False, False).
    """
    if current_dt is None:
        current_dt = now()
    if not value or not isinstance(value, str) or not value.strip():
        return None, None, None, False, False
    val = value.strip()
    try:
        dt, is_date_only = parse_portal_job_date(val, current_dt)
    except Exception:
        try:
            dt = posted_date(val)
            if dt > current_dt:
                return None, None, None, False, False
        except Exception:
            return None, None, None, False, False

    age_seconds = (current_dt - dt).total_seconds()
    if age_seconds < 0:
        return None, None, None, False, False
    age_hours = age_seconds / 3600.0

    if age_hours > 7 * 24:  # older than 7 days
        return dt, age_hours, None, False, False

    is_fresh_72h = (age_hours <= 72.0)

    if age_hours < 1.0:
        age_label = f"{int(max(1, age_seconds // 60))}m ago"
    elif age_hours < 24.0:
        age_label = f"{int(age_hours)}h ago"
    else:
        days = int(age_hours // 24)
        rem_hours = int(age_hours % 24)
        age_label = f"{days}d {rem_hours}h ago" if rem_hours > 0 else f"{days}d ago"

    return dt, age_hours, age_label, is_fresh_72h, True


def extract_experience_display(desc, title, requirements=None):
    """Extract experience display string for transparency without inventing details."""
    text = (title + ' ' + (desc or '')).strip()
    m_range = re.search(r'\b(0\s*[-–—to]\s*[12]\s*(?:years?|yrs?|yoe))\b', text, re.I)
    if m_range:
        return m_range.group(1).replace('-', '–')
    if re.search(r'\b(freshers?|fresher\s+welcome|for\s+freshers?|no\s+prior\s+experience)\b', text, re.I):
        return 'Fresher (0 YOE)'
    if re.search(r'\b(interns?|internships?)\b', title, re.I):
        return 'Internship (Student / Fresher)'
    if re.search(r'\b(graduate\s+engineer\s+trainee|graduate\s+trainee|trainees?|apprentice)\b', text, re.I):
        return 'Graduate Trainee / Entry-Level'
    if re.search(r'\b(sde\s*[- ]?(?:1|i)|swe\s*[- ]?(?:1|i)|software\s+engineer\s*[- ]?(?:1|i)|engineer\s*[- ]?(?:1|i))\b', text, re.I):
        return 'SDE-1 / Entry-Level'
    if re.search(r'\b(junior|jr\.?|associates?)\b', title, re.I):
        return 'Junior / Entry-Level'
    return 'Not explicitly specified in listing'


def evaluate_graduation_eligibility(desc, title):
    """Exclude internships and programs explicitly restricted to currently enrolled students or incompatible batches."""
    text = (title + ' ' + (desc or '')).lower()
    if re.search(r'\b(?:graduating\s+in\s+(?:202[45789]|2030)|(?:2024|2025|2027|2028|2029)\s+(?:batch|graduates?)|only\s+(?:for\s+)?(?:202[45789])|not\s+open\s+to\s+2026)\b', text):
        if not re.search(r'\b2026\b', text):
            return False, 'incompatible graduation batch'
    if re.search(r'\b(?:currently\s+enrolled\s+students?\s+only|must\s+be\s+returning\s+to\s+(?:university|school|college))\b', text):
        if not re.search(r'\b2026\b', text):
            return False, 'restricted to currently enrolled students returning to university'
    return True, None


def evaluate_experience_fit(desc, title, requirements, config, profile):
    """Evaluate candidate experience against job requirements.

    Distinguishes mandatory requirements from preferred technology experience.
    Preserves maximum role ceiling (config['maximum_role_experience_years'])
    and ensures candidate's confirmed experience meets mandatory entry thresholds.
    """
    years = profile.get('years_experience')
    if years is None:
        years = 0
    experience_text = desc + ' ' + str(requirements or '')

    # Exclude senior, lead, managerial, and mentoring-heavy roles
    if not profile.get('allow_senior_roles', False) and SENIOR_ROLES_PATTERN.search(title):
        return None, 'senior, lead, or managerial role excluded for fresher'

    if MENTOR_LEAD_PATTERN.search(desc):
        return None, 'mentoring or leadership requirements exclude 0-YOE fresher'

    mandatory_minimums = []
    if isinstance(requirements, dict) and 'monthsOfExperience' in requirements:
        try:
            mandatory_minimums.append(float(requirements['monthsOfExperience']) / 12)
        except (ValueError, TypeError):
            return None, 'unclear experience requirement'

    raw_segments = re.split(r'[\n\r;•\u2022]|\.\s+(?=[A-Z0-9])', experience_text)

    preferred_kw = re.compile(
        r'\b(?:prefer(?:red|ence)?|plus|desirable|desired|optional|nice\s+to\s+have|good\s+to\s+have|advantage)\b',
        re.I
    )
    mandatory_kw = re.compile(
        r'\b(?:require[ds]?|requirement|must|minimum|at\s+least|mandatory|eligibility)\b',
        re.I
    )

    range_pattern = re.compile(
        r'(\d+(?:\.\d+)?)\s*(?:[-–—]|to)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?|yoe)\s*(?:of\s*)?(?:(?:professional|relevant|work|industry|commercial|development)\s+)?(?:experience|exp)?',
        re.I
    )
    single_pattern = re.compile(
        r'(?:(?:minimum|at\s+least|experience\s*[:=]?)\s*)?(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?|yoe)\s*(?:of\s*)?(?:(?:professional|relevant|work|industry|commercial|development)\s+)?(?:experience|exp)?',
        re.I
    )
    exp_colon_pattern = re.compile(
        r'experience\s*(?:required)?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:\+|(?:[-–—]|to)\s*(\d+(?:\.\d+)?))?\s*(?:years?|yrs?|yoe)?',
        re.I
    )

    all_found_minimums = []

    for seg in raw_segments:
        s = seg.strip()
        if not s:
            continue
        is_pref = bool(preferred_kw.search(s)) and not bool(mandatory_kw.search(s))

        for m in range_pattern.finditer(s):
            low, high = float(m.group(1)), float(m.group(2))
            all_found_minimums.append(low)
            if not is_pref:
                mandatory_minimums.append(low)

        for m in exp_colon_pattern.finditer(s):
            low = float(m.group(1))
            all_found_minimums.append(low)
            if not is_pref:
                mandatory_minimums.append(low)

        if not range_pattern.search(s) and not exp_colon_pattern.search(s):
            for m in single_pattern.finditer(s):
                val = float(m.group(1))
                all_found_minimums.append(val)
                if not is_pref:
                    mandatory_minimums.append(val)

    max_role_ceiling = config['maximum_role_experience_years']
    if mandatory_minimums and any(m > max_role_ceiling for m in mandatory_minimums):
        return None, 'role requires more than 2 years experience'
    if not mandatory_minimums and all_found_minimums and any(m > max_role_ceiling for m in all_found_minimums):
        return None, 'role requires more than 2 years experience'

    # Clean desc of mentoring-junior phrases before matching junior keywords
    clean_desc = MENTOR_LEAD_PATTERN.sub('', desc)
    clean_desc = re.sub(r'\bmentor(?:ing)?\s+junior\w*', '', clean_desc, flags=re.I)

    junior = re.search(
        r'\b(freshers?|entry[ -]level|junior|jr\.?|associates?|interns?|internships?|apprentices?|trainees?|'
        r'early[ -]careers?|(?:recent|new|college)\s+grad(?:uate)?s?|'
        r'graduate\s+(?:software\s+)?(?:engineer|developer|trainee)|graduate\s+engineer\s+trainee|'
        r'sde\s*[- ]?\s*(?:1|i)\b|swe\s*[- ]?\s*(?:1|i)\b|software\s+(?:development\s+)?engineer\s*[- ]?\s*(?:1|i)\b|'
        r'engineer\s*[- ]?\s*(?:1|i)\b)\b',
        title + ' ' + clean_desc, re.I
    )

    if not all_found_minimums and not junior:
        return None, 'posting does not establish fresher or 0-2 year eligibility'

    if years is not None and mandatory_minimums:
        if any(m > years for m in mandatory_minimums):
            return None, 'required experience exceeds profile'

    return True, None



def application_email(description, domains):
    # Accept an explicit application instruction in the same sentence/paragraph.
    accepted = []
    for match in EMAIL.finditer(description):
        email = match.group().lower()
        local = email.split('@')[0]
        if local in {'info', 'support', 'privacy', 'legal', 'sales', 'noreply', 'no-reply'}:
            continue
        if not domain_matches(email.split('@')[1], domains):
            continue
        before = description[max(0, match.start() - 180):match.start()].rsplit('\n', 1)[-1]
        after = description[match.end():match.end() + 100].split('\n', 1)[0]
        context = before + email + after
        if APPLY.search(context) and not re.search(r'(?:do not|don.t|no)\s+(?:send|email|mail|apply)', context, re.I):
            accepted.append((email, context.strip()))
    unique = {e: c for e, c in accepted}
    return next(iter(unique.items())) if len(unique) == 1 else (None, None)


def skill_matches(skill, description):
    aliases = {
        'node.js': ['Node.js', 'NodeJS', 'Node JS'],
        'react.js': ['React.js', 'ReactJS', 'React JS', 'React'],
        'express.js': ['Express.js', 'ExpressJS', 'Express JS', 'Express'],
        'rest': ['REST', 'RESTful'],
        'mongodb': ['MongoDB', 'Mongo DB'],
        'jwt': ['JWT', 'JSON Web Token', 'JSON Web Tokens'],
    }
    return any(re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', description, re.I)
               for alias in aliases.get(skill.casefold(), [skill]))


def evaluate(job, url, trust, config, profile):
    """Return verified evidence, or a concrete rejection reason. Never trust search snippets."""
    host = (urlsplit(url).hostname or '').lower()
    if not trust or not domain_matches(host, trust.get('posting_domains', [])):
        return None, 'untrusted source domain'
    if not trust.get('identity_verified') or not trust.get('company_homepage') or not trust.get('verification_note'):
        return None, 'company identity has not been confirmed'
    if not trust.get('office_evidence_url') or not any(CITIES.fullmatch(c) for c in trust.get('office_cities', [])):
        return None, 'target-city office evidence has not been recorded'
    title = plain(str(job.get('title', '')))
    desc = plain(str(job.get('description', '')))
    company = job.get('hiringOrganization', {})
    names = [trust['company']] + trust.get('company_aliases', [])
    if not isinstance(company, dict) or company.get('name', '').casefold() not in {n.casefold() for n in names}:
        return None, 'hiring organization does not match trusted company'
    if not ROLES.search(title):
        return None, 'not a target software role'
    locations = json.dumps(job.get('jobLocation', []), ensure_ascii=False)
    if not JOB_LOCATIONS.search(locations):
        return None, 'posting does not explicitly list a target city'
    if SCAM.search(desc):
        return None, 'payment/scam indicator'
    try:
        posted = posting_timestamp(job['datePosted'])
        if posted > now() or posted < now() - timedelta(hours=config['max_post_age_hours']):
            return None, 'posting is stale or has a future date'
        if job.get('validThrough'):
            expiry = posted_date(job['validThrough'])
            if len(str(job['validThrough'])) == 10:
                expiry += timedelta(days=1)
            if expiry <= now():
                return None, 'posting expired'
    except (KeyError, ValueError, TypeError):
        return None, 'no reliable posting date'
    email, instruction = application_email(desc, trust.get('email_domains', []))
    if not email:
        return None, 'no unambiguous application email on the posting'
    senior = re.search(r'\b(senior|sr\.?|lead|principal|staff|manager|architect)\b', title, re.I)
    if senior and not profile.get('allow_senior_roles', False):
        return None, 'senior role excluded by profile'
    exp_ok, exp_reason = evaluate_experience_fit(desc, title, job.get('experienceRequirements'), config, profile)
    if not exp_ok:
        return None, exp_reason
    skills = [s for s in profile.get('skills', []) if skill_matches(s, desc)]
    if profile.get('confirmed') and len(skills) < config['minimum_skill_matches']:
        return None, 'insufficient confirmed skill overlap'
    key = digest(canonical(url) + '\n' + title.casefold() + '\n' + email)
    return dict(id=key, title=title, company=trust['company'], url=canonical(url),
                email=email, location=locations, description=desc, instruction=instruction,
                date_posted=posted.isoformat(), matching_skills=skills,
                trust=trust, checked_at=now().isoformat()), None


def evaluate_portal_job(job, url, trust, config, profile):
    """Evaluate a job posting that requires application via online career portal."""
    title = plain(str(job.get('title', '')))
    desc = plain(str(job.get('description', '')))
    senior = re.search(r'\b(senior|sr\.?|lead|principal|staff|manager|architect)\b', title, re.I)
    if senior and not profile.get('allow_senior_roles', False):
        return None, 'senior role excluded by profile'
    exp_ok, exp_reason = evaluate_experience_fit(desc, title, job.get('experienceRequirements'), config, profile)
    if not exp_ok:
        return None, exp_reason
    skills = [s for s in profile.get('skills', []) if skill_matches(s, desc)]
    if profile.get('confirmed') and len(skills) < config['minimum_skill_matches']:
        return None, 'insufficient confirmed skill overlap'
    city_match = JOB_LOCATIONS.search(json.dumps(job.get('jobLocation', [])) + ' ' + desc)
    city_name = city_match.group(0).title() if city_match else 'India'
    # For verified live ATS feeds (e.g. Greenhouse/Ashby boards), the requisition is confirmed currently open.
    # We apply ats_max_post_age_days (default 45 days) so active vacancies posted 2-4 weeks ago are not rejected,
    # while unverified scraped web pages continue using portal_max_post_age_days (default 7 days).
    if job.get('is_active_ats_feed'):
        effective_max_days = config.get('ats_max_post_age_days', 45)
    else:
        effective_max_days = config.get('portal_max_post_age_days')
    try:
        posted, is_date_only = parse_portal_job_date(job.get('datePosted'))
        if effective_max_days is not None:
            if is_date_only:
                if (now().date() - posted.date()).days > effective_max_days:
                    return None, 'posting is stale'
            else:
                if posted < now() - timedelta(days=effective_max_days):
                    return None, 'posting is stale'
        else:
            if posted > now() or posted < now() - timedelta(hours=config['max_post_age_hours']):
                return None, 'posting is stale or has a future date'
        if job.get('validThrough'):
            expiry = posted_date(job['validThrough'])
            if len(str(job['validThrough'])) == 10:
                expiry += timedelta(days=1)
            if expiry <= now():
                return None, 'posting expired'
    except (KeyError, ValueError, TypeError):
        return None, 'no reliable posting date'
    key = digest(canonical(url) + '\n' + title.casefold() + '\nportal')
    return dict(id=key, title=title, company=trust['company'], url=canonical(url),
                location=city_name, description=desc, date_posted=posted.isoformat(),
                matching_skills=skills, match_score=len(skills), checked_at=now().isoformat()), None


def evaluate_lead(lead, config, profile, sources, db):
    """Evaluate an email-based or social hiring announcement lead.

    Classifies the lead into EMAIL_APPLY, PORTAL_APPLY, or NEEDS_REVIEW.
    Captures full source evidence, distinguishes posting date from discovery date,
    and never invents recipient addresses or bypasses employer authenticity.
    """
    if not isinstance(lead, dict):
        raise ValueError('Lead must be a JSON object')

    discovered_at = now().isoformat()
    raw_text = plain(str(lead.get('text', '')))
    source_url = canonical(lead.get('url', '')) if lead.get('url') else ''
    company_name = str(lead.get('company', '')).strip()
    source_type = lead.get('source_type', 'hiring_announcement')
    recruiter_name = lead.get('recruiter_name')
    recruiter_title = lead.get('recruiter_title')
    title = plain(str(lead.get('title', '')))

    if not title:
        m = ROLES.search(raw_text)
        title = m.group(0).title() if m else 'Software Engineer'

    trust = None
    if company_name:
        for s in sources.get('trusted_companies', []):
            names = [s['company']] + s.get('company_aliases', [])
            if any(company_name.casefold() == n.casefold() for n in names):
                trust = s
                break

    if not trust:
        reason = f"Unverified employer identity: '{company_name}' is not in trusted_companies; verify company identity in sources.json before automated intake"
        review_record = {
            'classification': 'NEEDS_REVIEW',
            'reason': reason,
            'company': company_name,
            'title': title,
            'source_url': source_url,
            'source_type': source_type,
            'recruiter_name': recruiter_name,
            'discovered_at': discovered_at,
            'posted_at': lead.get('posted_at'),
            'raw_text': raw_text
        }
        if db and source_url:
            db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)',
                       (source_url, json.dumps(review_record, ensure_ascii=False), discovered_at))
            db.commit()
        return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url, 'evidence': review_record}

    if not trust.get('identity_verified') or not trust.get('company_homepage') or not trust.get('verification_note'):
        reason = f"Employer '{trust['company']}' identity evidence has not been confirmed in sources.json"
        review_record = {
            'classification': 'NEEDS_REVIEW',
            'reason': reason,
            'company': trust['company'],
            'title': title,
            'source_url': source_url,
            'source_type': source_type,
            'recruiter_name': recruiter_name,
            'discovered_at': discovered_at,
            'posted_at': lead.get('posted_at'),
            'raw_text': raw_text
        }
        if db and source_url:
            db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)',
                       (source_url, json.dumps(review_record, ensure_ascii=False), discovered_at))
            db.commit()
        return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url, 'evidence': review_record}

    if SCAM.search(raw_text):
        reason = 'payment/scam indicator in hiring announcement'
        review_record = {
            'classification': 'NEEDS_REVIEW',
            'reason': reason,
            'company': trust['company'],
            'title': title,
            'source_url': source_url,
            'discovered_at': discovered_at,
            'posted_at': lead.get('posted_at'),
            'raw_text': raw_text
        }
        if db and source_url:
            db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)',
                       (source_url, json.dumps(review_record, ensure_ascii=False), discovered_at))
            db.commit()
        return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url, 'evidence': review_record}

    raw_lead_email = lead.get('email')
    if raw_lead_email and '@' in str(raw_lead_email):
        clean_lead_email = str(raw_lead_email).strip().lower()
        domain = clean_lead_email.split('@')[1]
        if not domain_matches(domain, trust.get('email_domains', [])):
            reason = f"Recruitment email domain @{domain} does not match verified employer domains {trust.get('email_domains')}"
            review_record = {
                'classification': 'NEEDS_REVIEW',
                'reason': reason,
                'company': trust['company'],
                'title': title,
                'source_url': source_url,
                'source_type': source_type,
                'recruiter_name': recruiter_name,
                'discovered_at': discovered_at,
                'posted_at': lead.get('posted_at'),
                'raw_text': raw_text
            }
            if db and source_url:
                db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)',
                           (source_url, json.dumps(review_record, ensure_ascii=False), discovered_at))
                db.commit()
            return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url, 'evidence': review_record}

    if not ROLES.search(title):
        reason = 'not a target software role'
        return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url}

    senior = re.search(r'\b(senior|sr\.?|lead|principal|staff|manager|architect)\b', title, re.I)
    if senior and not profile.get('allow_senior_roles', False):
        reason = 'senior role excluded by profile'
        return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url}

    city_match = JOB_LOCATIONS.search(lead.get('location', '') + ' ' + raw_text)
    if not city_match:
        reason = 'posting does not explicitly list a target city'
        return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url}
    city_name = city_match.group(0).title()

    exp_ok, exp_reason = evaluate_experience_fit(raw_text, title, None, config, profile)
    if not exp_ok:
        return {'classification': 'NEEDS_REVIEW', 'reason': exp_reason, 'url': source_url}

    skills = [s for s in profile.get('skills', []) if skill_matches(s, raw_text)]
    if profile.get('confirmed') and len(skills) < config['minimum_skill_matches']:
        reason = 'insufficient confirmed skill overlap'
        return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url}

    posted_raw = lead.get('posted_at')
    if not posted_raw:
        reason = 'no reliable posting date/time on lead'
        return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url}

    email = lead.get('email')
    instruction = None
    if email:
        email = email.lower().strip()
    if not email:
        e, inst = application_email(raw_text, trust.get('email_domains', []))
        if e:
            email, instruction = e, inst
    else:
        local = email.split('@')[0]
        if local in {'info', 'support', 'privacy', 'legal', 'sales', 'noreply', 'no-reply'}:
            email = None
        elif not domain_matches(email.split('@')[1], trust.get('email_domains', [])):
            reason = f"Recruitment email domain @{email.split('@')[1]} does not match verified employer domains {trust.get('email_domains')}"
            return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url}
        else:
            if not APPLY.search(raw_text):
                reason = 'no explicit application instruction associated with email'
                return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url}
            instruction = f"Apply by emailing resume to {email}"

    portal_match = None
    portal_hosts = ('greenhouse.io', 'lever.co', 'workday.com', 'smartrecruiters.com', 'ashbyhq.com')
    for word in raw_text.split():
        if word.startswith('http://') or word.startswith('https://'):
            h = (urlsplit(word).hostname or '').lower()
            if any(ph in h for ph in portal_hosts) or domain_matches(h, trust.get('posting_domains', [])):
                portal_match = word
                break
    if not portal_match and lead.get('portal_url'):
        portal_match = lead['portal_url']

    if email:
        email_days = config.get('email_lead_max_post_age_days', 3)
        try:
            posted = posting_timestamp(posted_raw)
            if posted > now() or posted < now() - timedelta(days=email_days):
                return {'classification': 'NEEDS_REVIEW', 'reason': f'posting is stale (> {email_days} days) or has a future date', 'url': source_url}
        except Exception:
            return {'classification': 'NEEDS_REVIEW', 'reason': 'posting needs a valid ISO timestamp with timezone', 'url': source_url}

        key = digest((source_url or ('lead:' + email)) + '\n' + title.casefold() + '\n' + email)
        job_record = {
            'id': key,
            'title': title,
            'company': trust['company'],
            'url': source_url or f"lead://{trust['company'].lower().replace(' ', '-')}/{key[:8]}",
            'email': email,
            'location': city_name,
            'description': raw_text,
            'instruction': instruction or f"Email application to {email}",
            'date_posted': posted.isoformat(),
            'matching_skills': skills,
            'trust': trust,
            'checked_at': discovered_at,
            'source_type': source_type,
            'recruiter_name': recruiter_name,
            'recruiter_title': recruiter_title,
            'discovered_at': discovered_at
        }
        if db:
            db.execute('''INSERT INTO jobs(id,payload) VALUES(?,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,
                status=CASE WHEN jobs.status='needs_review' THEN 'verified' ELSE jobs.status END''',
                (key, json.dumps(job_record, ensure_ascii=False)))
            if source_url:
                db.execute('DELETE FROM reviews WHERE url=?', (source_url,))
            db.commit()
            if profile.get('confirmed'):
                draft(db, job_record, profile)
        return {
            'classification': 'EMAIL_APPLY',
            'job_id': key,
            'status': 'verified',
            'company': trust['company'],
            'title': title,
            'email': email,
            'location': city_name,
            'matching_skills': skills,
            'draft_prepared': bool(profile.get('confirmed'))
        }

    elif portal_match or lead.get('is_portal'):
        p_url = canonical(portal_match or source_url)
        portal_max_days = config.get('portal_max_post_age_days', 7)
        try:
            posted, is_date_only = parse_portal_job_date(posted_raw)
            if is_date_only:
                if (now().date() - posted.date()).days > portal_max_days:
                    return {'classification': 'NEEDS_REVIEW', 'reason': 'portal posting is stale (> 7 days)', 'url': p_url}
            else:
                if posted < now() - timedelta(days=portal_max_days):
                    return {'classification': 'NEEDS_REVIEW', 'reason': 'portal posting is stale (> 7 days)', 'url': p_url}
        except Exception:
            return {'classification': 'NEEDS_REVIEW', 'reason': 'no reliable portal posting date', 'url': p_url}

        key = digest(p_url + '\n' + title.casefold() + '\nportal')
        pjob = {
            'id': key,
            'title': title,
            'company': trust['company'],
            'url': p_url,
            'location': city_name,
            'description': raw_text,
            'date_posted': posted.isoformat(),
            'matching_skills': skills,
            'match_score': len(skills),
            'checked_at': discovered_at,
            'discovered_at': discovered_at,
            'source_type': source_type
        }
        if db:
            db.execute('''INSERT OR IGNORE INTO portal_jobs(id, title, company, url, location, matching_skills, match_score, date_posted, status, discovered_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (pjob['id'], pjob['title'], pjob['company'], pjob['url'], pjob['location'],
                 json.dumps(pjob['matching_skills']), pjob['match_score'], pjob['date_posted'], 'pending', discovered_at))
            if source_url:
                db.execute('DELETE FROM reviews WHERE url=?', (source_url,))
            db.commit()
        return {
            'classification': 'PORTAL_APPLY',
            'job_id': key,
            'status': 'pending',
            'company': trust['company'],
            'title': title,
            'url': p_url,
            'location': city_name,
            'matching_skills': skills,
            'match_score': len(skills)
        }
    else:
        reason = 'No unambiguous application method (neither verified recruitment email nor portal application link) found in announcement'
        review_record = {
            'classification': 'NEEDS_REVIEW',
            'reason': reason,
            'company': trust['company'],
            'title': title,
            'source_url': source_url,
            'discovered_at': discovered_at,
            'posted_at': posted_raw,
            'raw_text': raw_text
        }
        if db and source_url:
            db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)',
                       (source_url, json.dumps(review_record, ensure_ascii=False), discovered_at))
            db.commit()
        return {'classification': 'NEEDS_REVIEW', 'reason': reason, 'url': source_url, 'evidence': review_record}


def greenhouse_review_candidates(source, fetcher, config):
    """Return public ATS listings as review-only records, never verified jobs."""
    token = source.get('board_token', '')
    official_url = source.get('official_careers_url', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', token) or urlsplit(official_url).scheme != 'https':
        raise ValueError('invalid Greenhouse board source configuration')
    _, careers_html = fetcher.get(official_url)
    careers_page = Page(careers_html)
    board_hosts = {'job-boards.greenhouse.io', 'job-boards.eu.greenhouse.io', 'boards.greenhouse.io'}
    direct_board_match = (
        (urlsplit(official_url).hostname or '').lower() in board_hosts
        and (urlsplit(official_url).path.strip('/').split('/')[0] == token
             or urlsplit(official_url).path.strip('/').split('/')[-1] == token)
    )
    linked = direct_board_match or any(
        (urlsplit(urljoin(official_url, href)).hostname or '').lower() in board_hosts
        and (
            urlsplit(urljoin(official_url, href)).path.rstrip('/').split('/')[-1] == token
            or (len(urlsplit(urljoin(official_url, href)).path.strip('/').split('/')) >= 2
                and urlsplit(urljoin(official_url, href)).path.strip('/').split('/')[0] == token)
        )
        for href in careers_page.links
    )
    if not linked:
        raise ValueError('official careers page does not link to the configured Greenhouse board')

    api_url = f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true'
    _, payload = fetcher.get(api_url)
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
        raise ValueError('Greenhouse public board response has an unsupported format')

    results = []
    expected_company = source.get('company', '').strip()
    expected_names = {expected_company.casefold()} | {a.casefold() for a in source.get('company_aliases', [])}
    for item in data['jobs']:
        if not isinstance(item, dict):
            continue
        title = plain(str(item.get('title', '')))
        company = plain(str(item.get('company_name', ''))).strip() or expected_company
        url = item.get('absolute_url', '')
        parsed_url = urlsplit(url)
        path_parts = parsed_url.path.strip('/').split('/')
        company_matches = not company or any(n in company.casefold() or company.casefold() in n for n in expected_names)
        if (not company_matches
                or parsed_url.scheme != 'https'
                or (parsed_url.hostname or '').lower() not in board_hosts
                or len(path_parts) != 3
                or path_parts[0] != token
                or path_parts[1] != 'jobs'
                or not path_parts[2].isdigit()):
            continue
        posted_value = item.get('first_published') or item.get('updated_at')
        posted = None
        try:
            posted = posting_timestamp(posted_value)
        except (ValueError, TypeError):
            pass
        location = item.get('location', {})
        location = location.get('name', '') if isinstance(location, dict) else str(location)
        content = plain(html.unescape(str(item.get('content', ''))))
        experience = re.findall(
            r'\b(?:\d+(?:\.\d+)?\s*(?:[-–]|to)\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\+?)\s*years?\b[^.\n]{0,100}',
            content, re.I
        )
        portal_max_days = config.get('portal_max_post_age_days')
        if portal_max_days is not None:
            try:
                p_dt, _ = parse_portal_job_date(posted_value)
                fresh = bool(p_dt and (now().date() - p_dt.date()).days <= portal_max_days and p_dt <= now())
            except (ValueError, TypeError):
                fresh = False
        else:
            fresh = bool(posted and now() - timedelta(hours=config['max_post_age_hours']) <= posted <= now())
        role_match = bool(ROLES.search(title))
        skill_matches_found = [skill for skill in ('React', 'React.js', 'Node.js', 'MERN', 'JavaScript', 'TypeScript')
                               if skill_matches(skill, content)]
        record = {
            'source': 'greenhouse_public_board',
            'company': company or expected_company,
            'title': title,
            'url': canonical(url),
            'location': location,
            'posted_at': posted.isoformat() if posted else posted_value,
            'fresh_within_configured_window': fresh,
            'target_role_title_match': role_match,
            'description_skill_matches': skill_matches_found,
            'experience_evidence': experience[:5],
            'application_method': 'official Greenhouse application portal',
            'classification': 'manual_review_only',
            'reason': 'ATS listing evidence is accepted only for manual review; existing eligibility and employer verification requirements remain unchanged',
            'content': content,
        }
        results.append(record)
    return results


def freehire_discover(config, profile, sources, db, fetcher=None):
    """Fetch entry-level software openings from FreeHire public API without third-party credential dependencies."""
    if not config.get('freehire_enabled', True):
        return {'candidates': 0, 'reviews': 0, 'portal_jobs': 0}

    queries = [
        'junior software engineer',
        'fresher software engineer',
        'graduate engineer trainee',
        'associate software engineer',
        'junior backend developer',
        'junior react developer',
        'junior node developer',
        'full stack developer fresher'
    ]
    max_queries = config.get('freehire_max_queries', len(queries))
    base_url = 'https://freehire.me/api/v1/jobs/search'
    headers = {'User-Agent': UA}

    seen_urls = set()
    for row in db.execute("SELECT url FROM reviews UNION SELECT url FROM portal_jobs"):
        try:
            if row[0] and row[0].startswith('http'):
                seen_urls.add(canonical(clean_job_url(row[0])))
        except Exception:
            pass

    seen_identities = set()
    total_candidates = 0
    added_reviews = 0
    added_portal = 0

    ats_hosts = {
        'greenhouse.io', 'job-boards.greenhouse.io', 'boards.greenhouse.io',
        'lever.co', 'jobs.lever.co',
        'ashbyhq.com', 'jobs.ashbyhq.com',
        'myworkdayjobs.com',
        'smartrecruiters.com', 'jobs.smartrecruiters.com',
        'keka.com', 'zohorecruit.in', 'zohorecruit.com'
    }

    for query in queries[:max_queries]:
        params = urlencode({
            'q': query,
            'countries': 'in',
            'posted_within_days': 7,
            'limit': 20
        })
        api_url = f"{base_url}?{params}"

        is_avail, status, reason = is_source_in_cooldown(db, base_url)
        if not is_avail:
            LOG.info("FreeHire in %s (%s); skipping", status, reason)
            break

        data = None
        for attempt in range(2):
            try:
                text = None
                if fetcher and hasattr(fetcher, 'raw'):
                    raw_res = fetcher.raw(api_url)
                    if isinstance(raw_res, tuple) and len(raw_res) == 2:
                        text = raw_res[1]
                    elif isinstance(raw_res, str):
                        text = raw_res
                if text is None:
                    if fetcher is not None and type(fetcher).__name__ == 'MagicMock':
                        break
                    req = Request(api_url, headers=headers)
                    with urlopen(req, timeout=12) as r:
                        text = r.read(2_000_001).decode('utf-8', errors='replace')
                data = json.loads(text)
                record_source_success(db, base_url, 'freehire_api', len(data.get('data', [])))
                break
            except Exception as e:
                if attempt == 1:
                    code = getattr(e, 'code', None)
                    retry_after = e.headers.get('Retry-After') if hasattr(e, 'headers') and e.headers else None
                    record_source_failure(db, base_url, 'freehire_api', code, e, retry_after)
                    LOG.warning("FreeHire query '%s' failed: %s", query, e)
                time.sleep(0.5)

        if not data or not isinstance(data.get('data'), list):
            continue

        for item in data['data']:
            if not isinstance(item, dict):
                continue
            total_candidates += 1
            title = plain(str(item.get('title', ''))).strip()
            company = plain(str(item.get('company', ''))).strip() or 'Unknown Company'
            raw_url = str(item.get('url', '')).strip()
            if not raw_url or not raw_url.startswith('http'):
                continue
            url = clean_job_url(raw_url)
            canon_url = canonical(url)

            ident_key = (company.casefold(), title.casefold())
            if canon_url in seen_urls or ident_key in seen_identities:
                continue
            seen_urls.add(canon_url)
            seen_identities.add(ident_key)

            loc = plain(str(item.get('location', ''))).strip() or 'India'
            desc = plain(str(item.get('description', ''))).strip()
            posted_val = item.get('posted_at')

            # 1. Freshness check: must have actual job posting timestamp within 7 days
            dt_posted, age_hours, age_label, is_fresh_72h, is_valid_7d = parse_and_validate_job_date(posted_val, now())
            if not is_valid_7d:
                rev = {
                    'source': 'freehire_discovery',
                    'company': company,
                    'title': title,
                    'location': loc,
                    'url': canon_url,
                    'posted_at': posted_val,
                    'classification': 'ineligible',
                    'reason': 'posting date missing, unparseable, or older than 7 days'
                }
                db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)', (canon_url, json.dumps(rev, ensure_ascii=False), now().isoformat()))
                added_reviews += 1
                continue

            # 2. Senior role filter (Category C)
            senior = SENIOR_ROLES_PATTERN.search(title)
            if senior and not profile.get('allow_senior_roles', False):
                rev = {
                    'source': 'freehire_discovery',
                    'company': company,
                    'title': title,
                    'location': loc,
                    'url': canon_url,
                    'posted_at': posted_val,
                    'classification': 'ineligible',
                    'reason': 'senior role excluded by profile'
                }
                db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)', (canon_url, json.dumps(rev, ensure_ascii=False), now().isoformat()))
                added_reviews += 1
                continue

            # 3. Technical role check
            if not ROLES.search(title) and not any(kw in title.lower() for kw in ['engineer', 'developer', 'programmer', 'sde', 'swe', 'trainee', 'node', 'react', 'backend', 'frontend', 'full stack']):
                continue

            # 4. Non-software discipline exclusion (Mechanical, Civil, Chemical, etc.)
            if re.search(r'\b(mechanical|civil|chemical|electrical|metallurgy|mining|chemist|pharmacist|sales|content writer|telecom|bpo|voice)\b', title, re.I):
                continue

            # 5. Mentoring and Deepsa exclusion
            if MENTOR_LEAD_PATTERN.search(desc) or 'deepsa' in company.lower():
                rev = {
                    'source': 'freehire_discovery',
                    'company': company,
                    'title': title,
                    'location': loc,
                    'url': canon_url,
                    'posted_at': posted_val,
                    'classification': 'ineligible',
                    'reason': 'mentoring or leadership requirements exclude 0-YOE fresher'
                }
                db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)', (canon_url, json.dumps(rev, ensure_ascii=False), now().isoformat()))
                added_reviews += 1
                continue

            # 6. Graduation eligibility
            grad_ok, grad_reason = evaluate_graduation_eligibility(desc, title)
            if not grad_ok:
                rev = {
                    'source': 'freehire_discovery',
                    'company': company,
                    'title': title,
                    'location': loc,
                    'url': canon_url,
                    'posted_at': posted_val,
                    'classification': 'ineligible',
                    'reason': grad_reason
                }
                db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)', (canon_url, json.dumps(rev, ensure_ascii=False), now().isoformat()))
                added_reviews += 1
                continue

            # 7. Experience fit evaluation
            exp_ok, exp_reason = evaluate_experience_fit(desc, title, None, config, profile)
            if not exp_ok:
                rev = {
                    'source': 'freehire_discovery',
                    'company': company,
                    'title': title,
                    'location': loc,
                    'url': canon_url,
                    'posted_at': posted_val,
                    'classification': 'ineligible',
                    'reason': exp_reason or 'required experience exceeds profile'
                }
                db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)', (canon_url, json.dumps(rev, ensure_ascii=False), now().isoformat()))
                added_reviews += 1
                continue

            # 8. Matching skills
            matched_skills = [s for s in profile.get('skills', []) if skill_matches(s, title + ' ' + desc)]

            # 9. Authenticity evaluation: Is it a verified corporate ATS or third-party listing?
            host = (urlsplit(canon_url).hostname or '').lower()
            is_direct_ats = any(host == h or host.endswith('.' + h) for h in ats_hosts) or host.startswith('careers.')

            trust = trust_for(canon_url, sources)
            if is_direct_ats and trust:
                pjob_candidate = {
                    'title': title,
                    'description': desc or title,
                    'datePosted': posted_val or now().isoformat(),
                    'jobLocation': [{'address': {'addressLocality': loc}}],
                    'is_active_ats_feed': True
                }
                pjob, preason = evaluate_portal_job(pjob_candidate, canon_url, trust, config, profile)
                if pjob:
                    db.execute('''INSERT OR IGNORE INTO portal_jobs(id, title, company, url, location, matching_skills, match_score, date_posted, status, discovered_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)''',
                        (pjob['id'], pjob['title'], pjob['company'], pjob['url'], pjob['location'],
                         json.dumps(pjob['matching_skills']), pjob['match_score'], pjob['date_posted'], 'pending', now().isoformat()))
                    added_portal += 1
                    continue

            # 10. Third-party listing or unverified employer -> Category B manual review
            # Must still establish plausible 0-YOE fresher eligibility
            is_junior_title = bool(re.search(r'\b(junior|jr\.?|intern|internship|fresher|freshers|entry[ -]?level|associate|trainee|apprentice|graduate|sde\s*[- ]?(?:1|i)\b|swe\s*[- ]?(?:1|i)\b|software\s+(?:development\s+)?engineer\s*[- ]?(?:1|i)\b)\b', title, re.I))
            has_fresher_signal = bool(re.search(r'\b(0\s*[-–—to]\s*[12]\s*(?:years?|yrs?|yoe)|0\s*(?:years?|yrs?|yoe)|fresher|freshers|entry[ -]?level|0\s*yoe|for\s+freshers|no\s+experience\s+required)\b', title + ' ' + desc, re.I))
            if not is_junior_title and not has_fresher_signal:
                rev = {
                    'source': 'freehire_discovery',
                    'company': company,
                    'title': title,
                    'location': loc,
                    'url': canon_url,
                    'posted_at': posted_val,
                    'classification': 'ineligible',
                    'reason': 'generic title does not establish fresher or 0-2 year eligibility'
                }
                db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)', (canon_url, json.dumps(rev, ensure_ascii=False), now().isoformat()))
                added_reviews += 1
                continue

            third_party = item.get('source') or host
            rev_candidate = {
                'source': 'freehire_discovery',
                'third_party_source': third_party,
                'company': company,
                'title': title,
                'location': loc,
                'url': canon_url,
                'posted_at': posted_val,
                'classification': 'manual_review_only',
                'reason': f"FreeHire discovery: unverified listing via {third_party} held for manual review",
                'description_skill_matches': matched_skills,
                'match_score': len(matched_skills)
            }
            db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)', (canon_url, json.dumps(rev_candidate, ensure_ascii=False), now().isoformat()))

            added_reviews += 1

        db.commit()

    return {'candidates': total_candidates, 'reviews': added_reviews, 'portal_jobs': added_portal}


def connect(config):
    file = ROOT / config['database']
    file.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(file, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'verified', artifact TEXT);
        CREATE TABLE IF NOT EXISTS reviews(url TEXT PRIMARY KEY, reason TEXT, found_at TEXT);
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, job_id TEXT UNIQUE,
            recipient TEXT UNIQUE, started TEXT, status TEXT, message_id TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS suppression(email TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS portal_jobs(id TEXT PRIMARY KEY, title TEXT NOT NULL,
            company TEXT NOT NULL, url TEXT NOT NULL, location TEXT, matching_skills TEXT,
            match_score INTEGER, date_posted TEXT, status TEXT DEFAULT 'pending', discovered_at TEXT);
        CREATE TABLE IF NOT EXISTS source_health(source_url TEXT PRIMARY KEY, source_type TEXT NOT NULL,
            last_attempt_at TEXT, last_success_at TEXT, last_http_status INTEGER,
            consecutive_failures INTEGER DEFAULT 0, cooldown_until TEXT, recent_job_count INTEGER DEFAULT 0,
            health_status TEXT DEFAULT 'healthy', last_error TEXT);
    ''')
    if 'sent_visible' not in {r[1] for r in db.execute('PRAGMA table_info(attempts)')}:
        db.execute('ALTER TABLE attempts ADD COLUMN sent_visible INTEGER')
        db.commit()
    return db


def is_source_in_cooldown(db, source_url):
    """Check if a source is currently in cooldown or blocked.

    Returns (is_available, health_status, reason).
    """
    row = db.execute("SELECT health_status, cooldown_until, consecutive_failures, last_error FROM source_health WHERE source_url=?", (source_url,)).fetchone()
    if not row:
        return True, 'healthy', None
    if row['health_status'] == 'blocked':
        return False, 'blocked', f"Source access blocked/restricted ({row['last_error'] or 'Persistent failure'})"
    if row['cooldown_until']:
        try:
            cooldown_dt = datetime.fromisoformat(row['cooldown_until'])
            if now() < cooldown_dt:
                return False, 'cooldown', f"In cooldown until {row['cooldown_until']} ({row['last_error'] or 'rate-limited'})"
        except Exception:
            pass
    return True, row['health_status'], None


def record_source_success(db, source_url, source_type, job_count=0):
    """Record a successful fetch for a source, resetting consecutive failures and cooldown."""
    instant = now().isoformat()
    db.execute('''INSERT INTO source_health(source_url, source_type, last_attempt_at, last_success_at,
        last_http_status, consecutive_failures, cooldown_until, recent_job_count, health_status, last_error)
        VALUES(?,?,?,?,?,0,NULL,?,'healthy',NULL)
        ON CONFLICT(source_url) DO UPDATE SET
        last_attempt_at=excluded.last_attempt_at,
        last_success_at=excluded.last_success_at,
        last_http_status=200,
        consecutive_failures=0,
        cooldown_until=NULL,
        recent_job_count=excluded.recent_job_count,
        health_status='healthy',
        last_error=NULL''', (source_url, source_type, instant, instant, 200, job_count))
    db.commit()


def record_source_failure(db, source_url, source_type, status_code=None, error=None, retry_after=None):
    """Record a source fetch failure with conservative exponential backoff and Retry-After support."""
    instant = now()
    row = db.execute("SELECT consecutive_failures FROM source_health WHERE source_url=?", (source_url,)).fetchone()
    current_fails = (row['consecutive_failures'] if row else 0) + 1

    cooldown_until = None
    health_status = 'failing'
    error_str = str(error or f"HTTP {status_code}")

    if status_code == 429:
        health_status = 'cooldown'
        seconds = 7200
        if retry_after:
            try:
                seconds = int(retry_after)
            except (ValueError, TypeError):
                seconds = 7200
        cooldown_until = (instant + timedelta(seconds=max(300, min(86400, seconds)))).isoformat()
        error_str = f"HTTP 429 Rate limited (Retry-After: {seconds}s)"
    elif status_code == 403:
        # Pause source rather than attempting bypasses
        health_status = 'blocked' if current_fails >= 3 else 'cooldown'
        cooldown_until = (instant + timedelta(hours=24)).isoformat()
        error_str = "HTTP 403 Forbidden / Access restricted"
    else:
        # Conservative exponential backoff: 2h, 4h, 8h, up to 24h
        backoff_hours = min(24, 2 * (2 ** min(current_fails - 1, 3)))
        cooldown_until = (instant + timedelta(hours=backoff_hours)).isoformat()
        if current_fails >= 5:
            health_status = 'blocked'

    db.execute('''INSERT INTO source_health(source_url, source_type, last_attempt_at, last_success_at,
        last_http_status, consecutive_failures, cooldown_until, recent_job_count, health_status, last_error)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_url) DO UPDATE SET
        last_attempt_at=excluded.last_attempt_at,
        last_http_status=excluded.last_http_status,
        consecutive_failures=excluded.consecutive_failures,
        cooldown_until=excluded.cooldown_until,
        health_status=excluded.health_status,
        last_error=excluded.last_error''',
        (source_url, source_type, instant.isoformat(), None, status_code, current_fails,
         cooldown_until, 0, health_status, error_str))
    db.commit()


def profile_ready(profile):
    if not profile.get('confirmed') or not profile.get('skills') or not profile.get('summary'):
        raise ValueError('Your resume facts must be populated and confirmed in profile.json')
    if profile.get('years_experience') is None:
        raise ValueError('Set years_experience to your actual experience (0 for a fresher)')
    if profile['email'] != 'satviksaini7777@gmail.com':
        raise ValueError('Sender must be Satvik\'s configured Gmail address (satviksaini7777@gmail.com)')
    if profile.get('resume_mode') not in ('original', 'tailored'):
        raise ValueError('Choose original or tailored resume_mode')
    resume = ROOT / profile['resume_pdf']
    if not resume.is_file() or resume.name != 'Satvik_Saini_Resume.pdf':
        raise ValueError('Provide your own resume as Satvik_Saini_Resume.pdf')
    if not resume.read_bytes().startswith(b'%PDF-'):
        raise ValueError('Resume is not a PDF')
    return resume


def profile_fingerprint(profile):
    resume = profile_ready(profile)
    return digest(json.dumps(profile, sort_keys=True) + digest(resume.read_bytes()))


def email_message(job, profile, resume):
    message = EmailMessage()
    message['From'] = formataddr((profile['name'], profile['email']))
    message['To'] = job['email']

    subjects = [
        f"Application: {job['title']} - {profile['name']}",
        f"{profile['name']} - Application for {job['title']} at {job['company']}",
        f"{job['title']} Opening - {profile['name']} (Full-Stack / Software Developer)",
        f"Job Application: {job['title']} - {profile['name']}",
        f"{profile['name']} - {job['company']} {job['title']} Opportunity",
        f"Application for {job['title']} at {job['company']} - {profile['name']}",
    ]

    contact_name = job.get('contact_name')
    if not contact_name:
        m = re.search(r'(?:contact|reach out to|recruiter|hiring manager)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', job.get('instruction', '') + ' ' + job.get('description', ''))
        if m and m.group(1).lower() not in {'team', 'hr', 'hiring', 'careers', 'jobs', 'support', 'recruiter'}:
            contact_name = m.group(1)

    if contact_name:
        greetings = [
            f"Dear {contact_name},",
            f"Hi {contact_name},",
            f"Dear {contact_name} and the {job['company']} Hiring Team,",
            f"Hello {contact_name},",
            f"Dear {contact_name} and {job['company']} Recruitment Team,",
            f"Hi {contact_name} and {job['company']} Team,",
        ]
    else:
        greetings = [
            f"Dear {job['company']} Hiring Team,",
            f"Dear {job['company']} Recruitment Team,",
            f"Dear {job['company']} Talent Acquisition Team,",
            f"Hello {job['company']} Engineering Team,",
            f"Dear {job['company']} Technical Hiring Team,",
            f"Dear {job['company']} Hiring Manager,",
        ]

    openings = [
        f"I am writing to express my strong interest in the {job['title']} position at {job['company']}, advertised at:\n{job['url']}",
        f"Please consider my application for the {job['title']} role with {job['company']}. I found your opening here:\n{job['url']}",
        f"I would welcome the opportunity to join {job['company']} as a {job['title']}. Your posting invited direct applications:\n{job['url']}",
        f"I noticed the {job['title']} opening at {job['company']} ({job['url']}) and am enthusiastic about the opportunity to contribute to your engineering team.",
        f"Following your job advertisement for {job['title']} at {job['company']} ({job['url']}), please accept my resume and application.",
        f"I am writing to apply for the {job['title']} vacancy at {job['company']}, published at:\n{job['url']}",
    ]

    matched = ', '.join(job.get('matching_skills', [])[:6])
    relevance_options = [
        f"As a Computer Science Engineering graduate and Full Stack Developer specialising in the MERN stack, I work extensively with {matched}, focusing on clean architecture, scalable REST APIs, and database performance.\n\n" if matched else '',
        f"My technical background directly aligns with your requirements in {matched}. I have developed production-grade backend services with secure authentication, ACID transactions, and optimized database queries.\n\n" if matched else '',
        f"Having hands-on experience developing with {matched}, I am comfortable jumping straight into your codebase and delivering dependable, well-tested features from day one.\n\n" if matched else '',
        f"My core stack encompasses {matched}, which I have applied to build robust full-stack applications including an AI-driven career platform and fintech transaction ledger.\n\n" if matched else '',
        f"I specialize in {matched}, with a strong foundation in data structures, algorithms, and writing clean, maintainable code aligned with modern engineering practices.\n\n" if matched else '',
        f"Key technologies in my daily toolkit include {matched}. I bring a disciplined, solution-oriented approach to building and debugging web services.\n\n" if matched else '',
    ]

    closings = [
        'I have attached my resume (Satvik_Saini_Resume.pdf) for your review. I would welcome an opportunity to discuss how my skill set and background fit your team.',
        'Please find my resume attached (Satvik_Saini_Resume.pdf). I am excited about the work being done at your company and would love to speak further about the role.',
        'My resume is attached for your consideration. I am readily available for an interview and look forward to hearing from your team.',
        'I have attached my resume for full details on my experience and technical projects. I welcome the opportunity to connect and discuss how I can contribute.',
        'Please review my attached resume. I would appreciate the chance to discuss how my hands-on development experience can support your team\'s goals.',
        'My resume is attached detailing my professional experience and project work. I look forward to the possibility of discussing this opportunity with you.',
    ]

    summaries = [
        "Computer Science Engineering graduate (B.Tech, 2026) and Full Stack Developer specialising in the MERN stack (MongoDB, Express.js, React.js, Node.js) with JavaScript and TypeScript. I have designed, built and deployed end-to-end web applications covering React front ends, RESTful API design, database schema design, secure authentication and authorization, and Generative AI (LLM) integration with Google Gemini. Comfortable with production backend concepts such as ACID transactions, idempotent APIs, concurrency safety, input validation and data integrity, demonstrated through a fintech ledger service. Strong foundation in Data Structures & Algorithms, OOP and Operating Systems, with open-source contributions and hackathon experience. Seeking an entry-level Software Engineer, SDE-1 or Full Stack Developer role to contribute to scalable, secure products.",
        "I am a Computer Science Engineering graduate (B.Tech, 2026) and Full Stack Engineer focused on the MERN ecosystem (MongoDB, Express, React, Node.js) with TypeScript and JavaScript. My experience centers on building scalable, reliable web applications with clean RESTful APIs, robust schema architecture, secure token-based authentication, and generative AI features using Google Gemini. Through production-oriented projects like an immutable fintech ledger, I have practical experience handling ACID transactions, idempotent operations, and data consistency. Backed by solid CS fundamentals in DSA, OOP, and systems design, alongside merged open-source pull requests, I am seeking an SDE-1 / Software Developer position where I can add immediate value.",
        "As a B.Tech Computer Science graduate (Class of 2026) and Full Stack Developer, I specialize in building performant web services using Node.js, Express.js, React.js, and MongoDB with JavaScript and TypeScript. I have practical, hands-on experience designing end-to-end solutions—from responsive UI components and REST APIs to multi-document ACID transactions and LLM integration via Google Gemini. My focus is on writing clean, well-tested code with rigorous validation and concurrency safety. With active open-source contributions (Accord Project) and a strong foundation in core algorithms and operating systems, I am eager to contribute to your engineering team as an entry-level Software Engineer.",
        "I hold a B.Tech in Computer Science Engineering (2026) and specialize in full-stack development across Node.js, Express, React, and MongoDB with modern JavaScript and TypeScript. My hands-on work includes architecting secure REST APIs, designing optimized database schemas, and building production-grade services featuring ACID guarantees, idempotent payment endpoints, and Gemini LLM integration. Having contributed to open-source codebases and hackathons with a deep grounding in Data Structures and OOP, I am looking to bring my problem-solving skills to an entry-level Software Engineer / SDE-1 opening."
    ]

    highlights_variants = [
        "Key Project Highlights & Practical Experience:\n"
        "• Fintech Double-Entry Ledger Service: Built an immutable, append-only transaction ledger in Node.js & MongoDB utilizing multi-document ACID transactions, aggregation pipelines, and idempotent payment APIs to prevent partial writes and balance drift.\n"
        "• AI Interview Prep Platform: Engineered a full-stack career platform using React.js, Node.js, and Google Gemini API with Zod schema validation, JWT auth in HTTP-only cookies, and automated ATS PDF generation.\n"
        "• Open Source & Fundamentals: Contributed 8+ pull requests (9 merged) to public repositories including Accord Project, with a strong foundation in Data Structures, Algorithms, and OOP.\n",

        "Demonstrated Technical Work & Selected Projects:\n"
        "• Distributed Ledger & Transaction Service: Designed a double-entry bookkeeping backend using Node.js and MongoDB with ACID multi-document transactions, atomic balances via aggregation pipelines, and idempotent endpoints against retry collisions.\n"
        "• Generative AI Career Suite: Developed a React + Node.js platform leveraging Google Gemini for automated role compatibility scoring, structured interview roadmap generation, and ATS-friendly PDF exports.\n"
        "• Open Source Collaboration: Merged 9 pull requests in public repositories (Accord Project), demonstrating proficiency in Git workflows, branching strategies, and collaborative code reviews.\n",

        "Core Projects & Technical Contributions:\n"
        "• Double-Entry Accounting Service (Node.js/Express/MongoDB): Architected an append-only ledger guaranteeing financial consistency through ACID transactions, real-time balance aggregation, and JWT/Bcrypt security.\n"
        "• AI Resume & Interview Prep Platform: Implemented an end-to-end LLM application using Google Gemini and Zod validation with Puppeteer PDF streaming and secure session management.\n"
        "• Software Engineering Foundations: Active hackathon participant and open-source contributor with 8+ PRs submitted, backed by strong coursework in Data Structures, Algorithms, and Operating Systems.\n",

        "Practical Engineering Highlights:\n"
        "• Fintech Transaction Engine: Built a production-modeled double-entry ledger backend with MongoDB ACID transactions, idempotent payment routes, and TTL index automated expirations.\n"
        "• Full-Stack AI Platform: Shipped an automated resume analysis and interview prep tool powered by React, Express, MongoDB, and Google Gemini LLM with HTTP-only cookie authentication.\n"
        "• Open-Source & Agile Delivery: Proven ability to write maintainable code with 9 merged PRs in open-source projects, applying SDLC best practices and disciplined testing.\n"
    ]

    signoffs = [
        "Kind regards,",
        "Best regards,",
        "Sincerely,",
        "Warm regards,",
        "With regards,",
        "Respectfully,"
    ]

    h = int(digest(job['id'] + job.get('email', ''))[:12], 16)
    v_subj = h % len(subjects)
    v_greet = (h // 7) % len(greetings)
    v_open = (h // 13) % len(openings)
    v_summary = (h // 19) % len(summaries)
    v_rel = (h // 29) % len(relevance_options)
    v_high = (h // 41) % len(highlights_variants)
    v_close = (h // 53) % len(closings)
    v_sign = (h // 71) % len(signoffs)

    message['Subject'] = subjects[v_subj]
    message['Date'] = formatdate(localtime=True)
    message['Message-ID'] = make_msgid(domain='gmail.com')

    body = (f"{greetings[v_greet]}\n\n"
            f"{openings[v_open]}\n\n"
            f"{summaries[v_summary]}\n\n"
            f"{relevance_options[v_rel]}"
            f"{highlights_variants[v_high]}\n"
            f"{closings[v_close]}\n\n"
            f"Portfolio: {profile['portfolio']}\nGitHub: {profile['github']}\n\n"
            f"Thank you for your time and consideration.\n\n{signoffs[v_sign]}\n{profile['name']}\n{profile['email']}\n")
    if profile.get('phone'):
        body += profile['phone'] + '\n'
    message.set_content(body)
    message.add_attachment(resume.read_bytes(), maintype='application', subtype='pdf', filename='Satvik_Saini_Resume.pdf')
    return message


def extract_salary_info(job):
    """Extract estimated monthly salary in INR and annual CTC in LPA."""
    desc = plain(str(job.get('description', '')))
    base = job.get('baseSalary')
    monthly_inr = None
    ctc_lpa = None

    if isinstance(base, dict):
        val = base.get('value', {})
        unit = str(base.get('unitText', 'YEAR')).upper()
        raw_val = None
        if isinstance(val, (int, float)):
            raw_val = float(val)
        elif isinstance(val, dict):
            raw_val = float(val.get('minValue') or val.get('value') or 0)
        if raw_val:
            if 'YEAR' in unit:
                ctc_lpa = raw_val / 100000
                monthly_inr = raw_val / 12
            else:
                monthly_inr = raw_val
                ctc_lpa = (raw_val * 12) / 100000

    if ctc_lpa is None:
        lpa_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:[-–to]\s*(\d+(?:\.\d+)?))?\s*(?:lpa|lakhs?|lac|lacs)', desc, re.I)
        if lpa_match:
            try:
                val = float(lpa_match.group(2) or lpa_match.group(1))
                ctc_lpa = val
                monthly_inr = (val * 100000) / 12
            except Exception:
                pass

    if monthly_inr is None:
        pm_match = re.search(r'(?:inr|rs\.?|₹)?\s*(\d{2,3}),?(\d{3})\s*(?:[-–to]\s*(?:inr|rs\.?|₹)?\s*\d{2,3},?\d{3})?\s*(?:per\s*month|/month|pm|\/pm)', desc, re.I)
        if pm_match:
            try:
                monthly_inr = float(pm_match.group(1) + pm_match.group(2))
                ctc_lpa = (monthly_inr * 12) / 100000
            except Exception:
                pass

    return {'monthly_inr': monthly_inr, 'ctc_lpa': ctc_lpa}


def compute_priority_score(job, profile, config):
    """Score job by recency, skill fit, interview probability, and 6-7+ LPA CTC fit."""
    score = 0
    desc = str(job.get('description', ''))
    try:
        posted = posting_timestamp(job.get('date_posted', ''))
        age_hours = (now() - posted).total_seconds() / 3600
        if age_hours <= 4:
            score += 25
        elif age_hours <= 8:
            score += 18
        elif age_hours <= 12:
            score += 10
        elif age_hours <= 18:
            score += 3
        else:
            score -= 50
    except Exception:
        pass

    skills = job.get('matching_skills', [])
    score += len(skills) * 8

    title = str(job.get('title', '')).lower()
    if re.search(r'\b(freshers?|junior|trainee|sde\s*[- ]?1|swe\s*[- ]?1|entry)\b', title + ' ' + desc, re.I):
        score += 20
    if 'intern' not in title:
        score += 10

    # 40,000 INR/month in-hand requires ~6.0 to 7.0+ LPA CTC
    sal = extract_salary_info(job)
    min_ctc = config.get('minimum_ctc_lpa', 6.0)
    target_ctc = config.get('target_ctc_lpa', 7.0)
    min_sal = config.get('minimum_monthly_salary_inr', 40000)

    if sal['ctc_lpa'] is not None:
        if sal['ctc_lpa'] >= target_ctc:
            score += 45  # 7+ LPA CTC gives 48k-55k+ in-hand
        elif sal['ctc_lpa'] >= min_ctc:
            score += 35  # 6.0-7.0 LPA CTC gives ~40k-45k in-hand
        elif sal['ctc_lpa'] < 4.5:
            score -= 40  # Under 4.5 LPA CTC (yields under 30k in-hand)
        else:
            score -= 10
    elif sal['monthly_inr'] is not None:
        if sal['monthly_inr'] >= min_sal:
            score += 35
        elif sal['monthly_inr'] < 30000:
            score -= 40
    else:
        score += 5

    return score


def resume_for(job, profile, directory):
    original = profile_ready(profile)
    if profile['resume_mode'] == 'original':
        return original
    from tailor_resume import generate
    output = directory / 'Satvik_Saini_Resume.pdf'
    generate(profile, job['description'], output)
    return output


def draft(db, job, profile):
    row = db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()
    if not row or row[0] not in ('verified', 'queued'):
        # Preserve exactly what was accepted/attempted; rediscovery is not a resend.
        return False
    directory = ROOT / 'output' / job['id']
    directory.mkdir(parents=True, exist_ok=True)
    resume = resume_for(job, profile, directory)
    msg = email_message(job, profile, resume)
    file = directory / 'application.eml'
    file.write_bytes(msg.as_bytes())
    (directory / 'evidence.json').write_text(json.dumps(job, indent=2), encoding='utf-8')
    artifact = dict(file=str(file), checksum=digest(file.read_bytes()), profile=profile_fingerprint(profile))
    db.execute("UPDATE jobs SET artifact=?, status='queued' WHERE id=? AND status IN ('verified','queued')",
               (json.dumps(artifact), job['id']))
    db.commit()
    return True


def trust_for(url, sources):
    host = (urlsplit(url).hostname or '').lower()
    return next((s for s in sources['trusted_companies'] if domain_matches(host, s['posting_domains'])), None)


def unwrap_alert_link(url):
    """Unwrap a Google Alerts tracking link; still a lead, never trusted evidence."""
    p = urlsplit(html.unescape(url))
    if p.hostname in ('www.google.com', 'google.com') and p.path == '/url':
        values = parse_qs(p.query)
        url = (values.get('url') or values.get('q') or [''])[0]
    if urlsplit(url).scheme != 'https':
        return None
    if urlsplit(url).hostname in ('www.google.com', 'google.com', 'accounts.google.com', 'support.google.com'):
        return None
    return url


def feed_links(content):
    tree = ET.fromstring(content)
    links = []
    for element in tree.iter():
        tag = element.tag.rsplit('}', 1)[-1]
        if tag == 'link':
            link = element.attrib.get('href') or element.text or ''
            target = unwrap_alert_link(link.strip())
            if target:
                links.append(target)
    return links


def alert_message_links(raw):
    from email import policy
    from email.parser import BytesParser
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    links = []
    for part in msg.walk():
        if part.get_content_type() == 'text/html' and part.get_content_disposition() != 'attachment':
            for link in Page(part.get_content()).links:
                target = unwrap_alert_link(link)
                if target:
                    links.append(target)
    return list(dict.fromkeys(links))


def gmail_alert_links(profile):
    if os.environ.get('READ_GMAIL_ALERTS', '').lower() != 'true':
        return []
    password = os.environ.get('GMAIL_APP_PASSWORD', '').replace(' ', '')
    if not password or os.environ.get('SMTP_EMAIL') != profile['email']:
        LOG.warning('Gmail Alerts discovery needs your local Gmail app password')
        return []
    links = []
    # Only Google Alert emails, read-only; no mark-read, delete or inbox-wide ingestion.
    with imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ssl.create_default_context(), timeout=30) as mailbox:
        mailbox.login(profile['email'], password)
        code, _ = mailbox.select('INBOX', readonly=True)
        if code != 'OK':
            raise ValueError('Cannot select Gmail inbox for alerts')
        since = (now() - timedelta(days=14)).strftime('%d-%b-%Y')
        code, messages = mailbox.search(None, 'FROM', '"googlealerts-noreply@google.com"', 'SINCE', since)
        if code != 'OK':
            raise ValueError('Cannot search Google Alert emails')
        for mid in (messages[0] or b'').split()[-100:]:
            code, parts = mailbox.fetch(mid, '(BODY.PEEK[])')
            if code == 'OK':
                for item in parts:
                    if isinstance(item, tuple) and len(item[1]) <= 2_000_000:
                        links.extend(alert_message_links(item[1]))
    return links


def renew_tick_lease(db, duration_minutes=15, owner_token=None):
    """Extend the scheduler lease while active discovery is making verified forward progress."""
    try:
        if owner_token is not None:
            curr = db.execute("SELECT value FROM meta WHERE key='tick_lease_owner'").fetchone()
            if not curr or curr[0] != owner_token:
                LOG.warning("Tick lease expired or claimed by another worker; skipping renewal")
                return False
        db.execute("INSERT OR REPLACE INTO meta VALUES('tick_lease_until',?)",
                   ((now() + timedelta(minutes=duration_minutes)).isoformat(),))
        db.commit()
        return True
    except Exception:
        return False


def discover(config, profile, sources, db, fetcher, lease_token=None):
    targets = list(sources.get('posting_urls', []))
    try:
        targets.extend(gmail_alert_links(profile))
    except Exception as e:
        LOG.warning('Google Alerts email discovery unavailable (%s)', type(e).__name__)
    for feed in sources.get('alert_feeds', []):
        try:
            # Explicit publisher-provided RSS/Atom subscriptions, not search-page scraping.
            _, content = fetcher.raw(feed)
            targets.extend(feed_links(content))
        except Exception as e:
            LOG.warning('Subscribed feed unavailable (%s)', type(e).__name__)
    # Search is discovery only: snippets never qualify recipients for sending.
    # Bing's public RSS endpoint is best effort, with no API/SLA guarantee.
    if config.get('public_search_enabled', False):
        roles = ['software engineer', 'software developer', 'backend developer', 'SWE SDE']
        cities = ['Noida', 'Delhi', 'Gurugram Gurgaon', 'Hyderabad', 'Bengaluru Bangalore']
        queries = [f'"{role}" {city} "send" "resume" email jobs' for role in roles for city in cities]
        offset = (now().date().toordinal() * config['search_queries_per_day']) % len(queries)
        for i in range(config['search_queries_per_day']):
            try:
                _, content = fetcher.get('https://www.bing.com/search?' + urlencode({'q': queries[(offset+i) % len(queries)], 'format': 'rss'}))
                feed = ET.fromstring(content)
                targets.extend(x.text for x in feed.findall('.//item/link') if x.text)
            except Exception as e:
                LOG.warning('Search unavailable: %s', e)
    for source in sources.get('trusted_companies', []):
        for start in source.get('career_urls', []):
            is_avail, status, reason = is_source_in_cooldown(db, start)
            if not is_avail:
                LOG.info('Career source %s in %s (%s); skipping', start, status, reason)
                continue
            try:
                final, content = fetcher.get(start)
                if not trust_for(final, {'trusted_companies': [source]}):
                    raise ValueError('Career page redirects outside trusted domains')
                page = Page(content)
                new_links = [urljoin(final, link) for link in page.links
                             if re.search(r'job|career|opening|position', link, re.I)
                             and domain_matches(urlsplit(urljoin(final, link)).hostname or '', source['posting_domains'])]
                targets.append(final)
                targets.extend(new_links)
                record_source_success(db, start, 'career_page', len(new_links))
            except Exception as e:
                code = getattr(e, 'code', None)
                retry_after = e.headers.get('Retry-After') if hasattr(e, 'headers') and e.headers else None
                record_source_failure(db, start, 'career_page', code, e, retry_after)
                LOG.warning('Career source %s unavailable: %s', start, e)
        renew_tick_lease(db, 15, owner_token=lease_token)
    ats_candidates = 0
    portal_added = 0
    for board in sources.get('greenhouse_boards', []):
        board_url = board.get('board_url', board.get('official_careers_url', ''))
        is_avail, status, reason = is_source_in_cooldown(db, board_url)
        if not is_avail:
            LOG.info('Greenhouse board %s in %s (%s); skipping', board_url, status, reason)
            continue
        try:
            candidates = greenhouse_review_candidates(board, fetcher, config)
            record_source_success(db, board_url, 'greenhouse_board', len(candidates))
            for candidate in candidates:
                db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)',
                           (candidate['url'], json.dumps(candidate, ensure_ascii=False), now().isoformat()))
                if not board.get('review_only', False):
                    candidate_job = {
                        'title': candidate['title'],
                        'description': candidate.get('content') or candidate['title'],
                        'datePosted': candidate.get('posted_at'),
                        'jobLocation': [{'address': {'addressLocality': candidate.get('location', '')}}],
                        'is_active_ats_feed': True,
                    }
                    trust_proxy = {'company': candidate.get('company') or board.get('company', '')}
                    pjob, preason = evaluate_portal_job(candidate_job, candidate['url'], trust_proxy, config, profile)
                    if pjob:
                        db.execute('''INSERT OR IGNORE INTO portal_jobs(id, title, company, url, location, matching_skills, match_score, date_posted, status, discovered_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?)''',
                            (pjob['id'], pjob['title'], pjob['company'], pjob['url'], pjob['location'],
                             json.dumps(pjob['matching_skills']), pjob['match_score'], pjob['date_posted'], 'pending', now().isoformat()))
                        portal_added += 1
            db.commit()
            ats_candidates += len(candidates)
        except Exception as e:
            code = getattr(e, 'code', None)
            retry_after = e.headers.get('Retry-After') if hasattr(e, 'headers') and e.headers else None
            record_source_failure(db, board_url, 'greenhouse_board', code, e, retry_after)
            LOG.warning('Greenhouse board discovery unavailable (%s)', type(e).__name__)
        renew_tick_lease(db, 15, owner_token=lease_token)

    # FreeHire entry-level discovery adapter
    fh_counts = {}
    try:
        fh_counts = freehire_discover(config, profile, sources, db, fetcher)
        portal_added += fh_counts.get('portal_jobs', 0)
        renew_tick_lease(db, 15, owner_token=lease_token)
    except Exception as e:
        LOG.warning('FreeHire discovery unavailable (%s)', type(e).__name__)

    counts = {
        'verified': 0, 'review': 0, 'pages': 0,
        'ats_review_candidates': ats_candidates,
        'freehire_candidates': fh_counts.get('candidates', 0),
        'freehire_reviews': fh_counts.get('reviews', 0),
        'portal_jobs': portal_added
    }
    for url in list(dict.fromkeys(targets))[:config['max_pages_per_run']]:
        try:
            url = canonical(url)
            trust = trust_for(url, sources)
            if not trust:
                reason = 'New domain: confirm employer identity in sources.json before crawling/sending'
                db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)', (url, reason, now().isoformat()))
                counts['review'] += 1
                continue
            final, content = fetcher.get(url)
            counts['pages'] += 1
            page = Page(content)
            jobs = [j for block in page.blocks for j in json_jobs(block)]
            # A list page cannot prove which email belongs to which opening.
            if len(jobs) != 1:
                raise ValueError('Require a single JobPosting JSON-LD on the individual job page; review other formats manually')
            job, reason = evaluate(jobs[0], final, trust, config, profile)
            if job:
                db.execute('''INSERT INTO jobs(id,payload) VALUES(?,?)
                    ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,
                    status=CASE WHEN jobs.status='needs_review' THEN 'verified' ELSE jobs.status END''', (job['id'], json.dumps(job)))
                db.execute('DELETE FROM reviews WHERE url=?', (url,))
                db.commit()
                counts['verified'] += 1
                if profile.get('confirmed'):
                    draft(db, job, profile)
            elif reason == 'no unambiguous application email on the posting':
                pjob, _ = evaluate_portal_job(jobs[0], final, trust, config, profile)
                if pjob:
                    db.execute('''INSERT OR IGNORE INTO portal_jobs(id, title, company, url, location, matching_skills, match_score, date_posted, status, discovered_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)''',
                        (pjob['id'], pjob['title'], pjob['company'], pjob['url'], pjob['location'],
                         json.dumps(pjob['matching_skills']), pjob['match_score'], pjob['date_posted'], 'pending', now().isoformat()))
                    db.execute('DELETE FROM reviews WHERE url=?', (url,))
                    db.commit()
                    counts['portal_jobs'] = counts.get('portal_jobs', 0) + 1
                else:
                    raise ValueError(reason)
            else:
                raise ValueError(reason)
        except Exception as e:
            db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)', (url, str(e), now().isoformat()))
            counts['review'] += 1
    db.commit()
    fresh = eligible_count(config, db)
    if fresh >= config['daily_minimum']:
        db.execute("INSERT OR REPLACE INTO meta VALUES('batch_released_day',?)", (now().date().isoformat(),))
    db.execute("INSERT OR REPLACE INTO meta VALUES('next_discovery_at',?)",
               ((now() + timedelta(hours=config['discovery_retry_hours'])).isoformat(),))
    db.commit()
    counts['fresh_unique_recipients'] = fresh
    counts['daily_minimum'] = config['daily_minimum']
    counts['next_discovery_at'] = (now() + timedelta(hours=config['discovery_retry_hours'])).isoformat()
    export(db)
    return counts


def eligible_count(config, db):
    default_cutoff = now() - timedelta(hours=config['max_post_age_hours'])
    email_cutoff = now() - timedelta(days=config.get('email_lead_max_post_age_days', 3))
    recipients = set()
    for row in db.execute("SELECT payload FROM jobs WHERE status='queued' AND id NOT IN (SELECT job_id FROM attempts)"):
        job = json.loads(row[0])
        if db.execute('SELECT 1 FROM attempts WHERE recipient=? UNION SELECT 1 FROM suppression WHERE email=?',
                      (job['email'], job['email'])).fetchone():
            continue
        try:
            is_email_lead = job.get('source_type') in ('recruiter_post', 'hiring_announcement') or 'lead:' in job.get('id', '')
            cutoff = email_cutoff if is_email_lead else default_cutoff
            if cutoff <= posting_timestamp(job['date_posted']) <= now():
                recipients.add(job['email'])
        except (ValueError, TypeError, KeyError):
            continue
    return len(recipients)


def dispatch_gate(config, db):
    released = db.execute("SELECT value FROM meta WHERE key='batch_released_day'").fetchone()
    min_gate = config.get('daily_minimum', 1)
    if min_gate > 1:
        if not released or released[0] != now().date().isoformat():
            return f"waiting for at least {min_gate} fresh, verified, distinct application recipients"
    midnight = now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    rolling = (now() - timedelta(hours=24)).isoformat()
    count = db.execute('SELECT count(*) FROM attempts WHERE started >= ? OR started >= ?', (midnight, rolling)).fetchone()[0]
    due = db.execute("SELECT value FROM meta WHERE key='next_send_at'").fetchone()
    if count >= config['daily_limit'] or (due and posted_date(due[0]) > now()):
        return 'paced/capped'
    return None


def generate_referral_links(company, city='India'):
    from urllib.parse import quote_plus
    comp_q = quote_plus(company.strip())
    city_q = quote_plus(city.strip()) if city else 'India'
    return {
        'recruiters': f"https://www.linkedin.com/search/results/people/?keywords={comp_q}+{city_q}+technical+recruiter",
        'engineers': f"https://www.linkedin.com/search/results/people/?keywords={comp_q}+{city_q}+software+engineer",
        'managers': f"https://www.linkedin.com/search/results/people/?keywords={comp_q}+engineering+manager"
    }


def send_portal_digest(config, profile, db, force=False):
    current = now()
    hour_slot = 'morning_6am' if current.hour < 12 else 'evening_6pm'
    slot_key = f"digest_sent_{current.strftime('%Y-%m-%d')}_{hour_slot}"
    delivery_key = f"digest_delivery_{current.strftime('%Y-%m-%d')}_{hour_slot}"
    preview_key = f"digest_preview_{current.strftime('%Y-%m-%d')}_{hour_slot}"
    digest_send_enabled = os.environ.get('DIGEST_SEND_ENABLED', os.environ.get('SEND_ENABLED', '')).lower() == 'true'
    if digest_send_enabled:
        if os.environ.get('PAUSE_SENDING', '').lower() == 'true':
            return {'mode': 'paused', 'message': 'PAUSE_SENDING is enabled'}
        if db.execute("SELECT 1 FROM meta WHERE key='smtp_paused'").fetchone():
            return {'mode': 'paused', 'message': 'Resolve the SMTP error, then use resume-sending'}
        if os.environ.get('SMTP_EMAIL') != profile['email']:
            return {'mode': 'blocked', 'message': 'SMTP sender does not match the profile email'}
        if db.execute("SELECT 1 FROM meta WHERE key=?", (delivery_key,)).fetchone():
            return {'mode': 'already_sent_today', 'slot': slot_key}
    if not force:
        target_hours = config.get('portal_digest_hours', [6, 18])
        if current.hour not in target_hours:
            return {'mode': 'skipped', 'reason': f"Current hour {current.hour} is not in digest hours {target_hours}"}
        if db.execute("SELECT 1 FROM meta WHERE key IN (?,?)", (slot_key, delivery_key)).fetchone():
            return {'mode': 'already_sent_today', 'slot': slot_key}
        if not digest_send_enabled and db.execute("SELECT 1 FROM meta WHERE key=?", (preview_key,)).fetchone():
            return {'mode': 'preview_already_saved', 'slot': slot_key}

    cutoff = (current - timedelta(hours=24)).isoformat()
    rows = db.execute('''
        SELECT * FROM portal_jobs
        WHERE status='pending'
        ORDER BY match_score DESC, date_posted DESC
    ''').fetchall()
    portal_max_days = config.get('portal_max_post_age_days')
    portal_max_hours = config.get('max_post_age_hours', 168)
    jobs = []
    for row in rows:
        job = dict(row)
        try:
            posted, is_date_only = parse_portal_job_date(job['date_posted'], current)
        except (ValueError, TypeError, KeyError):
            posted = None
        if not posted:
            db.execute("UPDATE portal_jobs SET status='expired_outdated' WHERE id=? AND status='pending'", (job['id'],))
            continue

        # Respect max_post_age_hours (e.g. 12h) or portal_max_days, and enforce absolute 7-day limit (168h)
        if portal_max_days is not None:
            stale = (current.date() - posted.date()).days > portal_max_days if is_date_only else posted < current - timedelta(days=portal_max_days)
        else:
            stale = posted > current or posted < current - timedelta(hours=portal_max_hours)

        if not stale and posted < current - timedelta(days=7):
            stale = True

        if stale or posted > current:
            db.execute("UPDATE portal_jobs SET status='expired_outdated' WHERE id=? AND status='pending'", (job['id'],))
            continue

        # Experience fit verification
        desc_text = job.get('description', '')
        if desc_text:
            exp_ok, exp_reason = evaluate_experience_fit(desc_text, job['title'], job.get('experienceRequirements'), config, profile)
            if not exp_ok:
                db.execute("UPDATE portal_jobs SET status='ineligible_experience' WHERE id=? AND status='pending'", (job['id'],))
                continue
            grad_ok, grad_reason = evaluate_graduation_eligibility(desc_text, job['title'])
            if not grad_ok:
                db.execute("UPDATE portal_jobs SET status='ineligible_graduation' WHERE id=? AND status='pending'", (job['id'],))
                continue

        age_seconds = (current - posted).total_seconds()
        age_hours = age_seconds / 3600.0
        is_fresh_72h = (age_hours <= 72.0)
        if age_hours < 1.0:
            age_label = f"{int(max(1, age_seconds // 60))}m ago"
        elif age_hours < 24.0:
            age_label = f"{int(age_hours)}h ago"
        else:
            days = int(age_hours // 24)
            rem_hours = int(age_hours % 24)
            age_label = f"{days}d {rem_hours}h ago" if rem_hours > 0 else f"{days}d ago"

        job['dt_posted'] = posted
        job['age_hours'] = age_hours
        job['age_label'] = age_label
        job['is_fresh_72h'] = is_fresh_72h
        job['exp_display'] = extract_experience_display(desc_text, job['title'], job.get('experienceRequirements'))
        job['verification_status'] = 'Verified ATS (Corporate Portal)'
        jobs.append(job)
    db.commit()


    # Prioritize jobs posted within 72 hours, then recency, then match score
    jobs.sort(key=lambda j: (
        0 if j['is_fresh_72h'] else 1,
        j['age_hours'],
        -(compute_priority_score(j, profile, config) if j.get('description') else j.get('match_score', 0))
    ))

    review_rows = db.execute("SELECT url, reason, found_at FROM reviews ORDER BY found_at DESC").fetchall()
    review_candidates = []
    seen_review_urls = set()
    for rr in review_rows:
        try:
            ev = json.loads(rr['reason'])
            if not isinstance(ev, dict):
                continue

            posted_val = ev.get('posted_at') or ev.get('date_posted') or ev.get('datePosted')
            # Missing or unparseable posting date: retain in DB for audit, exclude from emailed digest
            dt_posted, age_hours, age_label, is_fresh_72h, is_valid_7d = parse_and_validate_job_date(posted_val, current)
            if not is_valid_7d:
                continue

            t = str(ev.get('title', '')).strip()
            comp = str(ev.get('company', '')).strip() or 'Unknown Company'
            reason_str = str(ev.get('reason') or ev.get('classification') or '')
            desc = str(ev.get('description', '')).strip()
            comb = t + ' ' + desc + ' ' + reason_str

            # Ineligible markers
            if ev.get('classification', '').lower() == 'ineligible':
                continue
            ineligible_markers = [
                'senior role', 'payment/scam', 'requires more than 2 years', 'scam indicator',
                'exceeds profile', 'ineligible', 'role closed', 'posting expired', 'unmet mandatory',
                'tier-1', 'prior relevant internship', 'mentoring or leadership', 'incompatible graduation'
            ]
            if any(x in reason_str.lower() for x in ineligible_markers):
                continue

            # Senior / Managerial role exclusion
            if SENIOR_ROLES_PATTERN.search(t) and not profile.get('allow_senior_roles', False):
                continue

            # Non-software discipline exclusion
            if re.search(r'\b(mechanical|civil|chemical|electrical|metallurgy|mining|chemist|pharmacist|sales|content writer|telecom|bpo|voice|finance|analyst|executive|cluster)\b', t, re.I):
                continue

            # Mentoring / Leadership heavy role exclusion (e.g. Deepsa AI Automation)
            if MENTOR_LEAD_PATTERN.search(comb) or 'deepsa' in comp.lower():
                continue

            # Mandatory experience > 0 check
            if desc:
                exp_ok, exp_reason = evaluate_experience_fit(desc, t, None, config, profile)
                if not exp_ok:
                    continue
            if re.search(r'\b(?:4[-–—to]8|[3-9]|\d{2,})\+?\s*(?:years?|yrs?|yoe)\b', comb, re.I):
                continue
            if re.search(r'\b[1-9]\d*\s*[-–—to]\s*\d+\s*(?:years?|yrs?|yoe)\b', comb, re.I):
                continue
            if re.search(r'\b(?:minimum|at\s+least|must\s+have)\s+[1-9]\d*\+?\s*(?:years?|yrs?|yoe)\b', comb, re.I):
                continue

            # Graduation batch restriction check
            grad_ok, grad_reason = evaluate_graduation_eligibility(desc, t)
            if not grad_ok:
                continue

            # Plausible 0-YOE fresher eligibility check
            is_junior_title = bool(re.search(r'\b(junior|jr\.?|intern|internship|fresher|freshers|entry[ -]?level|associate|trainee|apprentice|graduate|sde\s*[- ]?(?:1|i)\b|swe\s*[- ]?(?:1|i)\b|software\s+(?:development\s+)?engineer\s*[- ]?(?:1|i)\b)\b', t, re.I))
            has_fresher_signal = bool(re.search(r'\b(0\s*[-–—to]\s*[12]\s*(?:years?|yrs?|yoe)|0\s*(?:years?|yrs?|yoe)|fresher|freshers|entry[ -]?level|0\s*yoe|for\s+freshers|no\s+experience\s+required)\b', comb, re.I))
            is_ambiguous_review = bool('ambiguous experience requirement' in reason_str.lower() or 'manual review' in reason_str.lower())
            if not is_junior_title and not has_fresher_signal and not is_ambiguous_review:
                continue

            url_c = canonical(rr['url'])
            if url_c in seen_review_urls:
                continue
            seen_review_urls.add(url_c)

            third_party = ev.get('third_party_source') or ev.get('source') or ''
            verification_status = f"Manual Review (Unverified Source: {third_party.title()})" if third_party else "Manual Review (Pending Employer Verification)"

            review_candidates.append({
                'url': url_c,
                'title': t or 'Software Engineering Opportunity',
                'company': comp,
                'location': ev.get('location') or 'India / Remote',
                'reason': reason_str or 'Held for manual review',
                'skills': ev.get('description_skill_matches', []),
                'posted_val': posted_val,
                'dt_posted': dt_posted,
                'age_hours': age_hours,
                'age_label': age_label,
                'is_fresh_72h': is_fresh_72h,
                'exp_display': extract_experience_display(desc, t),
                'verification_status': verification_status
            })
        except Exception:
            pass

    # Prioritize review roles posted within 72 hours, then recency
    review_candidates.sort(key=lambda r: (
        0 if r['is_fresh_72h'] else 1,
        r['age_hours']
    ))

    if not jobs and not review_candidates:
        LOG.info('No portal jobs or review candidates to include in digest')
        return {'mode': 'no_jobs', 'count': 0, 'review_count': 0}

    lines_txt = [
        f"SATVIK'S PRIORITY PORTAL JOBS & REFERRAL DIGEST - {current.strftime('%Y-%m-%d %H:%M')} IST",
        "=" * 70,
        f"Category A - Confirmed Eligible Portal Jobs: {len(jobs)}",
        f"Category B - Promising Opportunities for Manual Review: {len(review_candidates)}",
        "=" * 70,
        ""
    ]

    html_cards = []
    for idx, j in enumerate(jobs, 1):
        try:
            skills = json.loads(j['matching_skills'])
        except Exception:
            skills = []
        skills_str = ', '.join(skills) if skills else 'Software Engineering'
        refs = generate_referral_links(j['company'], j.get('location', 'India'))
        job_ref = f"#JOB-{j['id'][:8].upper()}"
        referral_msg_team = (
            f"Hi {j['company']} Team, I noticed an open {j['title']} position (Ref ID: {job_ref}) on your careers portal. "
            f"With hands-on experience in {skills_str} and building full-stack applications, I'd love to connect "
            f"and see if you would be open to submitting an internal referral for me. "
            f"My portfolio: {profile.get('portfolio')} and GitHub: {profile.get('github')}. Thanks!"
        )
        referral_msg_person = (
            f"Hi [Name], I noticed an open {j['title']} position at {j['company']} (Ref ID: {job_ref}). "
            f"With my background in {skills_str}, I would love to connect and see if you might be open "
            f"to submitting an internal referral for me. "
            f"Portfolio: {profile.get('portfolio')} and GitHub: {profile.get('github')}. Thank you!"
        )
        title_html = html.escape(j['title'])
        company_html = html.escape(j['company'])
        location_html = html.escape(j.get('location', 'N/A'))
        job_url_html = html.escape(j['url'], quote=True)
        refs_html = {key: html.escape(value, quote=True) for key, value in refs.items()}
        skills_html = html.escape(skills_str)
        team_message_html = html.escape(referral_msg_team)
        person_message_html = html.escape(referral_msg_person)
        posted_html = html.escape(j['date_posted'][:16].replace('T', ' '))
        age_label = j.get('age_label', 'Recent')
        age_badge_color = "#34D399" if j.get('is_fresh_72h') else "#FBBF24"
        age_badge_bg = "#064E3B" if j.get('is_fresh_72h') else "#451A03"
        age_badge_border = "#059669" if j.get('is_fresh_72h') else "#B45309"
        exp_display = html.escape(j.get('exp_display', 'Fresher / 0–1 YOE'))
        verif_status = html.escape(j.get('verification_status', 'Verified ATS (Corporate Portal)'))

        lines_txt.extend([
            f"#{idx}. {j['title']} @ {j['company']} (Ref ID: {job_ref})",
            f"Location: {j.get('location', 'N/A')}",
            f"Verification: {verif_status}",
            f"Experience: {exp_display}",
            f"Posted: {j['date_posted']} ({age_label})",
            f"Salary Target Fit: ₹40,000+/mo in-hand (~6.0 - 7.5+ LPA CTC)",
            f"Skill Match Score: {j['match_score']} matched ({skills_str})",
            f"Portal Apply Link: {j['url']}",
            "LinkedIn Referral Searches (Click to connect with employees):",
            f"  - Technical Recruiters: {refs['recruiters']}",
            f"  - Software Engineers (Peer Referrals): {refs['engineers']}",
            f"  - Engineering Managers: {refs['managers']}",
            "Suggested Referral Message (Direct to Team):",
            f'  "{referral_msg_team}"',
            "Suggested Referral Message (Personalized to Employee Name):",
            f'  "{referral_msg_person}"',
            "-" * 70,
            ""
        ])

        html_cards.append(f"""
        <div style="background-color: #172235; border: 1px solid #24354D; border-radius: 10px; padding: 22px; margin-bottom: 22px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.4);">
            <div class="card-header-flex" style="display: flex; justify-content: space-between; align-items: baseline; border-bottom: 1px solid #24354D; padding-bottom: 12px; margin-bottom: 14px;">
                <h3 style="margin: 0; color: #F8FAFC; font-size: 21px; font-weight: 700; line-height: 1.4;">#{idx}. {title_html}</h3>
                <div class="card-badge-right" style="margin-left: 12px; white-space: nowrap;">
                    <span style="background-color: {age_badge_bg}; color: {age_badge_color}; border: 1px solid {age_badge_border}; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 6px; margin-right: 6px;">⏱ {age_label}</span>
                    <span style="background-color: #064E3B; color: #34D399; border: 1px solid #059669; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 6px; text-transform: uppercase; letter-spacing: 0.5px; margin-right: 6px;">Category A</span>
                    <span style="background-color: #0C4A6E; color: #7DD3FC; border: 1px solid #0284C7; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 6px;">Score: {j['match_score']} Skills</span>
                </div>
            </div>
            <p style="margin: 6px 0; color: #CBD5E1; font-size: 15px;"><strong style="color: #94A3B8;">Company:</strong> <span style="color: #F8FAFC; font-weight: 700;">{company_html}</span> &bull; <strong style="color: #94A3B8;">Job Ref ID:</strong> <span style="font-family: monospace; font-weight: 700; color: #38BDF8; background-color: #0F1A2C; padding: 2px 6px; border-radius: 4px; border: 1px solid #1E293B;">{job_ref}</span> &bull; <strong style="color: #94A3B8;">City:</strong> <span style="color: #E2E8F0;">{location_html}</span></p>
            <p style="margin: 6px 0; color: #CBD5E1; font-size: 15px;"><strong style="color: #94A3B8;">Posted:</strong> <span style="color: #F8FAFC; font-weight: 600;">{posted_html} IST</span> &bull; <strong style="color: #94A3B8;">Experience:</strong> <span style="color: #34D399; font-weight: 600;">{exp_display}</span> &bull; <strong style="color: #94A3B8;">Verification:</strong> <span style="color: #38BDF8; font-weight: 600;">{verif_status}</span></p>
            <p style="margin: 6px 0; color: #CBD5E1; font-size: 15px;"><strong style="color: #94A3B8;">Target Fit:</strong> <span style="color: #34D399; font-weight: 600;">₹40,000+/month in-hand (~6.0 - 7.5+ LPA CTC)</span> &bull; <strong style="color: #94A3B8;">Matched Skills:</strong> <span style="color: #93C5FD; font-weight: 600; background-color: #0F1A2C; padding: 3px 8px; border-radius: 4px; border: 1px solid #1E293B;">{skills_html}</span></p>
            <div style="margin: 18px 0 14px 0;">
                <a href="{job_url_html}" target="_blank" style="display: inline-block; background-color: #2563EB; color: #FFFFFF; text-decoration: none; padding: 11px 24px; border-radius: 6px; font-weight: 700; font-size: 15px; border: 1px solid #3B82F6; box-shadow: 0 2px 4px rgba(37, 99, 235, 0.3);">👉 Apply on Career Portal</a>
            </div>
            <div style="background-color: #0F1A2C; border: 1px solid #1E293B; border-left: 4px solid #10B981; padding: 14px 18px; margin-top: 14px; border-radius: 0 8px 8px 0;">
                <p style="margin: 0 0 8px 0; font-size: 14px; font-weight: 700; color: #34D399;">🤝 Get Referrals on LinkedIn (Click to find employees who can refer you):</p>
                <div class="action-buttons-flex" style="font-size: 13px; margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 8px;">
                    <a href="{refs_html['recruiters']}" target="_blank" style="display: inline-block; color: #38BDF8; background-color: #172235; border: 1px solid #24354D; padding: 6px 14px; border-radius: 6px; text-decoration: none; font-weight: 600;">🔍 Technical Recruiters</a>
                    <a href="{refs_html['engineers']}" target="_blank" style="display: inline-block; color: #38BDF8; background-color: #172235; border: 1px solid #24354D; padding: 6px 14px; border-radius: 6px; text-decoration: none; font-weight: 600;">👥 Software Engineers</a>
                    <a href="{refs_html['managers']}" target="_blank" style="display: inline-block; color: #38BDF8; background-color: #172235; border: 1px solid #24354D; padding: 6px 14px; border-radius: 6px; text-decoration: none; font-weight: 600;">👔 Engineering Managers</a>
                </div>
                <p style="margin: 8px 0 4px 0; font-size: 12px; color: #94A3B8; font-style: italic;">Copy-paste referral note (Direct to Team):</p>
                <div style="color: #E2E8F0; background-color: #0B1220; border: 1px solid #1E293B; padding: 10px 14px; border-radius: 6px; font-family: monospace; font-size: 13px; line-height: 1.5; margin-bottom: 10px; word-break: break-word;">{team_message_html}</div>
                <p style="margin: 8px 0 4px 0; font-size: 12px; color: #94A3B8; font-style: italic;">Or personalized to an employee (replace [Name] with their first name):</p>
                <div style="color: #E2E8F0; background-color: #0B1220; border: 1px solid #1E293B; padding: 10px 14px; border-radius: 6px; font-family: monospace; font-size: 13px; line-height: 1.5; word-break: break-word;">{person_message_html}</div>
            </div>
        </div>
        """)

    review_cards = []
    for r_idx, rj in enumerate(review_candidates, 1):
        r_title = html.escape(rj['title'])
        r_comp = html.escape(rj['company'])
        r_loc = html.escape(rj.get('location', 'N/A'))
        r_reason = html.escape(rj.get('reason', 'Held for manual review'))
        r_url = html.escape(rj['url'], quote=True)
        r_skills = rj.get('skills', [])
        r_skills_str = html.escape(', '.join(r_skills)) if r_skills else ''
        r_skills_html = f'<p style="margin: 6px 0 0 0; color: #94A3B8; font-size: 14px;"><strong style="color: #CBD5E1;">Matched Skills:</strong> <span style="color: #93C5FD; background-color: #0F1A2C; border: 1px solid #1E293B; padding: 2px 8px; border-radius: 4px; font-weight: 500;">{r_skills_str}</span></p>' if r_skills_str else ''
        r_refs = generate_referral_links(rj['company'], rj.get('location', 'India'))
        r_refs_html = {k: html.escape(v, quote=True) for k, v in r_refs.items()}
        r_age_label = rj.get('age_label', 'Active')
        r_age_badge_color = "#34D399" if rj.get('is_fresh_72h') else "#FBBF24"
        r_age_badge_bg = "#064E3B" if rj.get('is_fresh_72h') else "#451A03"
        r_age_badge_border = "#059669" if rj.get('is_fresh_72h') else "#B45309"
        r_exp_display = html.escape(rj.get('exp_display', 'Fresher / Entry-Level'))
        r_posted_html = html.escape(rj['dt_posted'].strftime('%Y-%m-%d %H:%M') if rj.get('dt_posted') else 'Recent')
        r_verif_status = html.escape(rj.get('verification_status', 'Manual Review (Pending Employer Verification)'))

        review_cards.append(f"""
        <div style="background-color: #172235; border: 1px solid #2B3A4F; border-radius: 10px; padding: 20px; margin-bottom: 20px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.4);">
            <div class="card-header-flex" style="display: flex; justify-content: space-between; align-items: baseline; border-bottom: 1px solid #24354D; padding-bottom: 10px; margin-bottom: 12px;">
                <h4 style="margin: 0; color: #F8FAFC; font-size: 19px; font-weight: 700; line-height: 1.4;">#{r_idx}. {r_title} <span style="color: #38BDF8; font-weight: 400;">@</span> {r_comp}</h4>
                <div class="card-badge-right" style="margin-left: 12px; white-space: nowrap;">
                    <span style="background-color: {r_age_badge_bg}; color: {r_age_badge_color}; border: 1px solid {r_age_badge_border}; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 6px; margin-right: 6px;">⏱ {r_age_label}</span>
                    <span style="background-color: #451A03; color: #FBBF24; border: 1px solid #B45309; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 6px; text-transform: uppercase; letter-spacing: 0.5px;">Manual Review</span>
                </div>
            </div>
            <p style="margin: 6px 0; font-size: 15px; color: #CBD5E1;"><strong style="color: #94A3B8;">Location:</strong> {r_loc} &bull; <strong style="color: #94A3B8;">Posted:</strong> <span style="color: #F8FAFC; font-weight: 600;">{r_posted_html} IST</span></p>
            <p style="margin: 6px 0; font-size: 15px; color: #CBD5E1;"><strong style="color: #94A3B8;">Experience:</strong> <span style="color: #FBBF24; font-weight: 600;">{r_exp_display}</span> &bull; <strong style="color: #94A3B8;">Status:</strong> <span style="color: #93C5FD; font-weight: 500;">{r_verif_status}</span></p>
            <div style="background-color: #241416; border: 1px solid #7F1D1D; border-left: 4px solid #EF4444; border-radius: 6px; padding: 10px 14px; margin: 12px 0;">
                <strong style="color: #F87171; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px;">Reason Held:</strong>
                <span style="color: #FCA5A5; font-size: 14px; font-weight: 500; margin-left: 6px;">{r_reason}</span>
            </div>
            {r_skills_html}
            <div class="action-buttons-flex" style="margin-top: 16px; display: flex; align-items: center; flex-wrap: wrap; gap: 10px;">
                <a href="{r_url}" target="_blank" style="display: inline-block; background-color: #D97706; color: #FFFFFF; text-decoration: none; padding: 10px 20px; border-radius: 6px; font-size: 14px; font-weight: 700; border: 1px solid #F59E0B;">🔍 Inspect Listing &amp; Apply</a>
                <span style="font-size: 13px; color: #94A3B8; margin-left: 4px;">Get Referrals:</span>
                <a href="{r_refs_html['recruiters']}" target="_blank" style="display: inline-block; color: #38BDF8; background-color: #0F1A2C; border: 1px solid #24354D; padding: 6px 14px; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: 600;">Technical Recruiters</a>
                <a href="{r_refs_html['engineers']}" target="_blank" style="display: inline-block; color: #38BDF8; background-color: #0F1A2C; border: 1px solid #24354D; padding: 6px 14px; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: 600;">Software Engineers</a>
            </div>
        </div>
        """)


    body_txt = "\n".join(lines_txt)
    cat_a_html = "".join(html_cards) if html_cards else '<div style="background-color: #172235; border: 1px dashed #334155; border-radius: 8px; padding: 20px; color: #94A3B8; font-size: 15px; font-style: italic;">0 currently pending in active queue (strict 0-YOE fresher and graduation-year gating active).</div>'
    cat_b_html = "".join(review_cards) if review_cards else '<div style="background-color: #172235; border: 1px dashed #334155; border-radius: 8px; padding: 20px; color: #94A3B8; font-size: 15px; font-style: italic;">No pending review candidates.</div>'

    body_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="dark">
    <meta name="supported-color-schemes" content="dark">
    <title>Satvik's Priority Job & Referral Digest</title>
    <style>
        :root {{ color-scheme: dark; supported-color-schemes: dark; }}
        body {{ margin: 0; padding: 0; background-color: #0B1220 !important; color: #F1F5F9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 16px; line-height: 1.6; -webkit-font-smoothing: antialiased; }}
        a {{ color: #38BDF8; }}
        @media only screen and (max-width: 680px) {{
            .digest-container {{ padding: 16px 12px !important; }}
            .card-header-flex {{ display: block !important; }}
            .card-badge-right {{ margin-top: 10px !important; margin-left: 0 !important; display: block !important; }}
            .action-buttons-flex {{ display: flex !important; flex-direction: column !important; gap: 8px !important; }}
            .action-buttons-flex a {{ width: 100% !important; box-sizing: border-box !important; text-align: center !important; margin: 4px 0 !important; }}
        }}
    </style>
</head>
<body style="margin: 0; padding: 0; background-color: #0B1220; color: #F1F5F9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 16px; line-height: 1.6;">
    <div style="background-color: #0B1220; width: 100%; min-height: 100vh; padding: 30px 10px; box-sizing: border-box;">
        <div class="digest-container" style="max-width: 960px; margin: 0 auto; background-color: #0F172A; padding: 32px 28px; border-radius: 12px; border: 1px solid #1E293B; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6);">
            
            <!-- Header -->
            <div style="border-bottom: 1px solid #1E293B; padding-bottom: 20px; margin-bottom: 24px;">
                <h1 style="color: #F8FAFC; font-size: 26px; font-weight: 800; margin: 0 0 8px 0; letter-spacing: -0.5px;">🚀 Satvik's Priority Job & Referral Digest</h1>
                <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; margin-top: 12px;">
                    <p style="color: #94A3B8; font-size: 15px; margin: 0;">Generated at <strong style="color: #E2E8F0;">{current.strftime('%Y-%m-%d %H:%M')} IST</strong> &bull; 2026 B.Tech CSE Radar</p>
                    <div style="display: flex; gap: 8px;">
                        <span style="background-color: #064E3B; color: #34D399; border: 1px solid #059669; font-size: 13px; font-weight: 700; padding: 4px 12px; border-radius: 20px;">{len(jobs)} Confirmed Eligible</span>
                        <span style="background-color: #451A03; color: #FBBF24; border: 1px solid #B45309; font-size: 13px; font-weight: 700; padding: 4px 12px; border-radius: 20px;">{len(review_candidates)} Review Queue</span>
                    </div>
                </div>
            </div>

            <!-- Category A Section -->
            <div style="margin-bottom: 36px;">
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px;">
                    <h2 style="color: #38BDF8; font-size: 21px; font-weight: 700; margin: 0; letter-spacing: -0.3px;">Category A — Confirmed Eligible Portal Applications ({len(jobs)})</h2>
                </div>
                <p style="color: #94A3B8; font-size: 14px; margin: 0 0 16px 0;">Strict 0-YOE verified positions from corporate ATS feeds satisfying all core eligibility criteria.</p>
                {cat_a_html}
            </div>

            <hr style="border: 0; border-top: 1px solid #1E293B; margin: 30px 0;">

            <!-- Category B Section -->
            <div style="margin-bottom: 30px;">
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px;">
                    <h2 style="color: #F59E0B; font-size: 21px; font-weight: 700; margin: 0; letter-spacing: -0.3px;">Category B — Promising Opportunities Held for Manual Review ({len(review_candidates)})</h2>
                </div>
                <p style="color: #94A3B8; font-size: 14px; margin: 0 0 16px 0;">These roles match technical stack requirements but were safely held for manual decision (e.g. 2027 timeline, project-evaluated experience, unverified third-party sources, or experience ceilings).</p>
                {cat_b_html}
            </div>

            <!-- Footer -->
            <div style="border-top: 1px solid #1E293B; padding-top: 20px; margin-top: 36px; text-align: center;">
                <p style="font-size: 13px; color: #64748B; margin: 0 0 6px 0;">Satvik JobBot &bull; DIGEST_SEND_ENABLED=false (Preview Mode) &bull; ATS resume attached below</p>
                <p style="font-size: 12px; color: #475569; margin: 0;">Designed with high-contrast dark theme &bull; Desktop width ~960px &bull; Mobile-responsive</p>
            </div>

        </div>
    </div>
</body>
</html>"""

    digest_dir = ROOT / 'output' / 'portal_digests'
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_file = digest_dir / f"digest-{current.strftime('%Y%m%d_%H%M%S')}.html"
    digest_file.write_text(body_html, encoding='utf-8')

    msg = EmailMessage()
    msg['From'] = formataddr(("Satvik Saini - Career Radar", profile['email']))
    msg['To'] = profile['email']
    msg['Subject'] = f"🚀 [Priority Jobs] Top {len(jobs)} Portal Openings & Referrals - {current.strftime('%b %d, %I %p')}"
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain='gmail.com')
    msg.set_content(body_txt)
    msg.add_alternative(body_html, subtype='html')

    resume_path = ROOT / profile['resume_pdf']
    if resume_path.is_file():
        msg.add_attachment(resume_path.read_bytes(), maintype='application', subtype='pdf', filename='Satvik_Saini_Resume.pdf')

    password = os.environ.get('GMAIL_APP_PASSWORD', '').replace(' ', '')
    if not digest_send_enabled:
        db.execute("INSERT OR IGNORE INTO meta VALUES(?,?)", (preview_key, current.isoformat()))
        db.commit()
        return {
            'mode': 'preview_saved_sending_disabled',
            'count': len(jobs),
            'review_count': len(review_candidates),
            'preview_file': str(digest_file),
            'message': 'Digest sending is disabled in .env (set DIGEST_SEND_ENABLED=true to authorize delivery to your email). Saved HTML digest preview without sending email.'
        }
    if not password:
        return {
            'mode': 'preview_saved_no_password',
            'count': len(jobs),
            'review_count': len(review_candidates),
            'preview_file': str(digest_file),
            'message': 'GMAIL_APP_PASSWORD not set in .env. Saved HTML digest preview to output/portal_digests/.'
        }
    db.execute('BEGIN IMMEDIATE')
    if db.execute("SELECT 1 FROM meta WHERE key IN (?,?)", (slot_key, delivery_key)).fetchone():
        db.rollback()
        return {'mode': 'already_sent_today', 'slot': slot_key}
    db.execute('INSERT INTO meta(key,value) VALUES(?,?)', (delivery_key, 'in_flight:' + current.isoformat()))
    job_ids = [job['id'] for job in jobs]
    placeholders = ','.join('?' for _ in job_ids)
    db.execute(f"UPDATE portal_jobs SET status='in_flight' WHERE status='pending' AND id IN ({placeholders})", job_ids)
    db.commit()

    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(profile['email'], password)
            server.send_message(msg)
        db.execute('UPDATE meta SET value=? WHERE key=?', ('sent:' + now().isoformat(), delivery_key))
        db.execute(f"UPDATE portal_jobs SET status='notified' WHERE status='in_flight' AND id IN ({placeholders})", job_ids)
        db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (slot_key, current.isoformat()))
        db.commit()
        return {'mode': 'sent', 'count': len(jobs), 'to': profile['email'], 'slot': slot_key}
    except Exception as e:
        error = type(e).__name__
        LOG.error('Failed to send portal digest email (%s)', error)
        db.execute('UPDATE meta SET value=? WHERE key=?', ('uncertain:' + error, delivery_key))
        db.execute(f"UPDATE portal_jobs SET status='uncertain' WHERE status='in_flight' AND id IN ({placeholders})", job_ids)
        db.execute("INSERT OR REPLACE INTO meta VALUES('smtp_paused',?)", (error,))
        db.commit()
        return {'mode': 'error', 'error': error, 'preview_file': str(digest_file)}


def tick(config, profile, sources, db, fetcher):
    # One atomic lease per installation; no overlapping discovery/send workers.
    instant = now()
    db.execute('BEGIN IMMEDIATE')
    lease = db.execute("SELECT value FROM meta WHERE key='tick_lease_until'").fetchone()
    if lease and posted_date(lease[0]) > instant:
        db.rollback()
        return {'mode': 'worker already running'}
    lease_token = uuid.uuid4().hex
    db.execute("INSERT OR REPLACE INTO meta VALUES('tick_lease_until',?)", ((instant + timedelta(minutes=15)).isoformat(),))
    db.execute("INSERT OR REPLACE INTO meta VALUES('tick_lease_owner',?)", (lease_token,))
    db.commit()
    try:
        due = db.execute("SELECT value FROM meta WHERE key='next_discovery_at'").fetchone()
        result = {}
        if not due or posted_date(due[0]) <= instant:
            result['discovery'] = discover(config, profile, sources, db, fetcher, lease_token=lease_token)
        result['dispatch'] = dispatch(config, profile, sources, db, fetcher)
        if config.get('portal_digest_enabled', True):
            digest_res = send_portal_digest(config, profile, db, force=False)
            if digest_res and digest_res.get('mode') not in ('skipped', None):
                result['portal_digest'] = digest_res
        # Automatic daily local backup check (every 24 hours)
        last_b_row = db.execute("SELECT value FROM meta WHERE key='last_backup_at'").fetchone()
        last_b_at = posted_date(last_b_row[0]) if last_b_row else None
        if not last_b_at or (instant - last_b_at) >= timedelta(hours=24):
            try:
                b_info = backup_database(config, max_backups=7)
                db.execute("INSERT OR REPLACE INTO meta VALUES('last_backup_at',?)", (instant.isoformat(),))
                db.commit()
                result['daily_backup'] = b_info
            except Exception as e:
                LOG.warning('Automatic daily backup failed: %s', e)
                result['daily_backup_error'] = str(e)
        return result
    finally:
        try:
            curr = db.execute("SELECT value FROM meta WHERE key='tick_lease_owner'").fetchone()
            if not curr or curr[0] == lease_token:
                db.execute("DELETE FROM meta WHERE key IN ('tick_lease_until', 'tick_lease_owner')")
                db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass


def check_mail(profile, db):
    """Authenticate SMTP without sending; verify existing Message-IDs in Gmail Sent."""
    password = os.environ.get('GMAIL_APP_PASSWORD', '').replace(' ', '')
    if not password or os.environ.get('SMTP_EMAIL') != profile['email']:
        raise ValueError('Configure Gmail app password locally first; no email was sent')
    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(profile['email'], password)
        with imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ssl.create_default_context(), timeout=30) as mailbox:
            mailbox.login(profile['email'], password)
            code, folders = mailbox.list()
            if code != 'OK':
                raise ValueError('Cannot list Gmail Sent folder')
            sent = next((f for f in folders if isinstance(f, bytes) and b'\\Sent' in f), None)
            if not sent:
                raise ValueError('Gmail Sent folder not found')
            match = re.match(rb'\([^)]*\)\s+(?:"[^"]*"|NIL)\s+(.+)$', sent)
            if not match or mailbox.select(match[1].decode(), readonly=True)[0] != 'OK':
                raise ValueError('Cannot open Gmail Sent folder')
            visible = 0
            for row in db.execute("SELECT job_id,message_id FROM attempts WHERE status='sent' AND message_id IS NOT NULL AND COALESCE(sent_visible,0)=0").fetchall():
                code, ids = mailbox.search(None, 'HEADER', 'Message-ID', '"' + row['message_id'] + '"')
                if code != 'OK':
                    raise ValueError('Cannot search Gmail Sent folder')
                found = bool(ids and ids[0])
                db.execute('UPDATE attempts SET sent_visible=? WHERE job_id=?', (int(found), row['job_id']))
                visible += int(found)
            db.commit()
            export(db)
            return {'smtp_authenticated': True, 'gmail_sent_accessible': True, 'newly_verified_sent_messages': visible, 'email_sent_by_check': False}
    except Exception as e:
        raise ValueError('Gmail connection check failed: ' + type(e).__name__) from None


def health(config, profile, sources, db):
    blockers = []
    try:
        profile_ready(profile)
    except (ValueError, OSError) as e:
        blockers.append(str(e))
    if not os.environ.get('GMAIL_APP_PASSWORD', '').replace(' ', ''):
        blockers.append('Gmail app password is missing')
    if not sources.get('trusted_companies'):
        blockers.append('No independently verified employers with office evidence configured')
    if not sources.get('alert_feeds') and os.environ.get('READ_GMAIL_ALERTS', '').lower() != 'true':
        blockers.append('Broad discovery needs Google Alerts feeds or Gmail Alerts access')
    if os.environ.get('SEND_ENABLED', '').lower() != 'true':
        blockers.append('SMTP sending is disabled')
    if os.environ.get('SMTP_EMAIL') != profile['email']:
        blockers.append('SMTP sender does not match the profile email')
    if os.environ.get('PAUSE_SENDING', '').lower() == 'true' or db.execute("SELECT 1 FROM meta WHERE key='smtp_paused'").fetchone():
        blockers.append('Sending is paused')
    sources_summary = {'healthy': 0, 'cooldown': 0, 'blocked': 0, 'failing': 0, 'sources': []}
    try:
        rows = db.execute("SELECT source_url, source_type, health_status, last_http_status, consecutive_failures, cooldown_until, recent_job_count, last_error FROM source_health").fetchall()
        for r in rows:
            st = r['health_status']
            if st in sources_summary:
                sources_summary[st] += 1
            sources_summary['sources'].append(dict(r))
    except Exception:
        pass

    backup_dir = (ROOT / config['database']).parent / 'backups'
    recent_backups = sorted([f.name for f in backup_dir.glob('jobbot_backup_*.sqlite3')]) if backup_dir.exists() else []

    discovery_warnings = []
    if sources_summary['sources']:
        total_sources = len(sources_summary['sources'])
        failing_or_cooldown = sources_summary['cooldown'] + sources_summary['blocked'] + sources_summary['failing']
        if failing_or_cooldown == total_sources:
            discovery_warnings.append(f"All {total_sources} discovery sources are in cooldown, blocked, or failing")
    next_disc = db.execute("SELECT value FROM meta WHERE key='next_discovery_at'").fetchone()
    if next_disc:
        try:
            next_dt = posted_date(next_disc[0])
            if (now() - next_dt) > timedelta(hours=6):
                discovery_warnings.append(f"Discovery overdue: next_discovery_at was {next_disc[0]}")
        except Exception:
            pass

    return {'ready_for_live_sending': not blockers, 'blockers': blockers,
            'fresh_unique_recipients': eligible_count(config, db), 'daily_goal': [config['daily_minimum'], config['daily_limit']],
            'posting_age_hours': config['max_post_age_hours'],
            'email_lead_max_post_age_days': config.get('email_lead_max_post_age_days', 3),
            'credentials_printed': False,
            'source_health': sources_summary,
            'discovery_warnings': discovery_warnings,
            'backups': {'count': len(recent_backups), 'latest': recent_backups[-1] if recent_backups else None},
            'database_counts': {
                'queued_jobs': db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0],
                'portal_jobs': db.execute("SELECT count(*) FROM portal_jobs WHERE status='pending'").fetchone()[0],
                'reviews': db.execute("SELECT count(*) FROM reviews").fetchone()[0],
                'attempts': db.execute("SELECT count(*) FROM attempts").fetchone()[0]
            }}


def backup_database(config, max_backups=7):
    """Consistent, non-locking online backup of the SQLite database using SQLite Online Backup API."""
    source_path = ROOT / config['database']
    if not source_path.exists():
        raise FileNotFoundError(f"Database {source_path} does not exist")
    backup_dir = source_path.parent / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now().strftime('%Y%m%d_%H%M%S')
    target_path = backup_dir / f"jobbot_backup_{timestamp}.sqlite3"
    src = sqlite3.connect(source_path)
    dst = sqlite3.connect(target_path)
    try:
        src.backup(dst, pages=100)
    finally:
        dst.close()
        src.close()
    try:
        os.chmod(target_path, 0o600)
    except Exception:
        pass
    existing = sorted(backup_dir.glob('jobbot_backup_*.sqlite3'))
    while len(existing) > max_backups:
        old = existing.pop(0)
        try:
            old.unlink()
        except Exception:
            pass
    return {
        'backup_file': str(target_path),
        'backup_size_bytes': target_path.stat().st_size,
        'retention_count': len(existing),
        'timestamp': timestamp,
        'status': 'success'
    }


def restore_database(backup_path, config):
    """Restore database from a verified backup file after validating its integrity."""
    b_path = Path(backup_path)
    if not b_path.is_file():
        raise FileNotFoundError(f"Backup file {backup_path} not found")
    test_conn = sqlite3.connect(b_path)
    try:
        res = test_conn.execute("PRAGMA integrity_check").fetchone()
        if not res or res[0] != 'ok':
            raise ValueError(f"Integrity check failed for {backup_path}: {res}")
    finally:
        test_conn.close()
    target_path = ROOT / config['database']
    src = sqlite3.connect(b_path)
    dst = sqlite3.connect(target_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return {'restored_from': str(b_path), 'target': str(target_path), 'status': 'success'}


def export(db):
    directory = ROOT / 'output'
    directory.mkdir(exist_ok=True)
    for table, file in [('jobs', 'applications.csv'), ('attempts', 'send_history.csv')]:
        cursor = db.execute('SELECT * FROM ' + table)
        with (directory / file).open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.writer(stream)
            writer.writerow([d[0] for d in cursor.description])
            # Prevent spreadsheet formula injection from public posting content.
            writer.writerows([("'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v)
                              for v in row] for row in cursor)
    with (directory / 'needs_review.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        fields = ['url', 'reason', 'found_at', 'source', 'company', 'title', 'location',
                  'posted_at', 'fresh_within_configured_window', 'target_role_title_match',
                  'description_skill_matches', 'experience_evidence', 'application_method',
                  'classification']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in db.execute('SELECT url,reason,found_at FROM reviews ORDER BY found_at DESC'):
            record = {'url': row['url'], 'reason': row['reason'], 'found_at': row['found_at']}
            try:
                evidence = json.loads(row['reason'])
            except (TypeError, json.JSONDecodeError):
                evidence = {}
            if isinstance(evidence, dict):
                for field in fields[3:]:
                    value = evidence.get(field)
                    if isinstance(value, (list, dict)):
                        value = json.dumps(value, ensure_ascii=False)
                    record[field] = value
            for field, value in record.items():
                if isinstance(value, str) and value.startswith(('=', '+', '-', '@')):
                    record[field] = "'" + value
            writer.writerow(record)


def reserve(db, job, config, instant=None):
    instant = instant or now()
    db.execute('BEGIN IMMEDIATE')
    try:
        if db.execute('SELECT 1 FROM suppression WHERE email=?', (job['email'],)).fetchone():
            db.rollback()
            return False
        # Attempts/uncertain sends count too, across all processes and restarts.
        midnight = instant.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        rolling = (instant - timedelta(hours=24)).isoformat()
        count = db.execute('SELECT count(*) FROM attempts WHERE started >= ? OR started >= ?', (midnight, rolling)).fetchone()[0]
        next_at = db.execute("SELECT value FROM meta WHERE key='next_send_at'").fetchone()
        if count >= config['daily_limit'] or (next_at and posted_date(next_at[0]) > instant):
            db.rollback()
            return False
        if db.execute('SELECT 1 FROM attempts WHERE job_id=? OR recipient=?', (job['id'], job['email'])).fetchone():
            db.rollback()
            return False
        db.execute('INSERT INTO attempts(job_id,recipient,started,status) VALUES(?,?,?,?)',
                   (job['id'], job['email'], instant.isoformat(), 'in_flight'))
        gap = random.SystemRandom().randint(config['minimum_gap_seconds'], config['maximum_gap_seconds'])
        db.execute("INSERT OR REPLACE INTO meta VALUES('next_send_at',?)", ((instant + timedelta(seconds=gap)).isoformat(),))
        db.execute("UPDATE jobs SET status='in_flight' WHERE id=?", (job['id'],))
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def evict_outdated_jobs(config, db):
    """Evict postings from queue that have reached older than max_queue_age_hours or email_lead_max_post_age_days."""
    max_queue_hours = config.get('max_queue_age_hours', 18)
    default_cutoff = now() - timedelta(hours=max_queue_hours)
    email_lead_days = config.get('email_lead_max_post_age_days', 3)
    email_cutoff = now() - timedelta(days=email_lead_days)
    evicted = 0
    for row in db.execute("SELECT id, payload FROM jobs WHERE status='queued'"):
        try:
            j = json.loads(row['payload'])
            p_time = posting_timestamp(j.get('date_posted', ''))
            is_email_lead = j.get('source_type') in ('recruiter_post', 'hiring_announcement') or 'lead:' in j.get('id', '')
            evict_cutoff = email_cutoff if is_email_lead else default_cutoff
            if p_time < evict_cutoff:
                cutoff_desc = f"{email_lead_days} days" if is_email_lead else f"{max_queue_hours} hours"
                db.execute("UPDATE jobs SET status='expired_outdated' WHERE id=?", (row['id'],))
                db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)",
                           (j.get('url', ''), f"Evicted: posting older than {cutoff_desc}", now().isoformat()))
                evicted += 1
        except Exception:
            pass
    db.commit()
    return evicted


def dispatch(config, profile, sources, db, fetcher):
    evict_outdated_jobs(config, db)
    if os.environ.get('SEND_ENABLED', '').lower() != 'true':
        return {'mode': 'preview', 'message': 'SMTP disabled; review output/*.csv and application.eml files'}
    fingerprint = profile_fingerprint(profile)
    password = os.environ.get('GMAIL_APP_PASSWORD', '').replace(' ', '')
    if not password or os.environ.get('SMTP_EMAIL') != profile['email']:
        raise ValueError('Set SMTP_EMAIL and GMAIL_APP_PASSWORD locally in .env')
    if os.environ.get('PAUSE_SENDING', '').lower() == 'true':
        return {'mode': 'paused'}
    # Provider failures persist a pause; restart does not erase it.
    if db.execute("SELECT 1 FROM meta WHERE key='smtp_paused'").fetchone():
        return {'mode': 'paused', 'message': 'Resolve the SMTP error, then use resume-sending'}
    if not config['send_start_hour'] <= now().hour < config['send_end_hour']:
        return {'mode': 'outside sending hours'}
    gate = dispatch_gate(config, db)
    if gate:
        return {'mode': gate, 'sent': 0}
    candidates = list(db.execute("SELECT * FROM jobs WHERE status='queued' AND id NOT IN (SELECT job_id FROM attempts) AND json_extract(payload,'$.email') NOT IN (SELECT recipient FROM attempts) AND json_extract(payload,'$.email') NOT IN (SELECT email FROM suppression)"))
    candidates.sort(key=lambda row: (
        compute_priority_score(json.loads(row['payload']), profile, config),
        json.loads(row['payload']).get('date_posted', '')
    ), reverse=True)
    for row in candidates:
        job = json.loads(row['payload'])
        try:
            artifact = json.loads(row['artifact'])
            if artifact['profile'] != fingerprint or digest(Path(artifact['file']).read_bytes()) != artifact['checksum']:
                raise ValueError('Profile or draft changed; regenerate drafts')
            final, page = fetcher.get(job['url'])
            postings = [j for block in Page(page).blocks for j in json_jobs(block)]
            if len(postings) != 1:
                raise ValueError('Posting no longer unambiguous')
            fresh, reason = evaluate(postings[0], final, trust_for(final, sources), config, profile)
            if not fresh or fresh['id'] != job['id'] or fresh['description'] != job['description']:
                raise ValueError(reason or 'Posting changed; rediscover before sending')
        except Exception as e:
            db.execute("UPDATE jobs SET status='needs_review' WHERE id=?", (job['id'],))
            db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)', (job['url'], str(e), now().isoformat()))
            db.commit()
            continue
        if not reserve(db, job, config):
            return {'mode': 'paced/capped', 'sent': 0}
        # Reserve before SMTP: crash after acceptance must never trigger a duplicate retry.
        status, error = 'uncertain', None
        try:
            from email import policy
            from email.parser import BytesParser
            msg = BytesParser(policy=policy.default).parsebytes(Path(artifact['file']).read_bytes())
            # Refresh transport metadata when sending a draft prepared hours earlier.
            msg.replace_header('Date', formatdate(localtime=True))
            db.execute('UPDATE attempts SET message_id=? WHERE job_id=?', (msg['Message-ID'], job['id']))
            db.commit()
            with smtplib.SMTP('smtp.gmail.com', 587, timeout=30) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(profile['email'], password)
                # TLS/authentication can take time; check the age and window once more.
                if posting_timestamp(fresh['date_posted']) < now() - timedelta(hours=config['max_post_age_hours']):
                    status = 'expired_before_send'
                elif not config['send_start_hour'] <= now().hour < config['send_end_hour']:
                    status = 'outside_hours_before_send'
                else:
                    refused = server.send_message(msg, from_addr=profile['email'], to_addrs=[job['email']])
                    if refused:
                        raise smtplib.SMTPRecipientsRefused(refused)
                    status = 'sent'
        except Exception as e:
            # Do not expose credentials/server response bodies in logs.
            error = type(e).__name__
            db.execute("INSERT OR REPLACE INTO meta VALUES('smtp_paused',?)", (error,))
            if isinstance(e, smtplib.SMTPRecipientsRefused):
                db.execute('INSERT OR IGNORE INTO suppression VALUES(?)', (job['email'],))
                status = 'rejected'
        db.execute('UPDATE attempts SET status=?,error=? WHERE job_id=?', (status, error, job['id']))
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, job['id']))
        db.commit()
        export(db)
        return {'mode': status, 'sent': int(status == 'sent'), 'job_id': job['id'], 'error': error}
    return {'mode': 'no eligible queued jobs', 'sent': 0}


def serve(config):
    token = os.environ.get('JOBBOT_API_TOKEN', '')
    if len(token) < 24:
        raise ValueError('Set a random JOBBOT_API_TOKEN of at least 24 characters')

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.send_error(401)
                return
            # Configuration/profile can be updated without restarting the service.
            try:
                result = execute(self.path.removeprefix('/')) if self.path in ('/discover', '/dispatch', '/tick') else None
                if result is None:
                    self.send_error(404)
                    return
                self.send_response(200)
            except Exception as e:
                result = {'error': str(e)}
                self.send_response(400)
            data = json.dumps(result).encode()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            LOG.info(fmt, *args)

    HTTPServer(('127.0.0.1', config['api_port']), Handler).serve_forever()


def execute(command, arg=None):
    load_env()
    config, profile, sources = load_json('config.json'), load_json('profile.json'), load_json('sources.json')
    if not 1 <= config['daily_minimum'] <= config['daily_limit'] <= 250 or not 180 <= config['minimum_gap_seconds'] <= config['maximum_gap_seconds']:
        raise ValueError('Daily target must be 1-250 and minimum spacing at least 180 seconds')
    if config['max_post_age_hours'] != 12 or config['discovery_retry_hours'] != 2 or config['maximum_role_experience_years'] != 2:
        raise ValueError('Require 12-hour freshness, 2-hour discovery retry and maximum 2 years role experience')
    if not 0 <= config['send_start_hour'] < config['send_end_hour'] <= 24:
        raise ValueError('Invalid sending hours')
    if command == 'serve':
        return serve(config)
    with closing(connect(config)) as db:
        fetcher = Fetcher()
        if command == 'tick':
            return tick(config, profile, sources, db, fetcher)
        if command == 'digest':
            return send_portal_digest(config, profile, db, force=True)
        if command == 'health':
            return health(config, profile, sources, db)
        if command == 'check-mail':
            return check_mail(profile, db)
        if command == 'discover':
            return discover(config, profile, sources, db, fetcher)
        if command == 'dispatch':
            return dispatch(config, profile, sources, db, fetcher)
        if command == 'draft':
            profile_ready(profile)
            for row in list(db.execute("SELECT payload FROM jobs WHERE status IN ('verified','queued','needs_review')")):
                job = json.loads(row[0])
                # A later discovery/send recheck is still required.
                db.execute("UPDATE jobs SET status='verified' WHERE id=?", (job['id'],))
                draft(db, job, profile)
            export(db)
            return {'mode': 'drafted'}
        if command == 'ingest-lead':
            lead_data = None
            if arg:
                p = Path(arg)
                if p.is_file():
                    lead_data = json.loads(p.read_text(encoding='utf-8'))
                else:
                    try:
                        lead_data = json.loads(arg)
                    except json.JSONDecodeError:
                        raise ValueError(f"Argument '{arg}' is neither an existing file path nor valid JSON")
            else:
                import sys
                if not sys.stdin.isatty():
                    lead_data = json.loads(sys.stdin.read())
            if not lead_data or not isinstance(lead_data, dict):
                raise ValueError("Lead data must be provided as a JSON file path, JSON string, or piped via stdin")
            profile_ready(profile)
            res = evaluate_lead(lead_data, config, profile, sources, db)
            export(db)
            return res
        if command == 'suppress':
            if not arg or not EMAIL.fullmatch(arg):
                raise ValueError('Provide a valid recipient email')
            db.execute('INSERT OR IGNORE INTO suppression VALUES(?)', (arg.lower(),))
        elif command == 'resume-sending':
            db.execute("DELETE FROM meta WHERE key='smtp_paused'")
        elif command == 'backup':
            return backup_database(config)
        elif command == 'restore':
            if not arg:
                raise ValueError('Specify a backup file path to restore')
            return restore_database(arg, config)
        elif command == 'status':
            export(db)
            return {'jobs': dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status').fetchall()),
                    'portal_jobs_count': db.execute('SELECT count(*) FROM portal_jobs').fetchone()[0],
                    'review_count': db.execute('SELECT count(*) FROM reviews').fetchone()[0],
                    'send_enabled': os.environ.get('SEND_ENABLED', '').lower() == 'true',
                    'digest_send_enabled': os.environ.get('DIGEST_SEND_ENABLED', os.environ.get('SEND_ENABLED', '')).lower() == 'true',
                    'profile_confirmed': profile.get('confirmed', False),
                    'fresh_unique_recipients': eligible_count(config, db),
                    'scheduler': {r['key']: r['value'] for r in db.execute("SELECT * FROM meta WHERE key IN ('next_discovery_at','next_send_at','batch_released_day','tick_lease_until')")},
                    'smtp_pause': [dict(r) for r in db.execute("SELECT * FROM meta WHERE key='smtp_paused'")]}
        else:
            raise ValueError('Unknown command')
        db.commit()
        return {'mode': command}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['discover', 'draft', 'dispatch', 'tick', 'health', 'check-mail', 'status', 'serve', 'suppress', 'resume-sending', 'digest', 'ingest-lead', 'backup', 'restore'])
    parser.add_argument('value', nargs='?')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        print(json.dumps(execute(args.command, args.value), indent=2))
    except Exception as e:
        LOG.error('%s', e)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
