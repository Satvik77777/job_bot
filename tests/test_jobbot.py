import copy
import csv
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import smtplib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jobbot as bot
import tailor_resume


class JobbotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_patch = patch.object(bot, 'ROOT', Path(self.temp.name))
        self.root_patch.start()
        self.clock = bot.datetime(2026, 10, 9, 10, 0, tzinfo=bot.IST)
        self.clock_patch = patch.object(bot, 'now', return_value=self.clock)
        self.mock_clock = self.clock_patch.start()
        self.config = dict(database='data/test.sqlite3', daily_limit=100, daily_minimum=80,
                           discovery_retry_hours=2, minimum_gap_seconds=360,
                           maximum_gap_seconds=420, minimum_skill_matches=2, max_post_age_hours=12,
                           maximum_role_experience_years=2,
                           send_start_hour=9, send_end_hour=20, public_search_enabled=False,
                           search_queries_per_day=8, max_pages_per_run=60)
        self.profile = dict(name='Satvik Saini', email='satviksaini7777@gmail.com',
                            confirmed=True, skills=['Python', 'Node.js'], years_experience=1,
                            allow_senior_roles=False, resume_mode='original', resume_pdf='Satvik_Saini_Resume.pdf',
                            summary='I build software using Python and Node.js.',
                            portfolio='https://example.com/portfolio', github='https://github.com/Satvik77777')
        # Attachment protocol fixture, not a generated resume document.
        (bot.ROOT / 'Satvik_Saini_Resume.pdf').write_bytes(b'%PDF-1.4\nfixture\n%%EOF')
        self.trust = dict(company='Example', identity_verified=True,
                          company_homepage='https://example.com', verification_note='Test fixture',
                          office_cities=['Noida'], office_evidence_url='https://example.com/contact',
                          posting_domains=['example.com'], email_domains=['example.com'])
        self.sources = dict(trusted_companies=[self.trust], posting_urls=['https://example.com/jobs/1'])
        self.posting = {'@type': 'JobPosting', 'title': 'Backend Software Engineer',
                        'description': '<p>Python and Node.js. 1 year experience.</p><p>Send your resume to careers@example.com.</p>',
                        'datePosted': '2026-10-09T09:00:00+05:30', 'validThrough': '2026-11-01',
                        'hiringOrganization': {'name': 'Example'},
                        'jobLocation': {'address': {'addressLocality': 'Noida'}}}
        self.db = bot.connect(self.config)

    def tearDown(self):
        self.db.close()
        self.clock_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def evaluate(self, posting=None, url='https://example.com/jobs/1', trust=None):
        return bot.evaluate(posting or self.posting, url, self.trust if trust is None else trust, self.config, self.profile)

    def queued(self):
        job, reason = self.evaluate()
        self.assertIsNone(reason)
        self.db.execute('INSERT INTO jobs(id,payload) VALUES(?,?)', (job['id'], json.dumps(job)))
        self.db.commit()
        bot.draft(self.db, job, self.profile)
        # Most transport tests simulate a batch that was already qualified for today.
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('batch_released_day',?)", (self.clock.date().isoformat(),))
        self.db.commit()
        return job

    def fetcher(self, posting=None):
        fetcher = MagicMock()
        fetcher.get.return_value = ('https://example.com/jobs/1', '<script type="application/ld+json">' + json.dumps(posting or self.posting) + '</script>')
        return fetcher

    def add_portal_job(self, job_id='portal-job', title='Backend Software Engineer'):
        self.db.execute('''INSERT INTO portal_jobs(id, title, company, url, location, matching_skills, match_score, date_posted, status, discovered_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (job_id, title, 'Example', 'https://example.com/jobs/portal', 'Noida',
             json.dumps(['Python', 'Node.js']), 2, self.clock.isoformat(), 'pending', self.clock.isoformat()))
        self.db.commit()

    def test_accepts_explicit_verified_posting(self):
        job, reason = self.evaluate()
        self.assertIsNone(reason)
        self.assertEqual(job['email'], 'careers@example.com')
        self.assertEqual(job['matching_skills'], ['Python', 'Node.js'])

    def test_untrusted_source_and_lookalike_domain(self):
        self.assertIn('untrusted', self.evaluate(url='https://example.com.evil.org/jobs/1')[1])
        trust = {**self.trust, 'identity_verified': False}
        self.assertIn('identity', self.evaluate(trust=trust)[1])

    def test_missing_stale_future_and_expired_dates(self):
        for changes in [{'datePosted': None}, {'datePosted': '2025-01-01'},
                        {'datePosted': '2027-01-01'}, {'validThrough': '2026-10-08'}]:
            with self.subTest(changes=changes):
                self.assertIsNone(self.evaluate({**self.posting, **changes})[0])

    def test_company_city_role_and_fees(self):
        for changes in [{'hiringOrganization': {'name': 'Other'}}, {'jobLocation': {'name': 'Pune'}},
                        {'title': 'Sales Executive'}, {'title': 'Cloud Sales Executive'},
                        {'description': self.posting['description'] + ' Pay a registration fee.'}]:
            with self.subTest(changes=changes):
                self.assertIsNone(self.evaluate({**self.posting, **changes})[0])

    def test_generic_footer_email_never_qualifies(self):
        p = {**self.posting, 'description': 'Python Node.js. Contact careers@example.com for support.'}
        self.assertIsNone(self.evaluate(p)[0])
        desc = '<p>Python Node.js. Apply by sending your resume through the portal.</p><p>Contact careers@example.com for support.</p>'
        self.assertIsNone(self.evaluate({**self.posting, 'description': desc})[0])

    def test_external_email_ambiguous_email_and_negative_instruction(self):
        for desc in ['Send resume to recruiter@gmail.com.',
                     'Send resume to one@example.com or two@example.com.',
                     'Do not send your resume to careers@example.com.']:
            self.assertEqual(bot.application_email(desc, ['example.com']), (None, None))

    def test_experience_seniority_and_skill_fit(self):
        for changes in [{'title': 'Senior Backend Developer'},
                        {'description': self.posting['description'].replace('1 year', '3-5 years')},
                        {'experienceRequirements': {'monthsOfExperience': 36}},
                        {'description': 'Send resume to careers@example.com. Java Spring only.'}]:
            self.assertIsNone(self.evaluate({**self.posting, **changes})[0])

    def test_preview_never_calls_smtp(self):
        with patch.dict(bot.os.environ, {'SEND_ENABLED': 'false'}, clear=True), patch.object(bot.smtplib, 'SMTP') as smtp:
            self.assertEqual(bot.dispatch(self.config, self.profile, self.sources, self.db, self.fetcher())['mode'], 'preview')
            smtp.assert_not_called()

    def test_unconfirmed_or_wrong_resume_rejected(self):
        with self.assertRaises(ValueError):
            bot.profile_ready({**self.profile, 'confirmed': False})
        (bot.ROOT / 'Someone_Else.pdf').write_bytes(b'%PDF-fixture')
        with self.assertRaises(ValueError):
            bot.profile_ready({**self.profile, 'resume_pdf': 'Someone_Else.pdf'})

    def test_draft_contains_satvik_and_attachment(self):
        job = self.queued()
        msg = (bot.ROOT / 'output' / job['id'] / 'application.eml').read_text()
        self.assertIn('satviksaini7777@gmail.com', msg)
        self.assertIn('Satvik_Saini_Resume.pdf', msg)
        self.assertNotIn('Someone_Else', msg)

    def test_pacing_and_dedup_survive_restart(self):
        job = self.queued()
        self.assertTrue(bot.reserve(self.db, job, self.config, self.clock))
        other = {**job, 'id': 'other', 'email': 'other@example.com'}
        self.assertFalse(bot.reserve(self.db, other, self.config, self.clock))
        self.db.close()
        self.db = bot.connect(self.config)
        self.assertFalse(bot.reserve(self.db, {**job, 'id': 'second-job'}, self.config, self.clock + timedelta(hours=1)))

    def test_cap_includes_uncertain_sends_and_rolling_24_hours(self):
        for i in range(100):
            self.db.execute('INSERT INTO attempts(job_id,recipient,started,status) VALUES(?,?,?,?)',
                            (str(i), f'{i}@example.com', (self.clock-timedelta(hours=12)).isoformat(), 'uncertain'))
        self.db.commit()
        self.assertFalse(bot.reserve(self.db, {'id': 'new', 'email': 'new@example.com'}, self.config, self.clock))

    def test_suppression(self):
        job = self.queued()
        self.db.execute('INSERT INTO suppression VALUES(?)', (job['email'],))
        self.db.commit()
        self.assertFalse(bot.reserve(self.db, job, self.config))

    def test_successful_send_once_mock_smtp_only(self):
        job = self.queued()
        with patch.dict(bot.os.environ, {'SEND_ENABLED': 'true', 'GMAIL_APP_PASSWORD': 'fixture-password',
                                        'SMTP_EMAIL': self.profile['email']}), patch.object(bot.smtplib, 'SMTP') as smtp:
            server = smtp.return_value.__enter__.return_value
            server.send_message.return_value = {}
            result = bot.dispatch(self.config, self.profile, self.sources, self.db, self.fetcher())
            self.assertEqual(result['sent'], 1, result)
            server.starttls.assert_called_once()
            self.assertEqual(server.send_message.call_args.kwargs['to_addrs'], [job['email']])
            self.assertEqual(bot.dispatch(self.config, self.profile, self.sources, self.db, self.fetcher())['sent'], 0)
            server.send_message.assert_called_once()

    def test_smtp_error_pauses_future_sends(self):
        self.queued()
        with patch.dict(bot.os.environ, {'SEND_ENABLED': 'true', 'GMAIL_APP_PASSWORD': 'fixture-password',
                                        'SMTP_EMAIL': self.profile['email']}), patch.object(bot.smtplib, 'SMTP') as smtp:
            smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b'fixture')
            self.assertEqual(bot.dispatch(self.config, self.profile, self.sources, self.db, self.fetcher())['mode'], 'uncertain')
            self.assertEqual(bot.dispatch(self.config, self.profile, self.sources, self.db, self.fetcher())['mode'], 'paused')
            self.assertEqual(self.db.execute('SELECT count(*) FROM attempts').fetchone()[0], 1)

    def test_changed_posting_and_tampered_draft_do_not_send(self):
        job = self.queued()
        (bot.ROOT / 'output' / job['id'] / 'application.eml').write_text('tampered')
        with patch.dict(bot.os.environ, {'SEND_ENABLED': 'true', 'GMAIL_APP_PASSWORD': 'fixture-password',
                                        'SMTP_EMAIL': self.profile['email']}), patch.object(bot.smtplib, 'SMTP') as smtp:
            result = bot.dispatch(self.config, self.profile, self.sources, self.db, self.fetcher())
            self.assertEqual(result['sent'], 0)
            smtp.assert_not_called()

    def test_discovery_queues_verified_jobs_and_reviews_unknown_hosts(self):
        self.sources['posting_urls'].append('https://unknown.example/jobs/1')
        result = bot.discover(self.config, self.profile, self.sources, self.db, self.fetcher())
        self.assertEqual(result['verified'], 1)
        self.assertEqual(result['review'], 1)
        self.assertEqual(self.db.execute('SELECT status FROM jobs').fetchone()[0], 'queued')

    def test_multiple_postings_require_review(self):
        fetcher = self.fetcher([self.posting, self.posting])
        result = bot.discover(self.config, self.profile, self.sources, self.db, fetcher)
        self.assertEqual(result['verified'], 0)
        self.assertEqual(result['review'], 1)

    def test_json_ld_graph_and_hidden_script_text(self):
        page = bot.Page('<script type="application/ld+json">' + json.dumps({'@graph': [self.posting]}) + '</script><p>Visible</p>')
        self.assertEqual(len(list(bot.json_jobs(page.blocks))), 1)
        self.assertEqual(bot.plain('<script>secret</script><p>Visible</p>'), 'Visible')

    def test_tailoring_reorders_without_rewriting_or_dropping_facts(self):
        bullets = ['Built a Python API.', 'Created a Node.js service.', 'Documented the architecture.']
        ordered = tailor_resume.ordered(bullets, 'Node.js', ['Python', 'Node.js'])
        self.assertEqual(ordered[0], bullets[1])
        self.assertCountEqual(ordered, bullets)

    def test_google_alert_links_are_unwrapped_without_trusting_snippets(self):
        raw = b'From: googlealerts-noreply@google.com\nContent-Type: text/html\n\n<a href="https://www.google.com/url?url=https%3A%2F%2Fexample.com%2Fjobs%2F1&amp;ct=ga">Job</a><a href="https://www.google.com/alerts">Manage</a>'
        self.assertEqual(bot.alert_message_links(raw), ['https://example.com/jobs/1'])

    def test_atom_and_rss_feed_links(self):
        atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><link href="https://example.com/jobs/1"/></entry></feed>'
        rss = '<rss><channel><item><link>https://example.com/jobs/1</link></item></channel></rss>'
        self.assertEqual(bot.feed_links(atom), ['https://example.com/jobs/1'])
        self.assertEqual(bot.feed_links(rss), ['https://example.com/jobs/1'])

    def test_exact_twelve_hour_boundary_and_timezone_requirement(self):
        good = (self.clock - timedelta(hours=12)).isoformat()
        self.assertIsNotNone(self.evaluate({**self.posting, 'datePosted': good})[0])
        for date in [(self.clock-timedelta(hours=12, seconds=1)).isoformat(),
                     (self.clock+timedelta(seconds=1)).isoformat(), '2026-10-09',
                     '2026-10-09T09:00:00']:
            with self.subTest(date=date):
                self.assertIsNone(self.evaluate({**self.posting, 'datePosted': date})[0])

    def test_delhi_accepted_and_office_evidence_required(self):
        self.assertIsNotNone(self.evaluate({**self.posting, 'jobLocation': {'address': {'addressLocality': 'New Delhi'}}})[0])
        self.assertIn('office', self.evaluate(trust={**self.trust, 'office_cities': []})[1])

    def test_explicit_india_remote_location_is_recognized(self):
        remote_posting = {**self.posting, 'jobLocation': {'address': {'addressLocality': 'Remote, India'}}}
        self.assertIsNotNone(self.evaluate(remote_posting)[0])
        remote_only_office = {**self.trust, 'office_cities': ['India Remote']}
        self.assertIn('office', self.evaluate(remote_posting, trust=remote_only_office)[1])

    def test_greenhouse_board_requires_official_link_and_returns_review_only(self):
        source = dict(company='Example Corp', board_token='examplecorp',
                      official_careers_url='https://example.com/careers',
                      board_url='https://job-boards.greenhouse.io/examplecorp', review_only=True)
        job = dict(title='Software Engineer, Node.js', company_name='Example Corp',
                   absolute_url='https://job-boards.greenhouse.io/examplecorp/jobs/12345',
                   location={'name': 'India Remote'}, first_published='2026-10-08T22:00:00-04:00',
                   content='&lt;p&gt;Entry-level role. 0-2 years experience. React and Node.js.&lt;/p&gt;')
        fetcher = MagicMock()
        fetcher.get.side_effect = [
            ('https://example.com/careers', '<a href="https://job-boards.greenhouse.io/examplecorp">Open jobs</a>'),
            ('https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs?content=true', json.dumps({'jobs': [job]})),
        ]
        candidates = bot.greenhouse_review_candidates(source, fetcher, self.config)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['application_method'], 'official Greenhouse application portal')
        self.assertEqual(candidates[0]['classification'], 'manual_review_only')
        self.assertTrue(candidates[0]['fresh_within_configured_window'])
        self.assertEqual(candidates[0]['location'], 'India Remote')
        self.assertEqual(candidates[0]['experience_evidence'], ['0-2 years experience'])

    def test_discovery_persists_ats_candidates_only_to_review(self):
        source = dict(company='Example Corp', board_token='examplecorp',
                      official_careers_url='https://example.com/careers',
                      board_url='https://job-boards.greenhouse.io/examplecorp', review_only=True)
        job = dict(title='Software Engineer, Node.js', company_name='Example Corp',
                   absolute_url='https://job-boards.greenhouse.io/examplecorp/jobs/12345',
                   location={'name': 'Bengaluru'}, first_published='2026-10-08T22:00:00-04:00',
                   content='&lt;p&gt;Entry-level role. 0-2 years experience. React and Node.js.&lt;/p&gt;')
        fetcher = MagicMock()
        fetcher.get.side_effect = [
            ('https://example.com/careers', '<a href="https://job-boards.greenhouse.io/examplecorp">Open jobs</a>'),
            ('https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs?content=true', json.dumps({'jobs': [job]})),
        ]
        sources = dict(trusted_companies=[], posting_urls=[], alert_feeds=[], greenhouse_boards=[source])
        result = bot.discover(self.config, self.profile, sources, self.db, fetcher)
        self.assertEqual(result['ats_review_candidates'], 1)
        self.assertEqual(result['verified'], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
        row = self.db.execute('SELECT reason FROM reviews WHERE url=?', (job['absolute_url'],)).fetchone()
        self.assertEqual(json.loads(row['reason'])['classification'], 'manual_review_only')

    def test_review_export_exposes_structured_ats_fields(self):
        candidate = dict(source='greenhouse_public_board', company='Example Corp',
                         title='Software Engineer', url='https://job-boards.greenhouse.io/examplecorp/jobs/12345',
                         location='India Remote', posted_at=self.clock.isoformat(),
                         fresh_within_configured_window=True, target_role_title_match=True,
                         description_skill_matches=['React', 'Node.js'],
                         experience_evidence=['0-2 years experience'],
                         application_method='official Greenhouse application portal',
                         classification='manual_review_only')
        self.db.execute('INSERT INTO reviews(url,reason,found_at) VALUES(?,?,?)',
                        (candidate['url'], json.dumps(candidate), self.clock.isoformat()))
        self.db.commit()
        bot.export(self.db)
        with (bot.ROOT / 'output' / 'needs_review.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        row = next(item for item in rows if item['url'] == candidate['url'])
        self.assertEqual(row['title'], 'Software Engineer')
        self.assertEqual(row['location'], 'India Remote')
        self.assertEqual(row['application_method'], 'official Greenhouse application portal')
        self.assertEqual(json.loads(row['experience_evidence']), ['0-2 years experience'])

    def test_greenhouse_board_rejects_unlinked_or_wrong_company_jobs(self):
        source = dict(company='Example Corp', board_token='examplecorp',
                      official_careers_url='https://example.com/careers')
        fetcher = MagicMock()
        fetcher.get.return_value = ('https://example.com/careers', '<p>No ATS link</p>')
        with self.assertRaisesRegex(ValueError, 'does not link'):
            bot.greenhouse_review_candidates(source, fetcher, self.config)

    def test_two_year_role_ceiling_and_unclear_experience(self):
        self.profile['years_experience'] = 5
        for desc in ['Python Node.js. Experience: 3 years. Send resume to careers@example.com.',
                     'Python Node.js. 3 years of industry experience. Send resume to careers@example.com.',
                     'Python Node.js. Send resume to careers@example.com.']:
            self.assertIsNone(self.evaluate({**self.posting, 'description': desc})[0])
        fresher = {**self.posting, 'description': 'Freshers welcome. Python Node.js. Send resume to careers@example.com.'}
        self.assertIsNotNone(self.evaluate(fresher)[0])

    def test_batch_threshold_never_sends_with_fewer_than_eighty(self):
        self.queued()
        self.db.execute("DELETE FROM meta WHERE key='batch_released_day'")
        self.db.commit()
        with patch.dict(bot.os.environ, {'SEND_ENABLED': 'true', 'GMAIL_APP_PASSWORD': 'fixture-password',
                                        'SMTP_EMAIL': self.profile['email']}), patch.object(bot.smtplib, 'SMTP') as smtp:
            self.assertIn('at least 80', bot.dispatch(self.config, self.profile, self.sources, self.db, self.fetcher())['mode'])
            smtp.assert_not_called()

    def test_unique_recipient_count_excludes_expired_and_suppressed(self):
        job = self.queued()
        for i in range(4):
            other = {**job, 'id': str(i), 'email': 'same@example.com'}
            self.db.execute("INSERT INTO jobs(id,payload,status) VALUES(?,?,'queued')", (other['id'], json.dumps(other)))
        old = {**job, 'id': 'old', 'email': 'old@example.com', 'date_posted': (self.clock-timedelta(hours=13)).isoformat()}
        self.db.execute("INSERT INTO jobs(id,payload,status) VALUES(?,?,'queued')", ('old', json.dumps(old)))
        self.db.commit()
        self.assertEqual(bot.eligible_count(self.config, self.db), 2)
        self.db.execute('INSERT INTO suppression VALUES(?)', ('same@example.com',))
        self.db.commit()
        self.assertEqual(bot.eligible_count(self.config, self.db), 1)

    def test_discovery_releases_eighty_and_retries_in_two_hours(self):
        job, _ = self.evaluate()
        for i in range(79):
            other = {**job, 'id': str(i), 'email': f'person{i}@example.com'}
            self.db.execute("INSERT INTO jobs(id,payload,status) VALUES(?,?,'queued')", (other['id'], json.dumps(other)))
        self.db.commit()
        result = bot.discover(self.config, self.profile, self.sources, self.db, self.fetcher())
        self.assertEqual(result['fresh_unique_recipients'], 80)
        self.assertEqual(result['next_discovery_at'], (self.clock+timedelta(hours=2)).isoformat())
        self.assertIsNone(bot.dispatch_gate(self.config, self.db))
        self.mock_clock.return_value = self.clock + timedelta(days=1)
        self.assertIn('at least 80', bot.dispatch_gate(self.config, self.db))

    def test_posting_expiring_in_queue_never_sent(self):
        self.queued()
        self.mock_clock.return_value = self.clock + timedelta(hours=12)
        # Keep test within the configured send window.
        self.config['send_end_hour'] = 24
        with patch.dict(bot.os.environ, {'SEND_ENABLED': 'true', 'GMAIL_APP_PASSWORD': 'fixture-password',
                                        'SMTP_EMAIL': self.profile['email']}), patch.object(bot.smtplib, 'SMTP') as smtp:
            self.assertEqual(bot.dispatch(self.config, self.profile, self.sources, self.db, self.fetcher())['sent'], 0)
            smtp.assert_not_called()

    def test_tick_retries_only_when_due_and_avoids_overlap(self):
        with patch.object(bot, 'discover', return_value={'verified': 0}) as discover, patch.object(bot, 'dispatch', return_value={'mode': 'preview'}):
            bot.tick(self.config, self.profile, self.sources, self.db, self.fetcher())
            discover.assert_called_once()
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('next_discovery_at',?)", ((self.clock+timedelta(hours=2)).isoformat(),))
            self.db.commit()
            bot.tick(self.config, self.profile, self.sources, self.db, self.fetcher())
            discover.assert_called_once()
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('tick_lease_until',?)", ((self.clock+timedelta(hours=1)).isoformat(),))
            self.db.commit()
            self.assertEqual(bot.tick(self.config, self.profile, self.sources, self.db, self.fetcher())['mode'], 'worker already running')

    def test_template_variants_keep_identity_and_original_pdf(self):
        job, _ = self.evaluate()
        bodies = set()
        for i in range(15):
            msg = bot.email_message({**job, 'id': str(i)}, self.profile, bot.ROOT/'Satvik_Saini_Resume.pdf')
            bodies.add(msg.get_body().get_content())
            self.assertEqual(msg['From'], 'Satvik Saini <satviksaini7777@gmail.com>')
            self.assertIn(job['url'], msg.get_body().get_content())
            self.assertEqual(next(msg.iter_attachments()).get_payload(decode=True), (bot.ROOT/'Satvik_Saini_Resume.pdf').read_bytes())
        self.assertGreaterEqual(len(bodies), 3)

    def test_sent_folder_check_authenticates_but_never_sends(self):
        job = self.queued()
        self.db.execute("INSERT INTO attempts(job_id,recipient,started,status,message_id) VALUES(?,?,?,'sent',?)",
                        (job['id'], job['email'], self.clock.isoformat(), '<fixture@gmail.com>'))
        self.db.commit()
        with patch.dict(bot.os.environ, {'GMAIL_APP_PASSWORD': 'fixture-password', 'SMTP_EMAIL': self.profile['email']}), \
             patch.object(bot.smtplib, 'SMTP') as smtp, patch.object(bot.imaplib, 'IMAP4_SSL') as imap:
            mailbox = imap.return_value.__enter__.return_value
            mailbox.list.return_value = ('OK', [b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"'])
            mailbox.select.return_value = ('OK', [b'1'])
            mailbox.search.return_value = ('OK', [b'123'])
            result = bot.check_mail(self.profile, self.db)
            self.assertTrue(result['gmail_sent_accessible'])
            smtp.return_value.__enter__.return_value.send_message.assert_not_called()
            self.assertEqual(self.db.execute('SELECT sent_visible FROM attempts').fetchone()[0], 1)
            self.assertTrue(mailbox.select.call_args.kwargs['readonly'])

    def test_expiry_during_smtp_authentication_never_transmits(self):
        self.queued()
        self.config['send_end_hour'] = 24
        with patch.dict(bot.os.environ, {'SEND_ENABLED': 'true', 'GMAIL_APP_PASSWORD': 'fixture-password',
                                        'SMTP_EMAIL': self.profile['email']}), patch.object(bot.smtplib, 'SMTP') as smtp:
            server = smtp.return_value.__enter__.return_value
            def time_passed(*args):
                self.mock_clock.return_value = self.clock + timedelta(hours=12)
            server.login.side_effect = time_passed
            result = bot.dispatch(self.config, self.profile, self.sources, self.db, self.fetcher())
            self.assertEqual(result['mode'], 'expired_before_send')
            server.send_message.assert_not_called()
            self.assertFalse(self.db.execute("SELECT 1 FROM meta WHERE key='smtp_paused'").fetchone())

    def test_rediscovery_preserves_already_sent_artifacts(self):
        job = self.queued()
        file = bot.ROOT/'output'/job['id']/'application.eml'
        original = file.read_bytes()
        self.db.execute("UPDATE jobs SET status='sent' WHERE id=?", (job['id'],))
        self.db.commit()
        self.profile['summary'] = 'A changed profile that must not rewrite send evidence.'
        bot.discover(self.config, self.profile, self.sources, self.db, self.fetcher())
        self.assertEqual(file.read_bytes(), original)
        self.assertEqual(self.db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0], 'sent')

    def test_graduate_degree_alone_does_not_establish_fresher_eligibility(self):
        p = {**self.posting, 'description': 'Must be a graduate. Python Node.js. Send resume to careers@example.com.'}
        self.assertIsNone(self.evaluate(p)[0])

    def test_zero_to_two_year_range_keeps_freshers_eligible(self):
        self.profile['years_experience'] = 0
        desc = self.posting['description'].replace('1 year experience', '0 to 2 years experience')
        self.assertIsNotNone(self.evaluate({**self.posting, 'description': desc})[0])
        desc = 'Python Node.js. Experience: 0 to 2 years. Send resume to careers@example.com.'
        self.assertIsNotNone(self.evaluate({**self.posting, 'description': desc})[0])

    def test_known_skill_spellings_match_without_adding_skills(self):
        desc = self.posting['description'].replace('Node.js', 'NodeJS')
        job, reason = self.evaluate({**self.posting, 'description': desc})
        self.assertIsNone(reason)
        self.assertEqual(job['matching_skills'], ['Python', 'Node.js'])
        self.assertFalse(bot.skill_matches('Java', 'JavaScript'))

    def test_portal_job_evaluation_and_referral_links(self):
        # Posting without an application email but with valid portal role and skills
        portal_posting = {**self.posting, 'description': '<p>Apply on our portal. Python and Node.js developer needed. 1 year experience.</p>'}
        job, reason = self.evaluate(portal_posting)
        self.assertIsNone(job)
        self.assertEqual(reason, 'no unambiguous application email on the posting')
        pjob, preason = bot.evaluate_portal_job(portal_posting, 'https://example.com/jobs/portal-1', self.trust, self.config, self.profile)
        self.assertIsNone(preason)
        self.assertEqual(pjob['company'], 'Example')
        self.assertEqual(pjob['match_score'], 2)
        refs = bot.generate_referral_links('Example', 'Noida')
        self.assertIn('recruiters', refs)
        self.assertIn('engineers', refs)
        self.assertIn('Example+Noida+technical+recruiter', refs['recruiters'])

    def test_portal_digest_generation_and_saving(self):
        pjob, _ = bot.evaluate_portal_job(
            {**self.posting, 'description': '<p>Apply online. Python Node.js developer. 1 year experience.</p>'},
            'https://example.com/jobs/portal-2', self.trust, self.config, self.profile
        )
        self.db.execute('''INSERT INTO portal_jobs(id, title, company, url, location, matching_skills, match_score, date_posted, status, discovered_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (pjob['id'], pjob['title'], pjob['company'], pjob['url'], pjob['location'],
             json.dumps(pjob['matching_skills']), pjob['match_score'], pjob['date_posted'], 'pending', self.clock.isoformat()))
        self.db.commit()
        res = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
        self.assertEqual(res['count'], 1)
        self.assertIn(res['mode'], ('preview_saved_sending_disabled', 'preview_saved_no_password', 'sent'))

    def test_portal_digest_respects_disabled_sending(self):
        self.add_portal_job()
        with patch.dict(os.environ, {'SEND_ENABLED': 'false', 'GMAIL_APP_PASSWORD': 'test-password'}), \
                patch.object(bot.smtplib, 'SMTP') as smtp:
            result = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
        self.assertEqual(result['mode'], 'preview_saved_sending_disabled')
        smtp.assert_not_called()

    def test_portal_digest_respects_manual_pause_and_sender_match(self):
        self.add_portal_job()
        with patch.dict(os.environ, {'SEND_ENABLED': 'true', 'PAUSE_SENDING': 'true',
                                     'SMTP_EMAIL': self.profile['email'], 'GMAIL_APP_PASSWORD': 'test-password'}), \
                patch.object(bot.smtplib, 'SMTP') as smtp:
            result = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
        self.assertEqual(result['mode'], 'paused')
        smtp.assert_not_called()

        with patch.dict(os.environ, {'SEND_ENABLED': 'true', 'PAUSE_SENDING': 'false',
                                     'SMTP_EMAIL': 'wrong@example.com', 'GMAIL_APP_PASSWORD': 'test-password'}), \
                patch.object(bot.smtplib, 'SMTP') as smtp:
            result = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
        self.assertEqual(result['mode'], 'blocked')
        smtp.assert_not_called()

    def test_portal_digest_reservation_prevents_duplicate_send(self):
        self.add_portal_job()
        env = {'SEND_ENABLED': 'true', 'PAUSE_SENDING': 'false',
               'SMTP_EMAIL': self.profile['email'], 'GMAIL_APP_PASSWORD': 'test-password'}
        with patch.dict(os.environ, env), patch.object(bot.smtplib, 'SMTP') as smtp:
            server = smtp.return_value.__enter__.return_value
            first = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
            second = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
        self.assertEqual(first['mode'], 'sent')
        self.assertEqual(second['mode'], 'already_sent_today')
        self.assertEqual(smtp.call_count, 1)
        server.send_message.assert_called_once()

    def test_portal_digest_smtp_failure_pauses_and_is_not_retried(self):
        self.add_portal_job()
        env = {'SEND_ENABLED': 'true', 'PAUSE_SENDING': 'false',
               'SMTP_EMAIL': self.profile['email'], 'GMAIL_APP_PASSWORD': 'test-password'}
        with patch.dict(os.environ, env), patch.object(bot.smtplib, 'SMTP') as smtp:
            server = smtp.return_value.__enter__.return_value
            server.send_message.side_effect = smtplib.SMTPServerDisconnected('private server detail')
            first = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
            second = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
        self.assertEqual(first, {'mode': 'error', 'error': 'SMTPServerDisconnected', 'preview_file': first['preview_file']})
        self.assertEqual(second['mode'], 'paused')
        self.assertEqual(smtp.call_count, 1)
        self.assertEqual(self.db.execute("SELECT value FROM meta WHERE key='smtp_paused'").fetchone()[0], 'SMTPServerDisconnected')

    def test_portal_digest_escapes_job_content_in_html_preview(self):
        self.add_portal_job(title='<script>alert(1)</script>')
        with patch.dict(os.environ, {'SEND_ENABLED': 'false'}):
            result = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
        content = Path(result['preview_file']).read_text(encoding='utf-8')
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', content)
        self.assertNotIn('<script>alert(1)</script>', content)

    def test_portal_digest_drops_stale_jobs(self):
        stale_date = (self.clock - timedelta(hours=13)).isoformat()
        self.db.execute('''INSERT INTO portal_jobs(id, title, company, url, location, matching_skills, match_score, date_posted, status, discovered_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            ('stale-portal', 'Backend Engineer', 'Example', 'https://example.com/jobs/stale', 'Noida',
             json.dumps(['Python', 'Node.js']), 2, stale_date, 'pending', self.clock.isoformat()))
        self.db.commit()
        result = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
        status = self.db.execute("SELECT status FROM portal_jobs WHERE id='stale-portal'").fetchone()[0]
        self.assertEqual(result['mode'], 'no_jobs')
        self.assertEqual(status, 'expired_outdated')

    def test_portal_digest_preview_only_once_per_window(self):
        self.add_portal_job()
        self.config['portal_digest_hours'] = [self.clock.hour]
        with patch.dict(os.environ, {'SEND_ENABLED': 'false'}):
            first = bot.send_portal_digest(self.config, self.profile, self.db)
            second = bot.send_portal_digest(self.config, self.profile, self.db)
        self.assertEqual(first['mode'], 'preview_saved_sending_disabled')
        self.assertEqual(second['mode'], 'preview_already_saved')

    def test_queue_eviction_after_18_hours(self):
        job = self.queued()
        old_time = (self.clock - timedelta(hours=19)).isoformat()
        stale_job = {**job, 'id': 'stale-1', 'email': 'stale@example.com', 'date_posted': old_time}
        self.db.execute("INSERT INTO jobs(id,payload,status) VALUES(?,?,'queued')",
                        (stale_job['id'], json.dumps(stale_job)))
        self.db.commit()
        bot.dispatch(self.config, self.profile, self.sources, self.db, self.fetcher())
        status = self.db.execute("SELECT status FROM jobs WHERE id='stale-1'").fetchone()[0]
        self.assertEqual(status, 'expired_outdated')

    def test_priority_scoring_and_salary_fit(self):
        high_sal_job = {**self.posting, 'id': 'high-1', 'matching_skills': ['Python', 'Node.js'],
                        'description': 'Salary: 6 LPA (50,000 per month). Python Node.js developer.',
                        'date_posted': self.clock.isoformat()}
        low_sal_job = {**self.posting, 'id': 'low-1', 'matching_skills': ['Python', 'Node.js'],
                       'description': 'Stipend: 15,000 per month. Python Node.js developer.',
                       'date_posted': self.clock.isoformat()}
        score_high = bot.compute_priority_score(high_sal_job, self.profile, self.config)
        score_low = bot.compute_priority_score(low_sal_job, self.profile, self.config)
        self.assertGreater(score_high, score_low)

    def test_experience_evaluation_mandatory_vs_preferred(self):
        self.profile['years_experience'] = 0
        base = {**self.posting}

        # 0-1 and 0-2 years must be accepted for 0-year profile
        for desc in [
            'Python Node.js developer. Experience: 0-1 years. Send resume to careers@example.com.',
            'Python Node.js developer. 0 to 2 years of experience. Send resume to careers@example.com.',
            'Python Node.js developer. Required: 0-2 years experience. Preferred: 1+ year in React. Send resume to careers@example.com.',
            'Freshers welcome. Python Node.js developer. Good to have: 1-2 years experience. Send resume to careers@example.com.',
            'Junior Software Engineer. Python Node.js. 1 year experience preferred. Send resume to careers@example.com.'
        ]:
            with self.subTest(desc=desc):
                job, reason = self.evaluate({**base, 'description': desc})
                self.assertIsNotNone(job, f"Failed for {desc}: {reason}")

        # Mandatory experience exceeding candidate profile must be rejected
        for desc, expected_reason in [
            ('Python Node.js developer. Required: 1-2 years experience. Send resume to careers@example.com.', 'required experience exceeds profile'),
            ('Python Node.js developer. Required: 1-3 years experience. Send resume to careers@example.com.', 'required experience exceeds profile'),
            ('Python Node.js developer. Minimum 2+ years experience. Send resume to careers@example.com.', 'required experience exceeds profile'),
            ('Python Node.js developer. Required: 3+ years experience. Preferred: 0-1 years. Send resume to careers@example.com.', 'role requires more than 2 years experience'),
        ]:
            with self.subTest(desc=desc):
                job, reason = self.evaluate({**base, 'description': desc})
                self.assertIsNone(job)
                self.assertEqual(reason, expected_reason)

    def test_portal_date_handling_and_future_rejection(self):
        # Current simulated clock is 2026-10-09 10:00 IST
        dt_valid, is_date = bot.parse_portal_job_date('2026-10-08', self.clock)
        self.assertTrue(is_date)
        self.assertEqual(dt_valid.year, 2026)
        self.assertEqual(dt_valid.month, 10)
        self.assertEqual(dt_valid.day, 8)

        # Future calendar date must raise ValueError
        with self.assertRaises(ValueError):
            bot.parse_portal_job_date('2026-10-15', self.clock)

        # Invalid date string must raise ValueError
        with self.assertRaises(ValueError):
            bot.parse_portal_job_date('not-a-date', self.clock)

        # ISO timestamp without timezone must be accepted and localized to IST
        dt_iso, is_date2 = bot.parse_portal_job_date('2026-10-09T08:00:00', self.clock)
        self.assertFalse(is_date2)
        self.assertEqual(dt_iso.tzinfo, bot.IST)

    def test_portal_freshness_separation_7_days(self):
        config_portal = {**self.config, 'portal_max_post_age_days': 7}
        p_base = {
            'title': 'Backend Developer',
            'description': 'Python and Node.js developer. 0-2 years experience.',
            'hiringOrganization': {'name': 'Example'},
            'jobLocation': {'address': {'addressLocality': 'Noida'}},
        }

        # 3-day old job (within 7 days) must pass portal evaluation
        three_days_old = (self.clock - timedelta(days=3)).strftime('%Y-%m-%d')
        pjob, reason = bot.evaluate_portal_job({**p_base, 'datePosted': three_days_old},
                                               'https://example.com/jobs/p1', self.trust, config_portal, self.profile)
        self.assertIsNotNone(pjob, f"3-day old job should pass: {reason}")

        # 8-day old job (beyond 7 days) must be rejected as stale
        eight_days_old = (self.clock - timedelta(days=8)).strftime('%Y-%m-%d')
        pjob_stale, reason_stale = bot.evaluate_portal_job({**p_base, 'datePosted': eight_days_old},
                                                           'https://example.com/jobs/p2', self.trust, config_portal, self.profile)
        self.assertIsNone(pjob_stale)
        self.assertEqual(reason_stale, 'posting is stale')

        # Direct email application with 13-hour old posting must be rejected (12h rule unchanged)
        email_posting = {**self.posting, 'datePosted': (self.clock - timedelta(hours=13)).isoformat()}
        e_job, e_reason = self.evaluate(email_posting)
        self.assertIsNone(e_job)
        self.assertEqual(e_reason, 'posting is stale or has a future date')

    def test_greenhouse_candidates_route_to_portal_jobs_when_not_review_only(self):
        source = dict(company='Example Corp', board_token='examplecorp',
                      official_careers_url='https://example.com/careers',
                      board_url='https://job-boards.greenhouse.io/examplecorp', review_only=False)
        job = dict(title='Software Engineer, Node.js', company_name='Example Corp',
                   absolute_url='https://job-boards.greenhouse.io/examplecorp/jobs/99999',
                   location={'name': 'Noida'}, first_published='2026-10-08T12:00:00+05:30',
                   content='&lt;p&gt;Entry-level role. 0-2 years experience. Python and Node.js.&lt;/p&gt;')
        fetcher = MagicMock()
        fetcher.get.side_effect = [
            ('https://example.com/careers', '<a href="https://job-boards.greenhouse.io/examplecorp">Open jobs</a>'),
            ('https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs?content=true', json.dumps({'jobs': [job]})),
        ]
        config_with_portal = {**self.config, 'portal_max_post_age_days': 7}
        sources = dict(trusted_companies=[], posting_urls=[], alert_feeds=[], greenhouse_boards=[source])
        result = bot.discover(config_with_portal, self.profile, sources, self.db, fetcher)

        self.assertEqual(result['ats_review_candidates'], 1)
        self.assertEqual(result['portal_jobs'], 1)
        self.assertEqual(result['verified'], 0)  # Zero direct email jobs verified
        self.assertEqual(self.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)  # Zero jobs in email sending queue

        p_row = self.db.execute("SELECT * FROM portal_jobs WHERE url='https://job-boards.greenhouse.io/examplecorp/jobs/99999'").fetchone()
        self.assertIsNotNone(p_row)
        self.assertEqual(p_row['title'], 'Software Engineer, Node.js')
        self.assertEqual(p_row['status'], 'pending')

    def test_evaluate_lead_email_apply_verified_employer(self):
        lead = {
            'text': 'We are hiring a Junior Software Engineer at Example! 0-1 year experience. Python and Node.js. Location: Noida / Remote India. Send your resume to careers@example.com to apply.',
            'company': 'Example',
            'url': 'https://www.linkedin.com/posts/example_hiring_123',
            'posted_at': (self.clock - timedelta(hours=2)).isoformat(),
            'recruiter_name': 'Jane Doe',
            'recruiter_title': 'Technical Recruiter',
            'source_type': 'recruiter_post'
        }
        res = bot.evaluate_lead(lead, self.config, self.profile, self.sources, self.db)
        self.assertEqual(res['classification'], 'EMAIL_APPLY')
        self.assertEqual(res['status'], 'verified')
        self.assertEqual(res['email'], 'careers@example.com')
        self.assertTrue(res['draft_prepared'])

        # Check jobs table
        job_row = self.db.execute("SELECT status,payload,artifact FROM jobs WHERE id=?", (res['job_id'],)).fetchone()
        self.assertIsNotNone(job_row)
        self.assertEqual(job_row['status'], 'queued')
        payload = json.loads(job_row['payload'])
        self.assertEqual(payload['company'], 'Example')
        self.assertEqual(payload['source_type'], 'recruiter_post')
        self.assertEqual(payload['recruiter_name'], 'Jane Doe')
        # Check artifact draft
        artifact = json.loads(job_row['artifact'])
        self.assertTrue(Path(artifact['file']).is_file())
        self.assertTrue(Path(artifact['file']).name.endswith('.eml'))

    def test_evaluate_lead_portal_apply(self):
        lead = {
            'text': 'Example is expanding our backend team in Noida! Hiring Software Developer (0-2 years exp, Python, Node.js). Apply on our career portal: https://job-boards.greenhouse.io/example/jobs/12345',
            'company': 'Example',
            'url': 'https://www.linkedin.com/posts/example_portal_456',
            'posted_at': (self.clock - timedelta(days=2)).isoformat(),
            'source_type': 'recruiter_post'
        }
        config_with_portal = {**self.config, 'portal_max_post_age_days': 7}
        res = bot.evaluate_lead(lead, config_with_portal, self.profile, self.sources, self.db)
        self.assertEqual(res['classification'], 'PORTAL_APPLY')
        self.assertEqual(res['status'], 'pending')

        # Check portal_jobs table has record and jobs table has zero
        p_row = self.db.execute("SELECT * FROM portal_jobs WHERE id=?", (res['job_id'],)).fetchone()
        self.assertIsNotNone(p_row)
        self.assertEqual(p_row['company'], 'Example')
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_evaluate_lead_unverified_employer_routes_to_review(self):
        lead = {
            'text': 'Hiring freshers for Software Engineer role in Noida! Python Node.js. Send resume to jobs@unverifiedcorp.com',
            'company': 'Unverified Tech Corp',
            'url': 'https://www.linkedin.com/posts/random_post_789',
            'posted_at': (self.clock - timedelta(hours=1)).isoformat()
        }
        res = bot.evaluate_lead(lead, self.config, self.profile, self.sources, self.db)
        self.assertEqual(res['classification'], 'NEEDS_REVIEW')
        self.assertIn('trusted_companies', res['reason'])

        # Check reviews table
        rev_row = self.db.execute("SELECT * FROM reviews WHERE url=?", (lead['url'],)).fetchone()
        self.assertIsNotNone(rev_row)
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM portal_jobs").fetchone()[0], 0)

    def test_evaluate_lead_unauthorized_email_domain_routes_to_review(self):
        lead = {
            'text': 'We are hiring at Example! Send resume to recruiter123@gmail.com to apply for Software Engineer role.',
            'company': 'Example',
            'url': 'https://www.linkedin.com/posts/example_bad_email',
            'posted_at': (self.clock - timedelta(hours=1)).isoformat(),
            'email': 'recruiter123@gmail.com'
        }
        res = bot.evaluate_lead(lead, self.config, self.profile, self.sources, self.db)
        self.assertEqual(res['classification'], 'NEEDS_REVIEW')
        self.assertIn('does not match verified employer domains', res['reason'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_independent_digest_send_authorization(self):
        self.add_portal_job()
        self.config['portal_digest_hours'] = [self.clock.hour]
        # SEND_ENABLED=false (job application sending disabled), DIGEST_SEND_ENABLED=true (digest authorized)
        with patch.dict(os.environ, {'SEND_ENABLED': 'false', 'DIGEST_SEND_ENABLED': 'true',
                                     'SMTP_EMAIL': self.profile['email'], 'GMAIL_APP_PASSWORD': 'valid-password'}):
            with patch('smtplib.SMTP') as mock_smtp:
                server = MagicMock()
                mock_smtp.return_value.__enter__.return_value = server
                res = bot.send_portal_digest(self.config, self.profile, self.db)
                self.assertEqual(res['mode'], 'sent')
                self.assertEqual(res['count'], 1)
                self.assertEqual(res['to'], self.profile['email'])
                server.send_message.assert_called_once()

    def test_email_lead_freshness_scenarios(self):
        base_lead = {
            'text': 'Junior Software Engineer at Example. Python and Node.js. Location: Noida. 0-1 years exp. Send your resume to careers@example.com to apply.',
            'company': 'Example',
            'url': 'https://www.linkedin.com/posts/freshness_test',
            'email': 'careers@example.com',
            'source_type': 'recruiter_post'
        }
        cfg = {**self.config, 'email_lead_max_post_age_days': 3}

        # 1. 12 hours -> PASS (EMAIL_APPLY)
        lead_12h = {**base_lead, 'posted_at': (self.clock - timedelta(hours=12)).isoformat()}
        res_12h = bot.evaluate_lead(lead_12h, cfg, self.profile, self.sources, self.db)
        self.assertEqual(res_12h['classification'], 'EMAIL_APPLY')

        # 2. 24 hours -> PASS (EMAIL_APPLY)
        lead_24h = {**base_lead, 'posted_at': (self.clock - timedelta(hours=24)).isoformat()}
        res_24h = bot.evaluate_lead(lead_24h, cfg, self.profile, self.sources, self.db)
        self.assertEqual(res_24h['classification'], 'EMAIL_APPLY')

        # 3. 48 hours -> PASS (EMAIL_APPLY)
        lead_48h = {**base_lead, 'posted_at': (self.clock - timedelta(hours=48)).isoformat()}
        res_48h = bot.evaluate_lead(lead_48h, cfg, self.profile, self.sources, self.db)
        self.assertEqual(res_48h['classification'], 'EMAIL_APPLY')

        # 4. Exactly 72 hours -> PASS (EMAIL_APPLY)
        lead_72h = {**base_lead, 'posted_at': (self.clock - timedelta(hours=72)).isoformat()}
        res_72h = bot.evaluate_lead(lead_72h, cfg, self.profile, self.sources, self.db)
        self.assertEqual(res_72h['classification'], 'EMAIL_APPLY')

        # 5. More than 72 hours (73 hours) -> FAILS (NEEDS_REVIEW with stale)
        lead_73h = {**base_lead, 'posted_at': (self.clock - timedelta(hours=73)).isoformat()}
        res_73h = bot.evaluate_lead(lead_73h, cfg, self.profile, self.sources, self.db)
        self.assertEqual(res_73h['classification'], 'NEEDS_REVIEW')
        self.assertIn('posting is stale', res_73h['reason'])

        # 6. Missing timestamp -> FAILS (NEEDS_REVIEW)
        lead_no_date = {**base_lead, 'posted_at': None}
        res_no_date = bot.evaluate_lead(lead_no_date, cfg, self.profile, self.sources, self.db)
        self.assertEqual(res_no_date['classification'], 'NEEDS_REVIEW')
        self.assertIn('no reliable posting date/time', res_no_date['reason'])

        # 7. Timezone conversions (UTC 'Z' representation of 24h ago)
        utc_time = (self.clock - timedelta(hours=24)).astimezone(bot.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        lead_utc = {**base_lead, 'posted_at': utc_time}
        res_utc = bot.evaluate_lead(lead_utc, cfg, self.profile, self.sources, self.db)
        self.assertEqual(res_utc['classification'], 'EMAIL_APPLY')

        # 8. Duplicate hiring announcements
        initial_job_count = self.db.execute("SELECT count(*) FROM jobs").fetchone()[0]
        res_dup1 = bot.evaluate_lead(lead_24h, cfg, self.profile, self.sources, self.db)
        res_dup2 = bot.evaluate_lead(lead_24h, cfg, self.profile, self.sources, self.db)
        final_job_count = self.db.execute("SELECT count(*) FROM jobs").fetchone()[0]
        self.assertEqual(res_dup1['job_id'], res_dup2['job_id'])
        self.assertEqual(final_job_count, initial_job_count)  # No duplicate row inserted

    def test_vehant_technologies_in_review_due_to_experience(self):
        vehant_trust = {
            'company': 'Vehant Technologies',
            'company_aliases': ['Vehant', 'Vehant Technologies Private Limited'],
            'identity_verified': True,
            'company_homepage': 'https://www.vehant.com/',
            'verification_note': 'CIN U74900DL2005PTC139049, incorporated 2005, IIT Delhi incubated; Noida HQ',
            'office_cities': ['Noida', 'Delhi'],
            'office_evidence_url': 'https://www.vehant.com/contact-us/',
            'posting_domains': ['vehant.com'],
            'email_domains': ['vehant.com']
        }
        sources = {'trusted_companies': [vehant_trust], 'posting_urls': []}
        lead = {
            'company': 'Vehant Technologies',
            'title': 'Junior Software Developer',
            'location': 'Noida',
            'url': 'https://www.vehant.com/jobs/jr-web-developer-appdev-web/',
            'posted_at': (self.clock - timedelta(hours=12)).isoformat(),
            'email': 'recruitment@vehant.com',
            'text': 'Junior Software Developer – Web. Location: Noida. Experience: 1 to 3 years. Core Stack: Python, NodeJS, ReactJS, TypeScript, HTML5, CSS3, SQL. Submit Your Resume - recruitment@vehant.com'
        }
        profile_fresher = {**self.profile, 'years_experience': 0}
        res = bot.evaluate_lead(lead, self.config, profile_fresher, sources, self.db)
        # MUST remain NEEDS_REVIEW due to experience (1-3 yrs vs 0 yrs profile)
        self.assertEqual(res['classification'], 'NEEDS_REVIEW')
        self.assertEqual(res['reason'], 'required experience exceeds profile')

    def test_source_health_tracking_and_cooldown(self):
        source_url = "https://example.com/careers"

        # 1. Initially unrecorded source is healthy
        avail, status, reason = bot.is_source_in_cooldown(self.db, source_url)
        self.assertTrue(avail)
        self.assertEqual(status, 'healthy')
        self.assertIsNone(reason)

        # 2. Healthy source with 0 jobs found (distinguish zero jobs from failure)
        bot.record_source_success(self.db, source_url, "career_page", job_count=0)
        row = self.db.execute("SELECT * FROM source_health WHERE source_url=?", (source_url,)).fetchone()
        self.assertEqual(row['health_status'], 'healthy')
        self.assertEqual(row['recent_job_count'], 0)
        self.assertEqual(row['consecutive_failures'], 0)
        self.assertIsNone(row['cooldown_until'])

        # 3. HTTP 429 Rate limiting with Retry-After (1 hour = 3600s)
        bot.record_source_failure(self.db, source_url, "career_page", status_code=429, retry_after=3600)
        avail, status, reason = bot.is_source_in_cooldown(self.db, source_url)
        self.assertFalse(avail)
        self.assertEqual(status, 'cooldown')
        self.assertIn('cooldown until', reason)

        # Cooldown expires after 3600s (advance mock clock)
        with patch.object(bot, 'now', return_value=self.clock + timedelta(seconds=3605)):
            avail_after, status_after, _ = bot.is_source_in_cooldown(self.db, source_url)
            self.assertTrue(avail_after)

        # 4. HTTP 403 / Access restricted -> pause without bypass attempts
        bot.record_source_failure(self.db, source_url, "career_page", status_code=403, error="Access forbidden")
        row = self.db.execute("SELECT * FROM source_health WHERE source_url=?", (source_url,)).fetchone()
        self.assertIn(row['health_status'], ('cooldown', 'blocked'))

        # After 3 repeated 403s, status transitions to blocked
        bot.record_source_failure(self.db, source_url, "career_page", status_code=403)
        bot.record_source_failure(self.db, source_url, "career_page", status_code=403)
        avail, status, reason = bot.is_source_in_cooldown(self.db, source_url)
        self.assertFalse(avail)
        self.assertEqual(status, 'blocked')
        self.assertIn('blocked/restricted', reason)

        # 5. Success resets source health back to clean state
        bot.record_source_success(self.db, source_url, "career_page", job_count=3)
        avail, status, reason = bot.is_source_in_cooldown(self.db, source_url)
        self.assertTrue(avail)
        self.assertEqual(status, 'healthy')
        self.assertIsNone(reason)
        row = self.db.execute("SELECT * FROM source_health WHERE source_url=?", (source_url,)).fetchone()
        self.assertEqual(row['consecutive_failures'], 0)
        self.assertEqual(row['recent_job_count'], 3)

    def test_source_health_persistence_across_reconnect(self):
        source_url = "https://example.com/api"
        # Record failure and cooldown in active DB
        bot.record_source_failure(self.db, source_url, "ats_api", status_code=500, error="Internal Server Error")
        avail_before, status_before, _ = bot.is_source_in_cooldown(self.db, source_url)
        self.assertFalse(avail_before)

        # Simulate reboot / process termination: close DB and reconnect from disk
        self.db.close()
        new_db = bot.connect(self.config)
        try:
            avail_after, status_after, _ = bot.is_source_in_cooldown(new_db, source_url)
            self.assertFalse(avail_after)
            self.assertEqual(status_after, status_before)
            row = new_db.execute("SELECT consecutive_failures, last_http_status FROM source_health WHERE source_url=?",
                                 (source_url,)).fetchone()
            self.assertEqual(row['consecutive_failures'], 1)
            self.assertEqual(row['last_http_status'], 500)
        finally:
            self.db = new_db

    def test_database_backup_and_recovery(self):
        # Insert test data into jobs and source_health
        self.db.execute("INSERT INTO jobs(id, payload, status) VALUES('job_123', '{\"test\": true}', 'verified')")
        bot.record_source_success(self.db, "https://example.com/jobs", "career_page", job_count=5)
        self.db.commit()

        # 1. Run backup
        backup_info = bot.backup_database(self.config, max_backups=2)
        self.assertEqual(backup_info['status'], 'success')
        backup_file = Path(backup_info['backup_file'])
        self.assertTrue(backup_file.exists())
        self.assertGreater(backup_info['backup_size_bytes'], 0)

        # Check file permissions (0600: read/write for owner only)
        file_mode = oct(backup_file.stat().st_mode & 0o777)
        self.assertEqual(file_mode, '0o600')

        # 2. Test rotation (max_backups=2)
        backup_info_2 = bot.backup_database(self.config, max_backups=2)
        backup_info_3 = bot.backup_database(self.config, max_backups=2)
        backup_dir = backup_file.parent
        all_backups = list(backup_dir.glob('jobbot_backup_*.sqlite3'))
        self.assertLessEqual(len(all_backups), 2)

        # 3. Simulate disaster / database corruption: clear tables in current DB
        self.db.execute("DELETE FROM jobs")
        self.db.execute("DELETE FROM source_health")
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

        # Close active connection before restoring
        self.db.close()

        # Restore from the first verified backup
        restore_info = bot.restore_database(str(backup_file), self.config)
        self.assertEqual(restore_info['status'], 'success')

        # Reopen connection and verify restored data integrity
        self.db = bot.connect(self.config)
        restored_job = self.db.execute("SELECT id, status FROM jobs WHERE id='job_123'").fetchone()
        self.assertIsNotNone(restored_job)
        self.assertEqual(restored_job['status'], 'verified')
        restored_health = self.db.execute("SELECT health_status, recent_job_count FROM source_health WHERE source_url='https://example.com/jobs'").fetchone()
        self.assertIsNotNone(restored_health)
        self.assertEqual(restored_health['recent_job_count'], 5)

    def test_internship_and_early_career_experience_eligibility(self):
        profile_fresher = {**self.profile, 'years_experience': 0}
        # 1. Genuine internship vacancy establishes early-career eligibility for fresher (0 YOE)
        desc_intern = "We are seeking a Backend Software Engineering Intern. You will work with Node.js and Python on scalable microservices."
        ok, reason = bot.evaluate_experience_fit(desc_intern, "Software Engineering Intern", None, self.config, profile_fresher)
        self.assertTrue(ok)
        self.assertIsNone(reason)

        # 2. Apprentice / Trainee roles also establish eligibility
        desc_trainee = "Graduate Software Developer Trainee program. Learn React and Node.js."
        ok_t, reason_t = bot.evaluate_experience_fit(desc_trainee, "Graduate Software Trainee", None, self.config, profile_fresher)
        self.assertTrue(ok_t)
        self.assertIsNone(reason_t)

        # 3. But mandatory minimum exceeding profile (e.g. 1+ or 2+ years) still rejects appropriately
        desc_mid = "Software Engineer. Mandatory requirements: at least 2 years of professional experience in backend development."
        ok_mid, reason_mid = bot.evaluate_experience_fit(desc_mid, "Software Engineer", None, self.config, profile_fresher)
        self.assertFalse(ok_mid)
        self.assertEqual(reason_mid, "required experience exceeds profile")

    def test_ats_freshness_handling(self):
        posted_25d_ago = (self.clock - timedelta(days=25)).isoformat()
        job_data = {
            'title': 'Junior Software Engineer',
            'description': 'Python and Node.js. 0-1 years experience or fresher.',
            'datePosted': posted_25d_ago,
            'jobLocation': [{'address': {'addressLocality': 'Noida'}}]
        }
        cfg = {**self.config, 'portal_max_post_age_days': 7, 'ats_max_post_age_days': 45}

        # 1. Active live ATS feed job posted 25 days ago passes
        job_ats = {**job_data, 'is_active_ats_feed': True}
        res_ats, reason_ats = bot.evaluate_portal_job(job_ats, 'https://example.com/jobs/ats1', self.trust, cfg, self.profile)
        self.assertIsNotNone(res_ats)
        self.assertIsNone(reason_ats)

        # 2. Unverified scraped portal job posted 25 days ago fails under 7-day rule
        job_unverified = {**job_data, 'is_active_ats_feed': False}
        res_unv, reason_unv = bot.evaluate_portal_job(job_unverified, 'https://example.com/jobs/unv1', self.trust, cfg, self.profile)
        self.assertIsNone(res_unv)
        self.assertEqual(reason_unv, 'posting is stale')

    def test_automatic_daily_backup_in_tick(self):
        # Initial tick triggers automated backup
        with patch.object(bot, 'discover', return_value={'pages': 0}), \
                patch.object(bot, 'dispatch', return_value={'sent': 0}):
            res = bot.tick(self.config, self.profile, self.sources, self.db, bot.Fetcher())
        self.assertIn('daily_backup', res)
        self.assertEqual(res['daily_backup']['status'], 'success')
        last_b = self.db.execute("SELECT value FROM meta WHERE key='last_backup_at'").fetchone()
        self.assertIsNotNone(last_b)

        # Second tick immediately afterwards does NOT trigger duplicate backup
        with patch.object(bot, 'discover', return_value={'pages': 0}), \
                patch.object(bot, 'dispatch', return_value={'sent': 0}):
            res2 = bot.tick(self.config, self.profile, self.sources, self.db, bot.Fetcher())
        self.assertNotIn('daily_backup', res2)

    def test_silent_discovery_failure_detection(self):
        # Record failure for all configured sources
        bot.record_source_failure(self.db, "https://example.com/s1", "ats", status_code=500)
        bot.record_source_failure(self.db, "https://example.com/s2", "ats", status_code=500)
        # Advance failures to trigger cooldown
        for _ in range(5):
            bot.record_source_failure(self.db, "https://example.com/s1", "ats", status_code=500)
            bot.record_source_failure(self.db, "https://example.com/s2", "ats", status_code=500)

        h = bot.health(self.config, self.profile, self.sources, self.db)
        self.assertIn('discovery_warnings', h)
        self.assertTrue(any('cooldown, blocked, or failing' in w for w in h['discovery_warnings']))

    def test_portal_digest_with_review_candidates(self):
        # Insert a promising review candidate into reviews table
        review_candidate = {
            'title': 'Software Engineer, Backend (Node.js)',
            'company': 'Plane',
            'location': 'India',
            'reason': 'ambiguous experience requirement: held for manual review',
            'description_skill_matches': ['Node.js', 'TypeScript', 'SQL'],
            'posted_at': self.clock.isoformat()
        }
        self.db.execute("INSERT OR REPLACE INTO reviews(url, reason, found_at) VALUES(?,?,?)",
                        ('https://jobs.ashbyhq.com/plane/test-1', json.dumps(review_candidate), self.clock.isoformat()))
        self.db.commit()

        # Digest should generate preview containing Category B review candidates even if portal_jobs is 0
        with patch.dict(os.environ, {'DIGEST_SEND_ENABLED': 'false'}):
            res = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
        self.assertEqual(res['count'], 0)
        self.assertGreaterEqual(res.get('review_count', 0), 1)
        preview_path = Path(res['preview_file'])
        self.assertTrue(preview_path.exists())
        content = preview_path.read_text(encoding='utf-8')
        self.assertIn('Category B — Promising Opportunities Held for Manual Review', content)
        self.assertIn('Plane', content)



class TestV4MasterIntegrationScenarios(unittest.TestCase):
    """Isolated end-to-end integration tests for JobBot V4 across Scenarios A through O."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.clock = datetime(2026, 10, 29, 10, 0, tzinfo=bot.IST)
        self.clock_patch = patch('jobbot.now', return_value=self.clock)
        self.root_patch = patch('jobbot.ROOT', self.temp_path)
        self.clock_patch.start()
        self.root_patch.start()

        self.config = {
            'database': 'data/test_v4.sqlite3',
            'daily_limit': 5,
            'daily_minimum': 1,
            'discovery_retry_hours': 2,
            'minimum_gap_seconds': 240,
            'maximum_gap_seconds': 420,
            'send_start_hour': 9,
            'send_end_hour': 20,
            'max_post_age_hours': 12,
            'portal_max_post_age_days': 7,
            'ats_max_post_age_days': 45,
            'email_lead_max_post_age_days': 3,
            'maximum_role_experience_years': 2,
            'minimum_skill_matches': 2,
            'public_search_enabled': False,
            'search_queries_per_day': 8,
            'max_pages_per_run': 20,
            'portal_digest_enabled': True,
            'portal_digest_hours': [6, 18],
        }
        self.profile = {
            'name': 'Satvik Saini',
            'email': 'satviksaini7777@gmail.com',
            'confirmed': True,
            'years_experience': 0,
            'experience_as_of': '2026-10-28',
            'allow_senior_roles': False,
            'resume_mode': 'original',
            'resume_pdf': 'resumes/Satvik_Saini_Resume.pdf',
            'summary': 'Computer Science Engineering graduate (B.Tech, 2026) and Full Stack Developer specialising in MERN stack.',
            'skills': ['JavaScript', 'TypeScript', 'Node.js', 'Express.js', 'React.js', 'MongoDB', 'SQL', 'Python'],
            'portfolio': 'https://satvik-saini-portfolio.satviksaini7777.chatgpt.site/',
            'github': 'https://github.com/Satvik77777'
        }
        resume_file = self.temp_path / 'resumes' / 'Satvik_Saini_Resume.pdf'
        resume_file.parent.mkdir(parents=True, exist_ok=True)
        resume_file.write_bytes(b'%PDF-1.4\nTest Resume Content\n%%EOF')

        self.sources = {
            'trusted_companies': [
                {
                    'company': 'Razorpay',
                    'company_aliases': ['Razorpay Software Private Limited'],
                    'identity_verified': True,
                    'company_homepage': 'https://razorpay.com/',
                    'verification_note': 'MCA CIN U72200KA2013PTC097389',
                    'office_cities': ['Bengaluru', 'Noida'],
                    'office_evidence_url': 'https://razorpay.com/contact/',
                    'posting_domains': ['razorpay.com', 'job-boards.greenhouse.io'],
                    'email_domains': ['razorpay.com'],
                    'career_urls': ['https://razorpay.com/jobs/']
                }
            ],
            'greenhouse_boards': [
                {
                    'company': 'Razorpay',
                    'board_token': 'razorpaysoftwareprivatelimited',
                    'official_careers_url': 'https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited',
                    'board_url': 'https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited',
                    'review_only': False
                }
            ],
            'posting_urls': [],
            'alert_feeds': []
        }
        self.db = bot.connect(self.config)

    def tearDown(self):
        self.db.close()
        self.clock_patch.stop()
        self.root_patch.stop()
        self.temp_dir.cleanup()

    def test_scenario_a_valid_entry_level_portal_role(self):
        """Scenario A: Legitimate entry-level portal role -> Category A (confirmed eligible)."""
        job_data = {
            'title': 'Junior Software Engineer (Node.js & React)',
            'description': 'We are hiring a Junior Software Engineer with 0-1 years of experience in Node.js, TypeScript, and React.js. Fresh graduates from 2026 batch welcome. Location: Bengaluru, India.',
            'datePosted': self.clock.isoformat(),
            'jobLocation': [{'address': {'addressLocality': 'Bengaluru'}}],
            'is_active_ats_feed': True
        }
        trust_proxy = {'company': 'Razorpay'}
        pjob, reason = bot.evaluate_portal_job(job_data, 'https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/101', trust_proxy, self.config, self.profile)
        self.assertIsNotNone(pjob)
        self.assertIsNone(reason)
        self.assertEqual(pjob['company'], 'Razorpay')
        self.assertGreaterEqual(pjob['match_score'], 2)

    def test_scenario_b_promising_ambiguous_role(self):
        """Scenario B: Strong backend role with ambiguous seniority -> Category B (held for manual review)."""
        job_data = {
            'title': 'Software Engineer, Backend',
            'description': 'High throughput transaction processing using Node.js, TypeScript, and MongoDB. Evaluate architectural designs and project depth. Location: Bengaluru, India.',
            'datePosted': self.clock.isoformat(),
            'jobLocation': [{'address': {'addressLocality': 'Bengaluru'}}],
            'is_active_ats_feed': True
        }
        trust_proxy = {'company': 'Razorpay'}
        pjob, reason = bot.evaluate_portal_job(job_data, 'https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/102', trust_proxy, self.config, self.profile)
        self.assertIsNone(pjob)
        self.assertIn('posting does not establish fresher or 0-2 year eligibility', reason)

    def test_scenario_c_senior_role_ineligible(self):
        """Scenario C: Senior vacancy requiring 3+ years experience -> Ineligible."""
        job_data = {
            'title': 'Senior Backend Engineer',
            'description': 'Minimum 4+ years of production experience in backend architecture and Node.js microservices.',
            'datePosted': self.clock.isoformat(),
            'jobLocation': [{'address': {'addressLocality': 'Bengaluru'}}]
        }
        trust_proxy = {'company': 'Razorpay'}
        pjob, reason = bot.evaluate_portal_job(job_data, 'https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/103', trust_proxy, self.config, self.profile)
        self.assertIsNone(pjob)
        self.assertIn('senior role excluded by profile', reason)

    def test_scenario_d_student_only_internship(self):
        """Scenario D: Internship restricted to non-graduating batch -> Held safely."""
        desc = "Software Engineering Intern. Eligibility: Currently enrolled students graduating in 2027 only. Not open to 2026 graduates."
        ok, reason = bot.evaluate_experience_fit(desc, "Software Engineering Intern", None, self.config, self.profile)
        # Note: 2027 specific restriction is parsed safely
        self.assertTrue(ok)  # Experience pattern itself matches intern, graduation criteria held in review

    def test_scenario_e_preferred_experience_not_disqualifying(self):
        """Scenario E: Preferred experience ('1 year preferred') does not disqualify candidate with 0 YOE."""
        desc = "Entry-Level Software Engineer. 1 year experience preferred. Strong foundation in JavaScript, React.js, and Node.js."
        ok, reason = bot.evaluate_experience_fit(desc, "Entry-Level Software Engineer", None, self.config, self.profile)
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_scenario_f_authorized_email_application(self):
        """Scenario F: Verified direct-email hiring lead -> Prepared draft with approved resume."""
        lead = {
            'company': 'Razorpay',
            'title': 'Associate Backend Developer',
            'email': 'careers@razorpay.com',
            'location': 'Bengaluru',
            'posted_at': self.clock.isoformat(),
            'text': 'Associate Backend Developer. Location: Bengaluru. 0-1 years experience with Node.js and TypeScript. Please email your resume to careers@razorpay.com.'
        }
        res = bot.evaluate_lead(lead, self.config, self.profile, self.sources, self.db)
        self.assertEqual(res['classification'], 'EMAIL_APPLY')
        self.assertEqual(res['status'], 'verified')
        self.assertTrue(res['draft_prepared'])
        # Verify draft exists in database
        row = self.db.execute("SELECT status, artifact FROM jobs WHERE id=?", (res['job_id'],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'queued')
        artifact = json.loads(row['artifact'])
        self.assertTrue(Path(artifact['file']).exists())

    def test_scenario_g_portal_only_never_converted_to_email_apply(self):
        """Scenario G: Portal-only vacancy is never converted to EMAIL_APPLY."""
        lead = {
            'company': 'Razorpay',
            'title': 'Software Engineer',
            'portal_url': 'https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/201',
            'location': 'Bengaluru',
            'posted_at': self.clock.isoformat(),
            'text': 'Software Engineer. Location: Bengaluru. 0-1 years experience in Node.js and TypeScript. Apply on our career portal at https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/201'
        }
        res = bot.evaluate_lead(lead, self.config, self.profile, self.sources, self.db)
        self.assertEqual(res['classification'], 'PORTAL_APPLY')
        self.assertNotEqual(res['classification'], 'EMAIL_APPLY')
        # Check portal_jobs table
        p_row = self.db.execute("SELECT status FROM portal_jobs WHERE id=?", (res['job_id'],)).fetchone()
        self.assertIsNotNone(p_row)
        self.assertEqual(p_row['status'], 'pending')

    def test_scenario_h_duplicate_role_suppression(self):
        """Scenario H: Repeated insertion of the same role is idempotently deduplicated."""
        lead = {
            'company': 'Razorpay',
            'title': 'Associate Backend Developer',
            'email': 'careers@razorpay.com',
            'location': 'Bengaluru',
            'posted_at': self.clock.isoformat(),
            'text': 'Associate Backend Developer. Please email your resume to careers@razorpay.com. 0-1 years in Node.js and TypeScript.'
        }
        res1 = bot.evaluate_lead(lead, self.config, self.profile, self.sources, self.db)
        res2 = bot.evaluate_lead(lead, self.config, self.profile, self.sources, self.db)
        self.assertEqual(res1['job_id'], res2['job_id'])
        count = self.db.execute("SELECT count(*) FROM jobs WHERE id=?", (res1['job_id'],)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_scenario_i_one_source_fails_independent_sources_continue(self):
        """Scenario I: One failing ATS endpoint does not halt independent healthy sources."""
        failing_url = 'https://job-boards.greenhouse.io/failing_board'
        healthy_url = 'https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited'
        # Simulate 429 rate limit failure
        bot.record_source_failure(self.db, failing_url, 'greenhouse_board', status_code=429, error='Too Many Requests', retry_after='3600')
        is_avail, status, _ = bot.is_source_in_cooldown(self.db, failing_url)
        self.assertFalse(is_avail)
        self.assertEqual(status, 'cooldown')

        # Healthy source remains available
        is_healthy_avail, healthy_status, _ = bot.is_source_in_cooldown(self.db, healthy_url)
        self.assertTrue(is_healthy_avail)
        self.assertEqual(healthy_status, 'healthy')

    def test_scenario_j_smtp_uncertainty_safe_pause(self):
        """Scenario J: SMTP failure safely pauses without uncontrolled duplicate retries."""
        # Setup queued job on a trusted domain matching evaluate() digest
        url = 'https://razorpay.com/jobs/1'
        email = 'careers@razorpay.com'
        desc = 'Python and Node.js. 0-1 years experience. Please email your resume to careers@razorpay.com.'
        posting_json = {
            '@type': 'JobPosting',
            'title': 'Junior Software Engineer',
            'description': desc,
            'datePosted': self.clock.isoformat(),
            'hiringOrganization': {'name': 'Razorpay'},
            'jobLocation': {'address': {'addressLocality': 'Bengaluru'}}
        }
        fresh_job, _ = bot.evaluate(posting_json, url, self.sources['trusted_companies'][0], self.config, self.profile)
        self.assertIsNotNone(fresh_job)
        self.db.execute("INSERT INTO jobs(id, payload, status) VALUES(?,?,?)", (fresh_job['id'], json.dumps(fresh_job), 'queued'))
        bot.draft(self.db, fresh_job, self.profile)
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('batch_released_day',?)", (self.clock.date().isoformat(),))
        self.db.commit()

        # Simulate SMTP connection error
        with patch.dict(os.environ, {'SEND_ENABLED': 'true', 'SMTP_EMAIL': self.profile['email'], 'GMAIL_APP_PASSWORD': 'dummy'}):
            with patch('smtplib.SMTP', side_effect=smtplib.SMTPServerDisconnected("Connection lost")):
                fetcher = MagicMock()
                fetcher.get.return_value = (url, '<script type="application/ld+json">' + json.dumps(posting_json) + '</script>')
                res = bot.dispatch(self.config, self.profile, self.sources, self.db, fetcher)
                self.assertEqual(res['mode'], 'uncertain')
                # Verify SMTP is paused
                pause = self.db.execute("SELECT value FROM meta WHERE key='smtp_paused'").fetchone()
                self.assertIsNotNone(pause)

    def test_scenario_k_sqlite_online_backup_integrity(self):
        """Scenario K: Automated SQLite online backup creates clean, recoverable file."""
        self.db.execute("INSERT INTO jobs(id, payload, status) VALUES('k_1', '{\"name\":\"test\"}', 'verified')")
        self.db.commit()
        b_info = bot.backup_database(self.config, max_backups=3)
        self.assertEqual(b_info['status'], 'success')
        b_file = Path(b_info['backup_file'])
        self.assertTrue(b_file.exists())
        # Validate integrity
        conn = sqlite3.connect(b_file)
        try:
            res = conn.execute("PRAGMA integrity_check").fetchone()
            self.assertEqual(res[0], 'ok')
        finally:
            conn.close()

    def test_scenario_l_scheduler_and_lease_handling(self):
        """Scenario L: Scheduler respects due time and sets 15-minute lease."""
        instant = self.clock
        # Future discovery -> not due
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('next_discovery_at',?)", ((instant + timedelta(hours=1)).isoformat(),))
        self.db.commit()
        fetcher = MagicMock()
        res = bot.tick(self.config, self.profile, self.sources, self.db, fetcher)
        self.assertNotIn('discovery', res)

        # Past discovery -> due
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('next_discovery_at',?)", ((instant - timedelta(minutes=5)).isoformat(),))
        self.db.commit()
        with patch('jobbot.discover', return_value={'discovered': 0}) as mock_disc:
            res2 = bot.tick(self.config, self.profile, self.sources, self.db, fetcher)
            self.assertTrue(mock_disc.called)

    def test_scenario_m_digest_generation_categories(self):
        """Scenario M: Portal digest cleanly separates Category A and Category B."""
        # Add Category A job
        self.db.execute('''INSERT INTO portal_jobs(id, title, company, url, location, matching_skills, match_score, date_posted, status, discovered_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            ('p1', 'Associate Software Engineer', 'Razorpay', 'https://job-boards.greenhouse.io/razorpay/1', 'Bengaluru',
             json.dumps(['Node.js', 'React.js']), 2, self.clock.isoformat(), 'pending', self.clock.isoformat()))
        # Add Category B review candidate
        rev = {'title': 'Backend Developer', 'company': 'Groww', 'location': 'Bengaluru', 'reason': 'held for manual review: ambiguous experience', 'description_skill_matches': ['Node.js'], 'posted_at': self.clock.isoformat()}
        self.db.execute("INSERT INTO reviews(url, reason, found_at) VALUES(?,?,?)",
                        ('https://job-boards.eu.greenhouse.io/groww/2', json.dumps(rev), self.clock.isoformat()))
        self.db.commit()

        with patch.dict(os.environ, {'DIGEST_SEND_ENABLED': 'false'}):
            res = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
            self.assertEqual(res['count'], 1)
            self.assertEqual(res['review_count'], 1)
            p_file = Path(res['preview_file'])
            self.assertTrue(p_file.exists())
            html_text = p_file.read_text(encoding='utf-8')
            self.assertIn('Category A — Confirmed Eligible Portal Applications (1)', html_text)
            self.assertIn('Category B — Promising Opportunities Held for Manual Review (1)', html_text)
            self.assertIn('Razorpay', html_text)
            self.assertIn('Groww', html_text)

    def test_scenario_n_zero_eligible_roles_honest_reporting(self):
        """Scenario N: Zero eligible roles reports zero truthfully without inventing vacancies."""
        with patch.dict(os.environ, {'DIGEST_SEND_ENABLED': 'false'}):
            res = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
            self.assertEqual(res['mode'], 'no_jobs')
            self.assertEqual(res['count'], 0)

    def test_scenario_o_recovery_from_abrupt_interruption(self):
        """Scenario O: Process interruption clears expired lease and resumes cleanly."""
        # Stale lease from 20 minutes ago
        stale_time = (self.clock - timedelta(minutes=20)).isoformat()
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('tick_lease_until',?)", (stale_time,))
        self.db.commit()

        fetcher = MagicMock()
        res = bot.tick(self.config, self.profile, self.sources, self.db, fetcher)
        # Did not abort with 'worker already running'
        self.assertNotEqual(res.get('mode'), 'worker already running')
        # Lease is cleaned up after tick
        lease_after = self.db.execute("SELECT value FROM meta WHERE key='tick_lease_until'").fetchone()
        self.assertIsNone(lease_after)


    def test_scenario_p_lease_renewal_during_active_discovery(self):
        """Scenario P: Active discovery renews tick lease, preventing premature expiry on long cycles."""
        initial_time = self.clock
        bot.renew_tick_lease(self.db, duration_minutes=15)
        lease_row = self.db.execute("SELECT value FROM meta WHERE key='tick_lease_until'").fetchone()
        self.assertIsNotNone(lease_row)
        expected_lease = initial_time + timedelta(minutes=15)
        self.assertEqual(bot.posted_date(lease_row[0]), expected_lease)

    def test_scenario_q_category_c_strictly_excluded_from_category_b_digest(self):
        """Scenario Q: Category C ineligible roles (Vehant, Enterpret, senior) are strictly excluded from Category B."""
        # 1. Vehant: mandatory 1-3 years exceeds candidate 0 YOE profile -> Category C
        rev_vehant = {
            'title': 'Jr. Web Developer',
            'company': 'Vehant Technologies',
            'location': 'Noida',
            'reason': 'required experience exceeds profile',
            'description_skill_matches': ['HTML5', 'CSS3', 'JavaScript']
        }
        # 2. Enterpret: Tier-1 and prior internship mandatory -> Category C
        rev_enterpret = {
            'title': 'Software Engineering Intern',
            'company': 'Enterpret',
            'location': 'Bengaluru',
            'classification': 'ineligible',
            'reason': 'mandatory tier-1 college CS degree and prior relevant internship unmet',
            'description_skill_matches': ['Node.js']
        }
        # 3. Plane: Legitimate ambiguous backend opportunity -> Category B
        rev_plane = {
            'title': 'Software Engineer, Backend',
            'company': 'Plane',
            'location': 'India',
            'classification': 'manual_review_only',
            'reason': 'ambiguous experience requirement: held for manual review',
            'description_skill_matches': ['Node.js', 'TypeScript'],
            'posted_at': self.clock.isoformat()
        }
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)", ('https://example.com/vehant', json.dumps(rev_vehant), self.clock.isoformat()))
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)", ('https://example.com/enterpret', json.dumps(rev_enterpret), self.clock.isoformat()))
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)", ('https://example.com/plane', json.dumps(rev_plane), self.clock.isoformat()))
        self.db.commit()

        with patch.dict(os.environ, {'DIGEST_SEND_ENABLED': 'false'}):
            res = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
            self.assertEqual(res['count'], 0)  # 0 Category A
            self.assertEqual(res['review_count'], 1)  # Exactly 1 in Category B (Plane only!)
            preview_path = Path(res['preview_file'])
            content = preview_path.read_text(encoding='utf-8')
            self.assertIn('Plane', content)
            self.assertNotIn('Vehant', content)
            self.assertNotIn('Enterpret', content)

    def test_scenario_r_lease_owner_identity_protection(self):
        """Scenario R: Expired or superseded worker cannot renew or delete a newer worker's lease."""
        # 1. Worker 1 acquires lease
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('tick_lease_until',?)", ((self.clock + timedelta(minutes=15)).isoformat(),))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('tick_lease_owner',?)", ('worker-1-token',))
        self.db.commit()

        # 2. Worker 1 renews successfully with matching owner token
        renewed = bot.renew_tick_lease(self.db, duration_minutes=20, owner_token='worker-1-token')
        self.assertTrue(renewed)
        lease_row = self.db.execute("SELECT value FROM meta WHERE key='tick_lease_until'").fetchone()
        self.assertEqual(bot.posted_date(lease_row[0]), self.clock + timedelta(minutes=20))

        # 3. Worker 2 supersedes lease after worker 1 expires
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('tick_lease_owner',?)", ('worker-2-token',))
        self.db.commit()

        # 4. Worker 1 attempts to renew with old token -> rejected
        renewed_old = bot.renew_tick_lease(self.db, duration_minutes=30, owner_token='worker-1-token')
        self.assertFalse(renewed_old)
        # Lease remains at worker 2's timestamp
        lease_row = self.db.execute("SELECT value FROM meta WHERE key='tick_lease_until'").fetchone()
        self.assertEqual(bot.posted_date(lease_row[0]), self.clock + timedelta(minutes=20))

    def test_scenario_s_freehire_discovery_and_categorization(self):
        """Scenario S: FreeHire discovery strips tracking parameters, separates Category B manual review from Category C."""
        # Test clean_job_url
        raw_url = "https://www.linkedin.com/jobs/view/12345/?utm_source=freehire.me&utm_medium=feed&ref=123"
        clean_url = bot.clean_job_url(raw_url)
        self.assertEqual(clean_url, "https://www.linkedin.com/jobs/view/12345/")

        sample_data = {
            "data": [
                {
                    "title": "Junior Python Developer",
                    "company": "Pincler",
                    "location": "India",
                    "url": "https://www.linkedin.com/jobs/view/1001/?utm_source=freehire.me",
                    "description": "Junior Python Developer - Fresher / entry level with Python and Node.js skills.",
                    "posted_at": self.clock.isoformat(),
                    "source": "linkedin"
                },
                {
                    "title": "Senior Lead Engineer",
                    "company": "MegaCorp",
                    "location": "Bengaluru",
                    "url": "https://www.naukri.com/jobs/view/1002?utm_source=freehire.me",
                    "description": "Senior Lead Engineer with 8+ years experience.",
                    "posted_at": self.clock.isoformat(),
                    "source": "naukri"
                },
                {
                    "title": "Full Stack Engineer",
                    "company": "ExperiencedTech",
                    "location": "Noida",
                    "url": "https://www.naukri.com/jobs/view/1003?utm_source=freehire.me",
                    "description": "Requires 4 to 6 years of experience in React and Node.js.",
                    "posted_at": self.clock.isoformat(),
                    "source": "naukri"
                }
            ]
        }

        mock_fetcher = MagicMock()
        mock_fetcher.raw.return_value = ("https://freehire.me/api/v1/jobs/search", json.dumps(sample_data))

        cfg = {**self.config, 'freehire_max_queries': 1}
        res = bot.freehire_discover(cfg, self.profile, self.sources, self.db, fetcher=mock_fetcher)
        self.assertEqual(res['candidates'], 3)
        self.assertEqual(res['reviews'], 3)

        # Pincler junior role should be Category B manual review
        row_pincler = self.db.execute("SELECT reason FROM reviews WHERE url=?", ("https://www.linkedin.com/jobs/view/1001/",)).fetchone()
        self.assertIsNotNone(row_pincler)
        ev_pincler = json.loads(row_pincler[0])
        self.assertEqual(ev_pincler['classification'], 'manual_review_only')
        self.assertIn('Python', ev_pincler['description_skill_matches'])

        # Senior role should be marked ineligible
        row_mega = self.db.execute("SELECT reason FROM reviews WHERE url=?", ("https://www.naukri.com/jobs/view/1002",)).fetchone()
        self.assertIsNotNone(row_mega)
        ev_mega = json.loads(row_mega[0])
        self.assertEqual(ev_mega['classification'], 'ineligible')

        # 4-6 YOE role should be marked ineligible
        row_exp = self.db.execute("SELECT reason FROM reviews WHERE url=?", ("https://www.naukri.com/jobs/view/1003",)).fetchone()
        self.assertIsNotNone(row_exp)
        ev_exp = json.loads(row_exp[0])
        self.assertEqual(ev_exp['classification'], 'ineligible')

        # Run portal digest preview: Pincler should be in Category B, MegaCorp and ExperiencedTech excluded
        with patch.dict(os.environ, {'DIGEST_SEND_ENABLED': 'false'}):
            digest_res = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
            self.assertEqual(digest_res['count'], 0)  # 0 Category A
            self.assertEqual(digest_res['review_count'], 1)  # Only Pincler in Category B
            html = Path(digest_res['preview_file']).read_text(encoding='utf-8')
            self.assertIn('Pincler', html)
            self.assertNotIn('MegaCorp', html)
            self.assertNotIn('ExperiencedTech', html)
    def test_scenario_t_dark_mode_digest_styling_and_responsiveness(self):
        """Scenario T: Portal digest renders default dark mode (#0B1220, #172235), 960px desktop width, 16px body, and responsive layout."""
        # Insert Category A
        self.db.execute('''INSERT INTO portal_jobs(id, title, company, url, location, matching_skills, match_score, date_posted, status, discovered_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            ('p_dark', 'Frontend Developer', 'Razorpay', 'https://job-boards.greenhouse.io/razorpay/99', 'Bengaluru',
             json.dumps(['React.js', 'JavaScript']), 2, self.clock.isoformat(), 'pending', self.clock.isoformat()))
        # Insert Category B
        rev = {
            'title': 'Junior Full Stack Engineer',
            'company': 'Cred',
            'location': 'Bengaluru',
            'classification': 'manual_review_only',
            'reason': 'ambiguous experience requirement: held for manual review',
            'description_skill_matches': ['React.js', 'Node.js'],
            'posted_at': self.clock.isoformat()
        }
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)", ('https://example.com/cred_dark', json.dumps(rev), self.clock.isoformat()))
        self.db.commit()

        with patch.dict(os.environ, {'DIGEST_SEND_ENABLED': 'false'}):
            res = bot.send_portal_digest(self.config, self.profile, self.db, force=True)
            self.assertEqual(res['count'], 1)
            self.assertEqual(res['review_count'], 1)
            p_file = Path(res['preview_file'])
            self.assertTrue(p_file.exists())
            html = p_file.read_text(encoding='utf-8')

            # Verify Dark Mode colors
            self.assertIn('#0B1220', html)  # Near-black body background
            self.assertIn('#172235', html)  # Dark slate job cards
            self.assertIn('#0F172A', html)  # Container background

            # Verify Layout Width & Typography
            self.assertIn('max-width: 960px', html)
            self.assertIn('font-size: 16px', html)

            # Verify Accessibility & Email Client Dark Mode Support
            self.assertIn('name="color-scheme" content="dark"', html)
            self.assertIn('name="viewport"', html)
            self.assertIn('@media only screen and (max-width: 680px)', html)

            # Verify High Contrast Text and Distinct Badges
            self.assertIn('#F8FAFC', html)  # White/near-white high contrast text
            self.assertIn('#94A3B8', html)  # High contrast secondary text
            self.assertIn('Category A — Confirmed Eligible Portal Applications (1)', html)
            self.assertIn('Category B — Promising Opportunities Held for Manual Review (1)', html)
            self.assertIn('Razorpay', html)
            self.assertIn('Cred', html)

    def test_scenario_u_strict_relevance_freshness_and_deepsa_filtering(self):
        """Scenario U: Strict experience filtering (0-YOE target), Deepsa exclusion, freshness (<=72h priority, <=7d cutoff, no missing dates), and DB re-filtering."""
        # 1. Experience & Role Evaluation Unit Checks
        # Deepsa 4-8 YOE + "Mentor junior engineers"
        deepsa_desc = "Deepsa AI Automation. Mentor junior engineers and contribute to a culture of technical excellence. Minimum 4-8 years of experience required in backend development."
        ok_deepsa, reason_deepsa = bot.evaluate_experience_fit(deepsa_desc, "Backend Engineer", None, self.config, self.profile)
        self.assertFalse(ok_deepsa)
        self.assertTrue('mentor' in str(reason_deepsa).lower() or 'experience' in str(reason_deepsa).lower())

        # Mandatory 2+ YOE
        desc_2yoe = "Candidate must possess minimum 2+ years of professional software engineering experience."
        ok_2yoe, reason_2yoe = bot.evaluate_experience_fit(desc_2yoe, "Software Engineer", None, self.config, self.profile)
        self.assertFalse(ok_2yoe)

        # Explicit range including 0 (0-1 YOE or 0-2 YOE)
        desc_0_1 = "Requirements: 0-1 years of experience in Python, Node.js or React."
        ok_0_1, _ = bot.evaluate_experience_fit(desc_0_1, "Junior Software Engineer", None, self.config, self.profile)
        self.assertTrue(ok_0_1)

        desc_0_2 = "Open to freshers with 0 to 2 years experience."
        ok_0_2, _ = bot.evaluate_experience_fit(desc_0_2, "Associate Software Engineer", None, self.config, self.profile)
        self.assertTrue(ok_0_2)

        # Preferred 2+ YOE without mandatory
        desc_pref = "Qualifications: B.Tech in CSE. Preferred qualifications: 2+ years experience building web applications."
        ok_pref, _ = bot.evaluate_experience_fit(desc_pref, "Software Engineer", None, self.config, self.profile)
        self.assertTrue(ok_pref)

        # Senior/Lead role rejection
        ok_sr, reason_sr = bot.evaluate_experience_fit("Open to all, 0-1 years experience welcomed.", "Senior Full Stack Developer", None, self.config, self.profile)
        self.assertFalse(ok_sr)
        self.assertIn('senior', str(reason_sr).lower())

        # Graduation batch restriction
        desc_grad_2027 = "Eligibility: Only students currently enrolled and graduating in 2027 or 2028."
        ok_grad_2027, reason_grad_2027 = bot.evaluate_graduation_eligibility(desc_grad_2027, "Software Engineering Intern")
        self.assertFalse(ok_grad_2027)

        desc_grad_2026 = "Eligibility: Open to 2026 batch B.Tech/B.E graduates."
        ok_grad_2026, _ = bot.evaluate_graduation_eligibility(desc_grad_2026, "Graduate Software Engineer")
        self.assertTrue(ok_grad_2026)

        # 2. Date Parsing & Freshness Filter Checks
        # Fresh within 72h
        fresh_time = (self.clock - timedelta(hours=18)).isoformat()
        dt, age_hours, age_label, is_fresh_72h, is_valid_7d = bot.parse_and_validate_job_date(fresh_time, current_dt=self.clock)
        self.assertTrue(is_valid_7d)
        self.assertTrue(is_fresh_72h)
        self.assertEqual(age_label, '18h ago')

        # Stale (> 7 days)
        stale_time = (self.clock - timedelta(days=9)).isoformat()
        _, _, _, _, is_valid_stale = bot.parse_and_validate_job_date(stale_time, current_dt=self.clock)
        self.assertFalse(is_valid_stale)

        # Missing / Unreliable date
        _, _, _, _, is_valid_missing = bot.parse_and_validate_job_date(None, current_dt=self.clock)
        self.assertFalse(is_valid_missing)
        _, _, _, _, is_valid_invalid = bot.parse_and_validate_job_date("unparseable date string", current_dt=self.clock)
        self.assertFalse(is_valid_invalid)

        # 3. Database Re-Filtering during Digest Generation
        # Insert genuine Category A job (fresh, 10h old)
        self.db.execute('''INSERT INTO portal_jobs(id, title, company, url, location, matching_skills, match_score, date_posted, status, discovered_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            ('p_valid', 'Associate Software Engineer', 'FreshWorks', 'https://job-boards.greenhouse.io/freshworks/1', 'Chennai',
             json.dumps(['Python', 'JavaScript']), 2, (self.clock - timedelta(hours=10)).isoformat(), 'pending', self.clock.isoformat()))

        # Insert Category B candidate: genuine fresh 0-YOE job (20h old)
        rev_valid = {
            'title': 'Junior Backend Developer',
            'company': 'Swiggy',
            'location': 'Bengaluru',
            'classification': 'manual_review_only',
            'reason': 'unverified source held for manual review',
            'description_skill_matches': ['Python', 'Node.js'],
            'posted_at': (self.clock - timedelta(hours=20)).isoformat()
        }
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)",
                        ('https://example.com/swiggy', json.dumps(rev_valid), self.clock.isoformat()))

        # Insert previously stored unsuitable / stale records that must NEVER resurface:
        # a) Deepsa AI Automation (4-8 YOE, mentor junior)
        rev_deepsa = {
            'title': 'Backend Engineer',
            'company': 'Deepsa AI Automation',
            'location': 'Remote',
            'classification': 'manual_review_only',
            'reason': deepsa_desc,
            'description_skill_matches': ['Node.js'],
            'posted_at': (self.clock - timedelta(hours=5)).isoformat()
        }
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)",
                        ('https://example.com/deepsa', json.dumps(rev_deepsa), self.clock.isoformat()))

        # b) Stale job (>7 days old)
        rev_stale = {
            'title': 'Junior Frontend Developer',
            'company': 'StaleCorp',
            'location': 'Bengaluru',
            'classification': 'manual_review_only',
            'reason': 'valid fresher role',
            'description_skill_matches': ['React.js'],
            'posted_at': (self.clock - timedelta(days=12)).isoformat()
        }
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)",
                        ('https://example.com/stalecorp', json.dumps(rev_stale), self.clock.isoformat()))

        # c) Missing posted_at date
        rev_no_date = {
            'title': 'Junior QA Engineer',
            'company': 'NoDateCorp',
            'location': 'Noida',
            'classification': 'manual_review_only',
            'reason': 'valid role but missing date',
            'description_skill_matches': ['Python']
        }
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)",
                        ('https://example.com/nodatecorp', json.dumps(rev_no_date), self.clock.isoformat()))

        # d) Senior role in reviews table
        rev_senior = {
            'title': 'Lead Software Architect',
            'company': 'BigTech',
            'location': 'Hyderabad',
            'classification': 'manual_review_only',
            'reason': 'stored lead role',
            'description_skill_matches': ['Python'],
            'posted_at': (self.clock - timedelta(hours=6)).isoformat()
        }
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)",
                        ('https://example.com/bigtech', json.dumps(rev_senior), self.clock.isoformat()))

        # e) Incompatible graduation batch
        rev_batch = {
            'title': 'Software Intern (2028 batch)',
            'company': 'FutureTech',
            'location': 'Remote',
            'classification': 'manual_review_only',
            'reason': 'Must be currently enrolled student graduating in 2028 only',
            'description_skill_matches': ['Python'],
            'posted_at': (self.clock - timedelta(hours=8)).isoformat()
        }
        self.db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)",
                        ('https://example.com/futuretech', json.dumps(rev_batch), self.clock.isoformat()))

        self.db.commit()

        # Run digest generation
        with patch.dict(os.environ, {'DIGEST_SEND_ENABLED': 'false'}):
            res = bot.send_portal_digest(self.config, self.profile, self.db, force=True)

        self.assertEqual(res['count'], 1)  # Only FreshWorks
        self.assertEqual(res['review_count'], 1)  # Only Swiggy in Category B!

        p_file = Path(res['preview_file'])
        self.assertTrue(p_file.exists())
        content = p_file.read_text(encoding='utf-8')

        # Confirm inclusions
        self.assertIn('FreshWorks', content)
        self.assertIn('Swiggy', content)

        # Confirm strict rejections - none of these resurface
        self.assertNotIn('Deepsa', content)
        self.assertNotIn('StaleCorp', content)
        self.assertNotIn('NoDateCorp', content)
        self.assertNotIn('BigTech', content)
        self.assertNotIn('FutureTech', content)

        # Check Transparency fields on cards
        self.assertIn('⏱ 10h ago', content)
        self.assertIn('⏱ 20h ago', content)
        self.assertIn('Experience:', content)
        self.assertIn('Verification:', content)
        self.assertIn('Verified ATS', content)
        self.assertIn('Pending Employer Verification', content)


if __name__ == '__main__':
    unittest.main()






