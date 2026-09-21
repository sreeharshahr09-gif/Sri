#!/usr/bin/env python3
"""Manual weekly Tire Technology Watcher compiler for classic Outlook on Windows.

Run without arguments for a small GUI. This program only reads source messages
and saves/displays drafts. It has no email transmission or scheduling function.
Python 3.10+. See SETUP.md for installation and the offline sample preview.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.policy import SMTP
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit
import webbrowser

from bs4 import BeautifulSoup, Comment

IST = timezone(timedelta(hours=5, minutes=30), 'IST')
UTC = timezone.utc
BASE = Path(__file__).resolve().parent
DEFAULTS = {
    'mailbox': 'shreeharsha.r@apollotyres.com',
    'subject_prefix': 'Tire Technology Watcher',
    'source_folders': ['@inbox'],
    'include_subfolders': True,
    'source_sender': 'sreeharsha.hr09@gmail.com',
    'to': [],
    'cc': [],
    'signature': '',
}
MARKER = 'TireWatcherWeek'
EMAIL_RE = re.compile(r'^[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+$')


class WatcherError(RuntimeError):
    pass


def squish(value):
    return re.sub(r'\s+', ' ', str(value)).strip()


def read_config(path):
    cfg = dict(DEFAULTS)
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding='utf-8-sig')))
    return cfg


def addresses(value):
    values = re.split(r'[,;\n]+', value) if isinstance(value, str) else value
    result = []
    for raw in values:
        addr = raw.strip()
        if not addr:
            continue
        if not EMAIL_RE.fullmatch(addr):
            raise WatcherError(f'Enter complete email addresses, separated by semicolons: {addr}')
        if addr.lower() not in [a.lower() for a in result]:
            result.append(addr)
    return result


def validate_config(cfg):
    if not EMAIL_RE.fullmatch(cfg['mailbox'].strip()):
        raise WatcherError('Enter your complete official Outlook email address.')
    cfg['mailbox'] = cfg['mailbox'].strip()
    cfg['subject_prefix'] = cfg['subject_prefix'].strip()
    if not cfg['subject_prefix']:
        raise WatcherError('The daily email subject prefix cannot be blank.')
    if cfg.get('source_sender'):
        addresses([cfg['source_sender']])
    cfg['to'], cfg['cc'] = addresses(cfg['to']), addresses(cfg['cc'])
    if not isinstance(cfg['source_folders'], list) or not cfg['source_folders']:
        raise WatcherError('Choose at least one source folder.')
    return cfg


def previous_week(now=None):
    now = (now or datetime.now(IST)).astimezone(IST)
    return now.date() - timedelta(days=now.weekday() + 7)


def week_bounds(start):
    if isinstance(start, str):
        try:
            start = date.fromisoformat(start)
        except ValueError as exc:
            raise WatcherError('Use YYYY-MM-DD for the Monday date.') from exc
    if start.weekday() != 0:
        raise WatcherError('Choose a Monday as the start of the reporting week.')
    first = datetime.combine(start, time.min, tzinfo=IST)
    return first, first + timedelta(days=7)


def received_datetime(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    # Outlook ReceivedTime is local time when its COM result has no tzinfo.
    # astimezone handles that using the Windows timezone, not an assumed IST.
    return value.astimezone(UTC)


def subject_matches(subject, prefix):
    subject, prefix = squish(subject).casefold(), squish(prefix).casefold()
    if not subject.startswith(prefix):
        return False
    suffix = subject[len(prefix):]
    return not suffix or suffix[0].isspace() or suffix[0] in '-–—:|('


def safe_url(url):
    url = str(url).strip()
    if any(ord(c) < 32 for c in url):
        return ''
    p = urlsplit(url)
    if p.scheme.lower() in ('https', 'http') and p.hostname:
        return url
    if p.scheme.lower() == 'mailto' and p.path:
        return url
    return ''


def comparison_url(url):
    """Decode Safe Links only for duplicate comparison; keep protection in drafts."""
    p = urlsplit(url)
    if (p.hostname or '').lower().endswith('.safelinks.protection.outlook.com'):
        return parse_qs(p.query).get('url', [url])[0]
    return url


@dataclass
class SourceMail:
    uid: str
    subject: str
    received: datetime
    sender: str
    html: str
    attachment_count: int = 0


def plain_to_html(body):
    def linkify(text):
        parts = re.split(r'(https?://[^\s<>]+)', text)
        return ''.join(f'<a href="{escape(v, quote=True)}">{escape(v)}</a>'
                       if i % 2 else escape(v) for i, v in enumerate(parts))
    return '<div>' + ''.join('<p>' + linkify(p).replace('\n', '<br>') + '</p>'
                            for p in re.split(r'\n\s*\n', body.replace('\r\n', '\n')) if p.strip()) + '</div>'


def sanitize(body):
    """Preserve readable email content and links; discard active markup and images."""
    soup = BeautifulSoup(body, 'html.parser')
    for node in list(soup.find_all(string=lambda s: isinstance(s, Comment))):
        node.extract()
    for node in list(soup.find_all(['script', 'style', 'head', 'iframe', 'object', 'embed',
                                  'svg', 'math', 'form', 'input', 'button', 'img', 'link', 'meta'])):
        if node.parent is not None:
            node.decompose()
    allowed = {'div', 'p', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'sub', 'sup',
               'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'thead',
               'tbody', 'tr', 'td', 'th', 'a', 'blockquote', 'pre', 'code', 'hr'}
    for node in list(soup.find_all(True)):
        if node.name not in allowed:
            node.unwrap()
            continue
        attrs = {}
        if node.name == 'a' and safe_url(node.get('href', '')):
            attrs['href'] = safe_url(node['href'])
        for name in ('colspan', 'rowspan') if node.name in ('td', 'th') else ():
            if str(node.get(name, '')).isdigit():
                attrs[name] = node[name]
        node.attrs = attrs
    return soup


def headings_and_format(soup):
    """Recognize both standard headings and the flattened headings in the sample."""
    headings = []
    for node in list(soup.find_all(string=True)):
        text = squish(node)
        if not text:
            continue
        if node.parent.name in ('div', '[document]', 'body'):
            section = ''
            if text.startswith('Top developments '):
                section = 'Top developments'
                text = text[len('Top developments '):]
            if text.startswith('Research watch '):
                section = 'Research watch'
                text = text[len('Research watch '):]
            is_item = bool(re.match(r'^\d+[.)]\s+\S', text)) or bool(
                re.search(r'\bRelevance\s+\d+(?:\.\d+)?/10', text, re.I))
            if is_item:
                if section:
                    section_tag = soup.new_tag('h2')
                    section_tag.string = section
                    node.insert_before(section_tag)
                h = soup.new_tag('h3')
                h.string = text
                node.replace_with(h)
            elif text in ('Developing themes', 'Priority actions for the team', 'Research watch', 'Top developments'):
                h = soup.new_tag('h3')
                h.string = text
                node.replace_with(h)
    for h in soup.find_all(['h1', 'h2', 'h3', 'h4']):
        text = squish(h.get_text(' '))
        if (re.match(r'^\d+[.)]\s+\S', text) or re.search(r'\bRelevance\s+\d+(?:\.\d+)?/10', text, re.I)):
            headings.append(text)
    styles = {
        'p': 'margin:10px 0;line-height:1.55;',
        'h1': 'font-size:21px;color:#173e51;margin:20px 0 10px;',
        'h2': 'font-size:19px;color:#173e51;margin:20px 0 10px;',
        'h3': 'font-size:17px;color:#173e51;margin:24px 0 10px;line-height:1.4;',
        'a': 'color:#006d83;word-break:break-word;',
        'table': 'border-collapse:collapse;max-width:100%;',
        'td': 'padding:5px;vertical-align:top;',
        'th': 'padding:5px;text-align:left;',
        'li': 'margin:7px 0;line-height:1.5;',
    }
    for node in soup.find_all(True):
        if node.name in styles:
            node['style'] = styles[node.name]
    return headings


def select_messages(messages, cfg, start, end):
    selected = []
    duplicate_count = 0
    seen_ids, seen_bodies = set(), set()
    sender_filter = cfg.get('source_sender', '').strip().casefold()
    for m in sorted(messages, key=lambda x: x.received):
        if not start <= m.received < end:
            continue
        if not subject_matches(m.subject, cfg['subject_prefix']):
            continue
        if sender_filter and m.sender.casefold() != sender_filter:
            continue
        cleaned = sanitize(m.html)
        body_text = squish(cleaned.get_text(' '))
        if not body_text:
            raise WatcherError(f'An email has no readable body: {m.subject}. Check it in Outlook.')
        links = [comparison_url(a['href']) for a in cleaned.find_all('a', href=True)]
        fingerprint = sha256((body_text + '\n' + '\n'.join(links)).encode()).hexdigest()
        if (m.uid and m.uid in seen_ids) or fingerprint in seen_bodies:
            duplicate_count += 1
            continue
        if m.uid:
            seen_ids.add(m.uid)
        seen_bodies.add(fingerprint)
        selected.append(m)
    return selected, duplicate_count


@dataclass
class Digest:
    subject: str
    html: str
    text: str
    sources: list
    missing_dates: list
    duplicates: int
    key: str


def compile_digest(messages, cfg, start, end, sample=False):
    sources, duplicates = select_messages(messages, cfg, start, end)
    if not sources:
        raise WatcherError('No matching emails found for this week. Check the week, folder, subject and source sender.')
    days = {m.received.astimezone(IST).date() for m in sources}
    missing = [(start + timedelta(days=i)).date().isoformat() for i in range(7)
               if (start + timedelta(days=i)).date() not in days]
    label = f'{start:%d %b} – {end - timedelta(days=1):%d %b %Y}'
    subject = f'[Weekly] Tire Technology Watcher | {label}'
    chunks, index = [], []
    for n, m in enumerate(sources, 1):
        soup = sanitize(m.html)
        headings = headings_and_format(soup)
        day = m.received.astimezone(IST)
        index.append(f'<li><strong>{day:%A, %d %B}</strong>')
        if headings:
            index.append('<ul>' + ''.join(f'<li>{escape(h)}</li>' for h in headings) + '</ul>')
        index.append('</li>')
        attachment_note = (f'<p style="color:#8b4b16;">This source has {m.attachment_count} attachment(s). '
                           'Refer to the original email for those files.</p>') if m.attachment_count else ''
        chunks.append(f'<div style="border-top:3px solid #173e51;margin-top:32px;padding-top:16px;">'
                      f'<h2 style="font-size:23px;color:#173e51;margin:0 0 8px;">{day:%A, %d %B %Y}</h2>'
                      f'<p style="font-size:12px;color:#596774;">{escape(m.subject)}<br>'
                      f'Received {day:%H:%M} IST · Source sender: {escape(m.sender)}</p>'
                      f'{attachment_note}{soup}</div>')
    coverage = f'{len(sources)} source email(s) · {len(days)} of 7 receipt dates represented'
    missing_note = ('<p style="color:#8b4b16;">No matching email received on: '
                    + ', '.join(missing) + '.</p>') if missing else ''
    sample_note = '<p><strong>SAMPLE PREVIEW — generated only from the supplied 11 September email.</strong></p>' if sample else ''
    signature = '<p>' + escape(cfg.get('signature', '')).replace('\n', '<br>') + '</p>' if cfg.get('signature') else ''
    html = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + escape(subject) + '</title></head><body style="margin:0;background:#f0f3f5;">'
            '<div style="max-width:900px;margin:auto;padding:30px;background:#fff;font-family:Calibri,Arial,sans-serif;'
            'font-size:15px;color:#22303b;line-height:1.55;">'
            '<p style="font-size:12px;letter-spacing:2px;color:#006d83;">WEEKLY TECHNOLOGY REVIEW</p>'
            '<h1 style="font-size:30px;line-height:1.2;color:#173e51;margin:10px 0;">Tire Technology Watcher</h1>'
            f'<p style="font-size:20px;margin:8px 0;">{label}</p>{sample_note}'
            '<p>Dear team,</p><p>Please find the compilation of this week’s Tire Technology Watcher emails below.</p>'
            f'<p style="color:#596774;">{coverage}</p>{missing_note}'
            '<h2 style="font-size:21px;color:#173e51;">Contents</h2><ul>' + ''.join(index) + '</ul>'
            + ''.join(chunks) + signature + '</div></body></html>')
    if len(html.encode('utf-8')) > 2_000_000:
        raise WatcherError('The compiled body exceeds 2 MB. Select fewer source folders or review the unusually large source email.')
    key = sha256(f"{cfg['mailbox'].lower()}|{start.date()}|{end.date()}|{cfg['subject_prefix'].lower()}".encode()).hexdigest()[:24]
    return Digest(subject, html, BeautifulSoup(html, 'html.parser').get_text('\n', strip=True), sources, missing, duplicates, key)


def write_preview(digest, cfg, output):
    output.mkdir(parents=True, exist_ok=True)
    html_path = output / 'weekly_preview.html'
    html_path.write_text(digest.html, encoding='utf-8')
    eml = EmailMessage(policy=SMTP)
    eml['Subject'], eml['From'], eml['X-Unsent'] = digest.subject, cfg['mailbox'], '1'
    if cfg['to']:
        eml['To'] = ', '.join(cfg['to'])
    if cfg['cc']:
        eml['Cc'] = ', '.join(cfg['cc'])
    eml.set_content(digest.text)
    eml.add_alternative(digest.html, subtype='html')
    (output / 'weekly_preview.eml').write_bytes(eml.as_bytes())
    report = {
        'subject': digest.subject,
        'reporting_timezone': 'Asia/Kolkata',
        'source_email_count': len(digest.sources),
        'duplicate_emails_removed': digest.duplicates,
        'missing_receipt_dates': digest.missing_dates,
        'source_messages': [{'subject': m.subject, 'received': m.received.isoformat(),
                             'sender': m.sender, 'attachment_count': m.attachment_count} for m in digest.sources],
    }
    (output / 'compilation_report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    return html_path


def load_sample(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    result = []
    for m in data:
        body = m['body']
        result.append(SourceMail(m.get('internetMessageId') or m.get('id', ''), m['subject'],
                                 received_datetime(m['receivedDateTime']), m['from']['emailAddress']['address'],
                                 body['content'] if body['contentType'].lower() == 'html'
                                 else plain_to_html(body['content'])))
    return result


class OutlookDesktop:
    """Classic Outlook only. All writes are confined to its Drafts folder."""
    def __init__(self, cfg):
        if sys.platform != 'win32':
            raise WatcherError('Live mailbox access needs Windows with classic Outlook. Use --sample-json for offline preview.')
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise WatcherError('Install the Windows dependencies: python -m pip install -r tire_watcher_requirements.txt') from exc
        self.pythoncom = pythoncom
        pythoncom.CoInitialize()
        try:
            try:
                app = win32com.client.gencache.EnsureDispatch('Outlook.Application')
            except Exception:
                app = win32com.client.Dispatch('Outlook.Application')
            self.app = app
            self.session = app.GetNamespace('MAPI')
            if self.session.Offline:
                raise WatcherError('Outlook is offline. Connect it and wait for the source folders to synchronize.')
            accounts = [self.session.Accounts.Item(i) for i in range(1, self.session.Accounts.Count + 1)]
            matches = [a for a in accounts if str(a.SmtpAddress).casefold() == cfg['mailbox'].casefold()]
            if len(matches) != 1:
                raise WatcherError('The official mailbox must be configured as an Outlook account. '
                                   f"No unique account matches {cfg['mailbox']}. Open classic Outlook and check File > Account Settings.")
            self.account = matches[0]
            self.store = self.account.DeliveryStore
            self.drafts = self.store.GetDefaultFolder(16)
            self.cfg = cfg
        except Exception as exc:
            pythoncom.CoUninitialize()
            if isinstance(exc, WatcherError):
                raise
            raise WatcherError('Could not connect to classic Outlook. Open it, finish signing in, and try again. '
                               'The new Outlook app does not expose this desktop automation interface.') from exc

    def close(self):
        self.pythoncom.CoUninitialize()

    def folders(self):
        seen = set()
        def visit(folder):
            key = (str(folder.StoreID), str(folder.EntryID))
            if key in seen:
                return
            seen.add(key)
            yield folder
            if self.cfg['include_subfolders']:
                for i in range(1, folder.Folders.Count + 1):
                    yield from visit(folder.Folders.Item(i))
        excluded = set()
        for kind in (3, 4, 5, 16, 23):  # Deleted, Outbox, Sent, Drafts, Junk
            try:
                excluded.add(str(self.store.GetDefaultFolder(kind).EntryID))
            except Exception:
                pass
        for path in self.cfg['source_folders']:
            parts = [p.strip() for p in path.replace('\\', '/').split('/') if p.strip()]
            if not parts:
                raise WatcherError('A source folder path is empty.')
            folder = self.store.GetDefaultFolder(6) if parts[0].casefold() == '@inbox' else self.store.GetRootFolder().Folders.Item(parts[0])
            for part in parts[1:]:
                folder = folder.Folders.Item(part)
            for found in visit(folder):
                if str(found.EntryID) not in excluded:
                    yield found

    @staticmethod
    def sender_address(item):
        if str(item.SenderEmailType).upper() == 'EX':
            address = item.Sender
            if address is not None:
                user = address.GetExchangeUser()
                if user is not None and user.PrimarySmtpAddress:
                    return str(user.PrimarySmtpAddress)
            try:
                return str(item.PropertyAccessor.GetProperty('http://schemas.microsoft.com/mapi/proptag/0x5D01001F'))
            except Exception as exc:
                raise WatcherError('Could not resolve the SMTP sender of a matching source email.') from exc
        return str(item.SenderEmailAddress)

    def read_messages(self, start, end):
        result = []
        try:
            for folder in self.folders():
                items = folder.Items
                items.Sort('[ReceivedTime]', True)
                item = items.GetFirst()
                while item is not None:
                    if item.Class == 43:
                        received = received_datetime(item.ReceivedTime)
                        if received < start:
                            break
                        if received < end and subject_matches(str(item.Subject or ''), self.cfg['subject_prefix']):
                            sender = self.sender_address(item)
                            wanted = self.cfg.get('source_sender', '').strip().casefold()
                            if not wanted or wanted == sender.casefold():
                                try:
                                    uid = str(item.PropertyAccessor.GetProperty('http://schemas.microsoft.com/mapi/proptag/0x1035001F'))
                                except Exception:
                                    uid = str(item.EntryID)
                                body = str(item.HTMLBody or '') or plain_to_html(str(item.Body or ''))
                                result.append(SourceMail(uid, str(item.Subject), received, sender, body, int(item.Attachments.Count)))
                    item = items.GetNext()
        except WatcherError:
            raise
        except Exception as exc:
            raise WatcherError('Outlook could not read all selected folders. Compilation stopped to avoid a partial draft. '
                               'Check the folder paths, mailbox access and Outlook synchronization.') from exc
        return result

    def existing_draft(self, key):
        items = self.drafts.Items
        item = items.GetFirst()
        while item is not None:
            if item.Class == 43:
                marker = item.UserProperties.Find(MARKER)
                if marker is not None and str(marker.Value) == key:
                    return item
            item = items.GetNext()
        return None

    def save_draft(self, digest, cfg, new_copy=False, display=True):
        if not new_copy:
            existing = self.existing_draft(digest.key)
            if existing is not None:
                if display:
                    existing.Display(False)
                return 'existing'
        item = self.drafts.Items.Add('IPM.Note')
        try:
            item.SendUsingAccount = self.account
            # Dynamic pywin32 dispatch occasionally needs the documented property's DISPID.
            if item.SendUsingAccount is None or str(item.SendUsingAccount.SmtpAddress).casefold() != cfg['mailbox'].casefold():
                item._oleobj_.Invoke(64209, 0, 8, 0, self.account)
            if item.SendUsingAccount is None or str(item.SendUsingAccount.SmtpAddress).casefold() != cfg['mailbox'].casefold():
                raise WatcherError('Outlook did not select the official From account. No draft was saved.')
            item.Subject = digest.subject
            item.BodyFormat = 2
            item.HTMLBody = digest.html
            for recipient_type, key in ((1, 'to'), (2, 'cc')):
                for addr in cfg[key]:
                    recipient = item.Recipients.Add(addr)
                    recipient.Type = recipient_type
            marker = item.UserProperties.Add(MARKER, 1, False)
            marker.Value = digest.key
            item.Save()
        except Exception as exc:
            if isinstance(exc, WatcherError):
                raise
            raise WatcherError('Draft creation failed or its save status is uncertain. Check the official Drafts folder before retrying.') from exc
        try:
            parent = item.Parent
            if str(parent.StoreID) != str(self.store.StoreID):
                raise WatcherError('The saved draft is in an unexpected mailbox. Check Drafts before proceeding.')
            if display:
                item.Display(False)
        except WatcherError:
            raise
        except Exception as exc:
            raise WatcherError('The draft was saved, but Outlook could not open it. Find it in the official mailbox’s Drafts folder.') from exc
        return 'created'


def run(cfg, start_value, preview_only=False, sample_path=None, new_copy=False, open_preview=False):
    cfg = validate_config(dict(cfg))
    start, end = week_bounds(start_value)
    client = None
    try:
        if sample_path:
            messages = load_sample(sample_path)
            preview_only = True
        else:
            client = OutlookDesktop(cfg)
            messages = client.read_messages(start, end)
        digest = compile_digest(messages, cfg, start, end, sample=bool(sample_path))
        output = BASE / 'previews' / str(start.date())
        path = write_preview(digest, cfg, output)
        if preview_only:
            if open_preview:
                webbrowser.open(path.as_uri())
            status = f'Preview ready: {path}. No Outlook draft was created.'
        else:
            status = client.save_draft(digest, cfg, new_copy=new_copy)
            status = ('Existing draft reopened; your edits are preserved. The new compilation is available as a local preview.'
                      if status == 'existing' else f"Draft saved and opened in {cfg['mailbox']}.")
        notes = (f'\n{len(digest.sources)} source email(s); {digest.duplicates} duplicate email(s) removed.'
                 + ('\nNo matching email on: ' + ', '.join(digest.missing_dates) if digest.missing_dates else ''))
        return status + notes
    finally:
        if client is not None:
            client.close()


def gui(config_path):
    import queue
    import threading
    import tkinter as tk
    from tkinter import ttk, messagebox
    cfg = read_config(config_path)
    root = tk.Tk()
    root.title('Tire Technology Watcher — Weekly Draft')
    root.geometry('810x690')
    root.minsize(730, 640)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Prepare your weekly Outlook draft', font=('Segoe UI', 18, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
    ttk.Label(frame, text='Compile the daily emails, review the draft in Outlook, then send it yourself.', wraplength=720).grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 16))
    values = {}
    fields = [('mailbox', 'Official Outlook account', cfg['mailbox']),
              ('week', 'Week starting Monday', str(previous_week())),
              ('source_folders', 'Source folder(s)', '; '.join(cfg['source_folders'])),
              ('subject_prefix', 'Daily subject starts with', cfg['subject_prefix']),
              ('source_sender', 'Daily email sender', cfg.get('source_sender', '')),
              ('to', 'To', '; '.join(cfg['to'])), ('cc', 'Cc', '; '.join(cfg['cc']))]
    for row, (key, label, value) in enumerate(fields, 2):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 14), pady=6)
        values[key] = tk.StringVar(value=value)
        ttk.Entry(frame, textvariable=values[key], width=63).grid(row=row, column=1, sticky='ew', pady=6)
    ttk.Label(frame, text='Week: YYYY-MM-DD. Example: 2026-09-07 covers 7–13 September.\nFolders: @inbox or @inbox/Tire Technology Watcher; separate folders with semicolons.\nRecipients may be left blank and added in Outlook. Clear the sender field to match any sender.', wraplength=720).grid(row=9, column=0, columnspan=2, sticky='w', pady=(8, 10))
    subfolders = tk.BooleanVar(value=cfg['include_subfolders'])
    fresh = tk.BooleanVar(value=False)
    ttk.Checkbutton(frame, text='Include subfolders', variable=subfolders).grid(row=10, column=0, columnspan=2, sticky='w')
    ttk.Checkbutton(frame, text='Create a fresh draft even if this week already has one', variable=fresh).grid(row=11, column=0, columnspan=2, sticky='w')
    status = tk.StringVar(value='Ready. Requires classic Outlook on Windows with your official account signed in.')
    status_label = ttk.Label(frame, textvariable=status, wraplength=725, justify='left')
    status_label.grid(row=13, column=0, columnspan=2, sticky='w', pady=18)
    buttons = ttk.Frame(frame)
    buttons.grid(row=12, column=0, columnspan=2, sticky='w', pady=16)
    results = queue.Queue()
    def start_work(preview):
        try:
            current = dict(cfg)
            for key in ('mailbox', 'subject_prefix', 'source_sender'):
                current[key] = values[key].get().strip()
            current['source_folders'] = [v.strip() for v in values['source_folders'].get().split(';') if v.strip()]
            current['to'], current['cc'] = addresses(values['to'].get()), addresses(values['cc'].get())
            current['include_subfolders'] = subfolders.get()
            validate_config(current)
            week = values['week'].get().strip()
            first, end = week_bounds(week)
            if end > datetime.now(IST):
                if not messagebox.askyesno('Incomplete week', 'This reporting week has not ended. Compile the emails received so far?'):
                    return
            config_path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as exc:
            messagebox.showerror('Check settings', str(exc))
            return
        for button in (draft_button, preview_button):
            button.config(state='disabled')
        status.set('Reading the selected week and compiling the emails…')
        new_copy = fresh.get()
        def work():
            try:
                result = run(current, week, preview_only=preview, new_copy=new_copy, open_preview=preview)
                results.put((True, result))
            except Exception as exc:
                results.put((False, str(exc)))
        threading.Thread(target=work, daemon=True).start()
        def poll():
            try:
                ok, message = results.get_nowait()
            except queue.Empty:
                root.after(150, poll)
                return
            status.set(message)
            for button in (draft_button, preview_button):
                button.config(state='normal')
            if not ok:
                messagebox.showerror('Compilation stopped', message)
        root.after(150, poll)
    draft_button = ttk.Button(buttons, text='Compile and open Outlook draft', command=lambda: start_work(False))
    draft_button.pack(side='left', padx=(0, 12))
    preview_button = ttk.Button(buttons, text='Preview only', command=lambda: start_work(True))
    preview_button.pack(side='left')
    frame.columnconfigure(1, weight=1)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=BASE / 'config.json')
    parser.add_argument('--week-start', help='Monday date, YYYY-MM-DD; defaults to the previous completed week')
    parser.add_argument('--draft', action='store_true', help='Compile and save/open an Outlook draft without the GUI')
    parser.add_argument('--preview', action='store_true', help='Compile a local preview without writing to Outlook')
    parser.add_argument('--sample-json', type=Path, help='Read an offline sample; always preview-only')
    parser.add_argument('--new-copy', action='store_true', help='Create another draft, preserving any existing one')
    args = parser.parse_args()
    if args.draft and (args.preview or args.sample_json):
        parser.error('--draft cannot be combined with preview/sample mode')
    if not (args.draft or args.preview or args.sample_json):
        gui(args.config)
        return 0
    cfg = read_config(args.config)
    try:
        print(run(cfg, args.week_start or str(previous_week()),
                  preview_only=args.preview, sample_path=args.sample_json, new_copy=args.new_copy))
        return 0
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
