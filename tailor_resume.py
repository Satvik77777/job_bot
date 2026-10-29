"""Optional local PDF tailoring. Reorders supplied facts; never invents claims.

Requires reportlab. Core jobbot does not require this package.
"""
import re
from html import escape
from pathlib import Path


def score(text, description, skills):
    return sum(1 for skill in skills if re.search(r'(?<!\w)' + re.escape(skill) + r'(?!\w)', text, re.I)
               and re.search(r'(?<!\w)' + re.escape(skill) + r'(?!\w)', description, re.I))


def ordered(items, description, skills):
    return sorted(items, key=lambda x: -score(str(x), description, skills))


def generate(profile, description, destination):
    if not profile.get('confirmed'):
        raise ValueError('Confirm resume facts before PDF generation')
    if not profile.get('education') or not (profile.get('experience') or profile.get('projects')):
        raise ValueError('Tailored resume requires education and experience or projects from your own resume')
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether
    except ImportError as e:
        raise ValueError('Install optional reportlab, or use resume_mode=original') from e
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='NameLine', fontName='Helvetica-Bold', fontSize=17,
                              leading=20, alignment=TA_CENTER, spaceAfter=4))
    styles.add(ParagraphStyle(name='ContactLine', fontName='Helvetica', fontSize=8,
                              leading=10, alignment=TA_CENTER, spaceAfter=3))
    styles.add(ParagraphStyle(name='SectionLine', fontName='Helvetica-Bold', fontSize=10,
                              leading=12, spaceBefore=7, spaceAfter=3, textColor=colors.HexColor('#16354b')))
    styles.add(ParagraphStyle(name='ResumeBody', fontName='Helvetica', fontSize=9,
                              leading=11, spaceAfter=3))
    styles.add(ParagraphStyle(name='ResumeBullet', parent=styles['ResumeBody'], leftIndent=12, firstLineIndent=-8))
    def text(value):
        return escape(str(value)).replace('\n', '<br/>')
    story = [Paragraph(text(profile['name']), styles['NameLine'])]
    contact = [profile['email']] + ([profile['phone']] if profile.get('phone') else [])
    story.append(Paragraph(text(' | '.join(contact)), styles['ContactLine']))
    for link in [profile['portfolio'], profile['github']]:
        story.append(Paragraph(text(link), styles['ContactLine']))
    story.append(Paragraph('PROFILE', styles['SectionLine']))
    story.append(Paragraph(text(profile['summary']), styles['ResumeBody']))
    skills = profile['skills']
    story.append(Paragraph('SKILLS', styles['SectionLine']))
    story.append(Paragraph(text(', '.join(ordered(skills, description, skills))), styles['ResumeBody']))
    for heading, key in [('EXPERIENCE', 'experience'), ('PROJECTS', 'projects'), ('EDUCATION', 'education')]:
        entries = profile.get(key, [])
        if not entries:
            continue
        story.append(Paragraph(heading, styles['SectionLine']))
        if key == 'projects':
            entries = ordered(entries, description, skills)
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get('heading'):
                raise ValueError(f'{key} entries must contain heading, detail, and optional bullets')
            head = [Paragraph('<b>' + text(entry['heading']) + '</b>', styles['ResumeBody'])]
            if entry.get('detail'):
                head.append(Paragraph(text(entry['detail']), styles['ResumeBody']))
            story.append(KeepTogether(head))
            for bullet in ordered(entry.get('bullets', []), description, skills):
                story.append(Paragraph('- ' + text(bullet), styles['ResumeBullet']))
            story.append(Spacer(1, 3))
    if profile.get('achievements'):
        story.append(Paragraph('ACHIEVEMENTS AND OPEN SOURCE', styles['SectionLine']))
        for entry in profile['achievements']:
            story.append(Paragraph('- ' + text(entry), styles['ResumeBullet']))
    if profile.get('certifications'):
        story.append(Paragraph('CERTIFICATIONS', styles['SectionLine']))
        for entry in profile['certifications']:
            story.append(Paragraph(text(entry), styles['ResumeBody']))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(destination), pagesize=A4, topMargin=24, bottomMargin=24,
                      leftMargin=34, rightMargin=34, title=f"{profile['name']} - Resume",
                      author=profile['name']).build(story)
