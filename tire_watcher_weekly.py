#!/usr/bin/env python3
"""Concise Outlook weekly brief. Keep tire_watcher_draft.py in this folder.

Opening this file shows a GUI; it never sends on startup. Automatic sending is
enabled only by installing the Monday task. Requires classic Outlook/Windows.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, time
from email.message import EmailMessage
from email.policy import SMTP
from hashlib import sha256
from html import escape
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import webbrowser

import tire_watcher_draft as core
from bs4 import BeautifulSoup, NavigableString

BASE = Path(__file__).resolve().parent
CONFIG = BASE / 'tire_watcher_weekly_config.json'
TASK_NAME = 'Tire Technology Watcher - Weekly Brief'
MARKER = 'TireWatcherBriefWeek'
LAYOUT_MARKER = 'TireWatcherBriefLayout'
LAYOUT_VERSION = 2
DRAFT_VERSION = '2.6'
ERROR = core.WatcherError
SCORE_RE = re.compile(r'\bRelevance(?:\s+score)?\s*:?\s*(\d+(?:\.\d+)?)\s*/\s*10', re.I)
# Closing sections. Daily editions vary between singular and plural headings and
# add scan sections, so accept both forms rather than only the plural ones.
END_RE = re.compile(r"^(developing themes?|priority (?:team )?actions|weekly themes?|key takeaways?|"
                    r"references|disclaimer|watch list|today[’']s priority|closing notes?|"
                    r"event\s*[/&]?\s*learning scan|events? scan|learning scan|"
                    r"conference\s*[/&]?\s*event scan|patent scan)\b", re.I)
# Labelled metadata lines. These are structure, never the summary sentence.
FIELD_RE = re.compile(r'^(segment|segments|topic|technology topic|technology topics|technology area|'
                      r'relevance(?: score)?|why it matters|so what|suggested follow-ups?|'
                      r'suggested action|recommended follow-up|follow-ups?|next steps?|'
                      r'sources?|supporting sources?|reference|background|context|scope|deduplication|'
                      r"development|developments|what is new|what[’']s new|update|status)\s*:", re.I)
METADATA_RE = re.compile(r'^(?:(?:segment|segments|topic|technology topic|technology area)\s*:'
                         r'|relevance(?:\s+score)?\s*:?\s*\d)', re.I)
UPDATE_RE = re.compile(r"^(?:what is new|what[’']s new)\s*:\s*", re.I)
# The sentence that actually describes the development, whatever label carries it.
DETAIL_RE = re.compile(r"^(?:development|developments|what is new|what[’']s new|update|summary)\s*:\s*", re.I)
# Lines saying a section produced nothing today. These are not article content.
NOTHING_RE = re.compile(r'^(?:no\s|none\b|nothing\b|not applicable\b|n/?a\b)', re.I)
# Editorial 'nothing to report' bullets, which deliberately carry no source link.
NO_CONTENT_RE = re.compile(r'\bno (?:sufficiently|newly|new|further|additional|meaningful|other)\b'
                           r'|\bnot repeated\b|\bwas found today\b|\bnothing (?:new|further)\b', re.I)


def read_config(path):
    if not path.is_file():
        raise ERROR(f'Place {path.name} in the same folder as this program.')
    cfg = core.read_config(path)
    core.validate_config(cfg)
    # Upgrade older 3+6 layouts without changing mailbox or scheduled-send settings.
    if int(cfg.get('layout_version', 1)) < LAYOUT_VERSION:
        cfg.update(layout_version=LAYOUT_VERSION, featured_items=10, additional_items=10)
    cfg.setdefault('featured_items', 10)
    cfg.setdefault('additional_items', 10)
    featured, additional = item_limits(cfg)
    cfg['max_items_in_email'] = featured + additional  # Compatibility / reporting.
    cfg.setdefault('automatic_sending_enabled', False)
    if not cfg['to']:
        raise ERROR('At least one To address is required.')
    if set(x.lower() for x in cfg['to']) & set(x.lower() for x in cfg['cc']):
        raise ERROR('An address appears in both To and Cc. Remove the duplicate.')
    return cfg


def item_limits(cfg):
    counts = []
    for key, default, lower in [('featured_items', 10, 1), ('additional_items', 10, 0)]:
        value = cfg.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= 25:
            raise ERROR(f'{key} must be a whole number between {lower} and 25.')
        counts.append(value)
    return tuple(counts)


def write_config(path, cfg):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def excerpt(text, words):
    """Extract a first sentence, with a visible ellipsis if word-limited."""
    text = core.squish(text)
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z“])', text)
    # Discard this non-informative sentence only when a following sentence exists.
    if len(sentences) > 1 and sentences[0] == 'This is more than a capacity announcement.':
        sentences = sentences[1:]
    first = sentences[0] if sentences else ''
    parts = first.split()
    return ' '.join(parts[:words]).rstrip(' ,;:') + '…' if len(parts) > words else first


def topic_style(text):
    s = text.casefold()
    if any(k in s for k in ('conference', 'society', 'peer-reviewed', 'nature communications', 'research watch')):
        return 'RESEARCH & EVENTS', '#94600c', '#fff5df'
    if any(k in s for k in ('tbr', 'truck', 'construction tire')):
        return 'COMMERCIAL TIRES', '#235da0', '#edf3fb'
    if any(k in s for k in ('pcr', 'passenger', 'winter', 'suv')):
        return 'PASSENGER TIRES', '#7552a2', '#f3eef9'
    if any(k in s for k in ('mixing', 'compound', 'filler', 'additive', 'material')):
        return 'MATERIALS & PROCESS', '#087c72', '#e9f6f2'
    return 'TECHNOLOGY', '#52616b', '#eff3f6'


@dataclass
class Article:
    title: str
    lead: str
    why: str
    metadata: str
    links: list[str]
    score: float | None
    received: datetime
    fingerprint: str


def article_blocks(soup):
    """Read logical lines once, preserving inline text and the links on each line.

    Outlook may store a whole article inside one <p> with <br> separators,
    or as separate paragraphs. Inline bold/span tags are not separators.
    """
    blocks, parts, links = [], [], []
    boundaries = {'p', 'div', 'li', 'ul', 'ol', 'td', 'th', 'tr', 'table',
                  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre'}

    def flush():
        text = core.squish(''.join(parts))
        for match in re.finditer(r'https?://[^\s<>]+', text):
            url = core.safe_url(match[0].rstrip('.,;'))
            if url and not any(core.comparison_url(url) == core.comparison_url(existing) for existing in links):
                links.append(url)
        if text or links:
            blocks.append((text, list(dict.fromkeys(links)), False))
        parts.clear()
        links.clear()

    def visit(node):
        if isinstance(node, NavigableString):
            parts.append(str(node))
            return
        if node.name in boundaries or node.name in ('br', 'hr'):
            flush()
        if node.name == 'a':
            href = core.safe_url(node.get('href', ''))
            if href.startswith(('https://', 'http://')):
                links.append(href)
        for child in node.children:
            visit(child)
        if node.name in boundaries:
            flush()

    visit(soup)
    flush()
    return blocks


def normalize_plain_newsletter(body):
    """Reassemble wrapped plain text, including Outlook's paragraph wrappers.

    Recognize split numbered titles by a score on a continuation line before
    metadata. Ordinary rich HTML retains its original structure and links.
    """
    soup = core.sanitize(body)
    if soup.find(['p', 'li', 'table', 'h1', 'h2', 'h3']):
        blocks = article_blocks(soup)
        wrapped = False
        for index, (text, _, _) in enumerate(blocks):
            if not re.match(r'^\d+[.)]\s+\S', text) or SCORE_RE.search(text):
                continue
            for continuation, _, _ in blocks[index + 1:index + 7]:
                if FIELD_RE.match(continuation) or re.match(r'^\d+[.)]\s+\S', continuation) or END_RE.match(continuation):
                    break
                if SCORE_RE.search(continuation):
                    wrapped = True
                    break
            if wrapped:
                break
        if not wrapped:
            return body
        lines = []
        for text, links, _ in blocks:
            # Retain actual protected targets, including anchors with short labels.
            for url in links:
                if url not in text:
                    text += ' ' + url
            lines.append(text)
    else:
        lines = soup.get_text('\n').splitlines()
    out, pending, kind = [], [], ''

    def flush():
        if pending:
            out.append('<p>' + escape(core.squish(' '.join(pending))) + '</p>')
            pending.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
            kind = ''
            continue
        section = bool(END_RE.match(line)) or line.casefold() in ('top developments', 'research watch', 'quick reads')
        numbered = bool(re.match(r'^\d+[.)]\s+\S', line))
        field = bool(FIELD_RE.match(line))
        bullet = line.startswith(('• ', '- '))
        if section:
            flush()
            out.append('<p>' + escape(line) + '</p>')
            kind = ''
            continue
        if numbered or field or bullet:
            flush()
            kind = 'title' if numbered else 'metadata' if METADATA_RE.match(line) else 'field' if field else 'bullet'
        elif (kind == 'metadata' and pending and not pending[-1].endswith('|')
              and not line.startswith('|')):
            flush()
            kind = 'body'
        pending.append(line)
    flush()
    result = BeautifulSoup(''.join(out), 'html.parser')
    # Plain-text URLs need anchors for both the short email and full PDF.
    for node in list(result.find_all(string=True)):
        text = str(node)
        matches = list(re.finditer(r'https?://[^\s<>]+', text))
        if not matches:
            continue
        cursor = 0
        for match in matches:
            node.insert_before(NavigableString(text[cursor:match.start()]))
            url = match[0].rstrip('.,;')
            anchor = result.new_tag('a', href=core.safe_url(url))
            anchor.string = 'Source link'
            node.insert_before(anchor)
            node.insert_before(NavigableString(match[0][len(url):]))
            cursor = match.end()
        node.insert_before(NavigableString(text[cursor:]))
        node.extract()
    return str(result)


def extract_articles(mail):
    soup = core.sanitize(normalize_plain_newsletter(mail.html))
    core.headings_and_format(soup)
    blocks = article_blocks(soup)
    articles, current, incomplete, orphan_sources = [], None, [], []

    def finish():
        if not current:
            return
        leads, detail = [], ''
        for s in current['body']:
            if re.match(r'^sources?\s*:', s, re.I):
                break
            if DETAIL_RE.match(s) and not detail:
                detail = DETAIL_RE.sub('', s).strip()
                continue
            if not FIELD_RE.match(s) and not METADATA_RE.match(s) and not s.startswith('|'):
                leads.append(UPDATE_RE.sub('', s))
        # A labelled 'Development:' sentence is the summary; unlabelled prose is the
        # fallback. Either way the metadata lines never become the visible lead.
        lead = detail or next((s for s in leads if s), '')
        if not lead:
            # Some editions carry only a title plus 'Why it matters'. That is still a
            # usable update, so fall back to the heading rather than rejecting the email.
            lead = SCORE_RE.sub('', re.sub(r'^\d+[.)]\s*', '', current['heading'])).strip(' —–-|:')
        if not lead and not current['links']:
            if current['heading'].casefold() != 'research watch' or current['body'] or current['links']:
                incomplete.append(current['heading'])
            return
        title = re.sub(r'^\d+[.)]\s*', '', current['heading'])
        title = SCORE_RE.sub('', title).strip(' —–-|:')
        if title.casefold() == 'research watch':
            quoted = re.search(r'[“\"]([^”\"]{8,240})[”\"]', lead)
            if quoted:
                title = 'Research watch: ' + quoted[1].rstrip(' ,;')
        why = next((re.sub(r'^why it matters\s*:\s*', '', s, flags=re.I)
                    for s in current['body'] if re.match(r'^why it matters\s*:', s, re.I)), '')
        links = list(dict.fromkeys(current['links']))
        canonical = '\n'.join(core.comparison_url(link) for link in links)
        fingerprint = sha256((title.casefold() + '\n' + '\n'.join(current['body']) + canonical).encode()).hexdigest()
        score_match = SCORE_RE.search(current['heading'])
        if score_match is None:
            score_match = next((SCORE_RE.search(s) for s in current['body']
                                if METADATA_RE.match(s) and SCORE_RE.search(s)), None)
        articles.append(Article(title, lead, why, ' '.join(current['body'][:1]), links,
                                float(score_match[1]) if score_match else None, mail.received, fingerprint))

    pending_number = ''
    quick_mode = False
    quick_parts, quick_links = [], []
    quick_bullet = re.compile(r'^[•●▪‣–-]\s*')

    def finish_quick():
        if not quick_parts and not quick_links:
            return
        lead = core.squish(' '.join(quick_parts))
        lead = re.sub(r'https?://[^\s<>]+', '', lead)
        lead = re.sub(r'\bSource link\b', '', lead)
        lead = core.squish(lead).strip(' :;')
        links = list(dict.fromkeys(quick_links))
        if not lead or not links:
            # A quick read without a link is still readable in the PDF; note it and move on,
            # unless it is an explicit 'nothing found today' bullet, which is not a defect.
            if not (lead and (NOTHING_RE.match(lead) or NO_CONTENT_RE.search(lead))):
                incomplete.append(lead[:100] or 'quick read')
            quick_parts.clear()
            quick_links.clear()
            return
        fingerprint = sha256((lead.casefold() + '\n' + '\n'.join(core.comparison_url(u) for u in links)).encode()).hexdigest()
        articles.append(Article(excerpt(lead, 16), lead, '', '', links, None, mail.received, fingerprint))
        quick_parts.clear()
        quick_links.clear()
    for block_index, (text, links, is_heading) in enumerate(blocks):
        if text.casefold() == 'quick reads':
            finish()
            current = None
            quick_mode = True
            continue
        if END_RE.match(text):
            finish()
            current = None
            break
        if quick_mode:
            bullet = quick_bullet.match(text)
            if bullet:
                finish_quick()
                text = text[bullet.end():].strip()
            elif not quick_parts and not quick_links:
                # Unbulleted text under 'Quick reads' is usually a lead-in line, not an item.
                if NOTHING_RE.match(text):
                    continue
                incomplete.append(text[:100])
                continue
            quick_parts.append(text)
            quick_links.extend(links)
            continue
        if current is None and NOTHING_RE.match(text) and not links:
            continue
        if re.fullmatch(r'\d+[.)]', text) and not links:
            pending_number = text
            continue
        if pending_number:
            text = pending_number + ' ' + text
            pending_number = ''
        if text.casefold() in ('top developments', 'top development', 'developments'):
            finish()
            current = None
            continue
        text = re.sub(r'^(?:Top developments|Research watch)\s+(?=\S)', '', text, flags=re.I)
        next_text = blocks[block_index + 1][0] if block_index + 1 < len(blocks) else ''
        # Titles may be unnumbered headings; their metadata belongs to that title.
        followed_by_metadata = bool(METADATA_RE.match(next_text))
        candidate = bool(SCORE_RE.search(text)) or bool(re.match(r'^\d+[.)]\s+\S', text)) or followed_by_metadata
        if text.casefold() == 'research watch':
            finish()
            current = {'heading': 'Research watch', 'body': [], 'links': links}
        elif (candidate and len(text.split()) < 85 and not FIELD_RE.match(text) and not METADATA_RE.match(text)
              and not UPDATE_RE.match(text) and not END_RE.match(text)):
            finish()
            current = {'heading': text, 'body': [], 'links': links}
        elif END_RE.match(text):
            finish()
            current = None
        elif current:
            current['body'].append(text)
            current['links'].extend(links)
        if re.match(r'^sources?\s*:', text, re.I) and links and current is None:
            orphan_sources.append(text)
    finish()
    finish_quick()
    # A daily email that reports no updates, or whose layout drifted, is no longer fatal.
    # Its full text still reaches the PDF through the digest; only the short brief skips it.
    notes = []
    if incomplete:
        notes.append(f'{len(incomplete)} block(s) in "{mail.subject}" were not fully recognized: '
                     + '; '.join(core.squish(x)[:70] for x in incomplete[:3]))
    if orphan_sources:
        notes.append(f'{len(orphan_sources)} source line(s) in "{mail.subject}" had no matching update.')
    if not articles:
        notes.append(f'No updates were recognized in "{mail.subject}".')
    return articles, notes


@dataclass
class Brief:
    digest: core.Digest
    full_html: str
    articles: list[Article]
    repeated_articles: int
    sample: bool = False
    notes: list[str] = field(default_factory=list)


def link_html(article):
    if not article.links:
        return '<span style="color:#697681;font-size:12px;">See full report for source details</span>'
    links = []
    for i, url in enumerate(article.links[:2]):
        label = 'Read source' if i == 0 else 'Related source'
        links.append(f'<a href="{escape(url, quote=True)}" style="color:#087c72;font-size:12px;text-decoration:underline;">{label}</a>')
    return ' &nbsp;·&nbsp; '.join(links)


def compile_brief(messages, cfg, start, end, sample=False):
    messages = [replace(m, html=normalize_plain_newsletter(m.html)) for m in messages]
    full = core.compile_digest(messages, cfg, start, end, sample=sample)
    articles, seen, repeats, notes = [], set(), 0, []
    for mail in full.sources:
        found, mail_notes = extract_articles(mail)
        notes.extend(mail_notes)
        for article in found:
            if article.fingerprint in seen:
                repeats += 1
                continue
            seen.add(article.fingerprint)
            articles.append(article)
    articles.sort(key=lambda a: (a.score is not None, a.score or 0, a.received), reverse=True)
    featured_limit, additional_limit = item_limits(cfg)
    shown = articles[:featured_limit + additional_limit]
    featured, rest = shown[:featured_limit], shown[featured_limit:]
    has_scores = any(a.score is not None for a in articles)
    label = f'{start:%d %b} – {end - timedelta(days=1):%d %b %Y}'
    cards = []
    for i, a in enumerate(featured, 1):
        category, color, tint = topic_style(a.title + ' ' + a.metadata)
        why = (f'<p style="margin:9px 0 0;font-size:13px;line-height:1.5;color:#415362;">'
               f'<b>Why it matters</b> &nbsp;{escape(excerpt(a.why, 30))}</p>') if a.why else ''
        cards.append(f'''<tr><td data-brief-section="featured" style="padding:0 24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #dce5e9;">
<tr><td width="5" bgcolor="{color}" style="width:5px;font-size:1px;">&nbsp;</td>
<td bgcolor="#ffffff" style="padding:17px 18px;">
<p style="margin:0 0 7px;font-size:10px;font-weight:bold;letter-spacing:1px;color:{color};">{i:02d} &nbsp; / &nbsp; {escape(category)}</p>
<h2 style="margin:0 0 9px;font-size:18px;line-height:1.35;font-weight:bold;color:#172f40;">{escape(a.title)}</h2>
<p style="margin:0;font-size:14px;line-height:1.5;color:#263d4c;">{escape(excerpt(a.lead, 48))}</p>{why}
<p style="margin:11px 0 0;line-height:1.4;">{link_html(a)} <span style="font-size:11px;color:#788691;"> &nbsp;·&nbsp; {a.received.astimezone(core.IST):%d %b}</span></p>
</td></tr></table></td></tr>''')
    quick = []
    for i, a in enumerate(rest, len(featured) + 1):
        category, color, tint = topic_style(a.title + ' ' + a.metadata)
        quick.append(f'''<tr><td data-brief-section="additional" style="padding:13px 0;border-bottom:1px solid #e4eaee;">
<p style="margin:0 0 5px;color:{color};font-size:10px;letter-spacing:.6px;font-weight:bold;">{i:02d} &nbsp; / &nbsp; {escape(category)}</p>
<p style="margin:0 0 5px;font-size:14px;line-height:1.4;color:#172f40;font-weight:bold;">{escape(a.title)}</p>
<p style="margin:0 0 7px;font-size:13px;line-height:1.45;color:#526573;">{escape(excerpt(a.lead, 28))}</p>
{link_html(a)}</td></tr>''')
    quick_html = (f'''<tr><td style="padding:10px 24px 22px;"><h2 style="margin:0 0 3px;font-size:18px;color:#172f40;">{len(rest)} more to watch</h2>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'''
                  + ''.join(quick) + '</table></td></tr>') if quick else ''
    words = len(BeautifulSoup(''.join(cards + quick), 'html.parser').get_text(' ').split())
    minutes = max(1, math.ceil(words / 220))
    sample_note = ('<tr><td bgcolor="#fff5df" style="padding:10px 24px;color:#805913;font-size:12px;">'
                   '<b>LAYOUT SAMPLE</b> · Based on your one daily email of 11 September 2026.</td></tr>') if sample else ''
    missing_note = ('<p style="margin:8px 0 0;font-size:11px;color:#805913;">No matching email received on: '
                    + ', '.join(full.missing_dates) + '.</p>') if full.missing_dates else ''
    # Days that carried no updates, or whose layout drifted, are reported rather than
    # silently dropped. Their full text is still in the attached PDF.
    notes_note = ('<p style="margin:8px 0 0;font-size:11px;color:#805913;">'
                  + escape(' | '.join(notes[:4]))
                  + ' Full text remains in the attached PDF.</p>') if notes else ''
    order_note = ('Priority follows the relevance scores in the daily emails; ties use the latest received date.'
                  if has_scores else 'Items appear by most recent received date.')
    more = f'{len(articles) - len(shown)} additional item(s) and ' if len(articles) > len(shown) else ''
    signature = (f'<p style="margin:12px 0 0;">{escape(cfg["signature"]).replace(chr(10), "<br>")}</p>'
                 if cfg.get('signature') else '')
    if featured:
        intro = 'Hello team, here are the developments to catch up on this week.'
        headline = f'Top {len(featured)} updates'
    else:
        intro = ('Hello team, the daily emails for this week reported no new developments '
                 'above the relevance threshold. The attached PDF has each day in full.')
        headline = 'No new updates this week'
        order_note = ''
    html = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(full.subject)}</title></head>
<body style="margin:0;padding:0;background:#edf2f5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#edf2f5" style="border-collapse:collapse;">
<tr><td align="center" style="padding:20px 8px;">
<!--[if mso]><table role="presentation" width="700" cellpadding="0" cellspacing="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#ffffff" style="max-width:700px;border-collapse:collapse;font-family:Arial,Calibri,sans-serif;text-align:left;">
<tr><td bgcolor="#0c8b80" style="height:5px;font-size:1px;line-height:5px;">&nbsp;</td></tr>
<tr><td bgcolor="#142e40" style="padding:26px 24px 24px;color:#ffffff;">
<p style="margin:0 0 12px;font-size:11px;letter-spacing:2px;color:#72d8cb;font-weight:bold;">THE WEEKLY BRIEF &nbsp; / &nbsp; R&amp;D</p>
<h1 style="margin:0 0 10px;font-size:29px;line-height:1.2;color:#ffffff;">Tire Technology Watcher</h1>
<p style="margin:0;font-size:14px;line-height:1.5;color:#c7dbe5;">{label} &nbsp;·&nbsp; Approx. {minutes}-minute read</p></td></tr>
{sample_note}
<tr><td bgcolor="#e9f6f2" style="padding:12px 24px;font-size:12px;line-height:1.6;color:#245a53;">
<b>{len(shown)} of {len(articles)} developments</b> &nbsp; / &nbsp; {len(full.sources)} daily email(s) &nbsp; / &nbsp; Full PDF attached</td></tr>
<tr><td style="padding:22px 24px 16px;">
<p style="margin:0 0 8px;font-size:14px;line-height:1.5;color:#415362;">{intro}</p>
<h2 style="margin:0;font-size:19px;color:#172f40;">{headline}</h2>
<p style="margin:6px 0 0;font-size:11px;line-height:1.4;color:#70818d;">{order_note}</p></td></tr>
{''.join(cards)}{quick_html}
<tr><td style="padding:4px 24px 24px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#edf3f7">
<tr><td style="padding:17px 18px;color:#263d4c;">
<p style="margin:0 0 6px;font-size:15px;font-weight:bold;">Want the technical detail?</p>
<p style="margin:0;font-size:13px;line-height:1.5;">Open the attached <b>Full weekly report (PDF)</b> for {more}all technical notes, suggested follow-ups and source links.</p>
</td></tr></table></td></tr>
<tr><td style="padding:16px 24px;border-top:1px solid #dce5e9;color:#71818c;font-size:11px;line-height:1.5;">
Short excerpts from the daily Watcher emails; claims and relevance scores are those of the sources. Ellipses mark shortened text. Links lead to external sources.
{missing_note}{notes_note}{signature}</td></tr>
</table><!--[if mso]></td></tr></table><![endif]--></td></tr></table></body></html>'''
    digest = core.Digest(full.subject, html, BeautifulSoup(html, 'html.parser').get_text('\n', strip=True),
                         full.sources, full.missing_dates, full.duplicates, full.key)
    return Brief(digest, full.html, articles, repeats, sample=sample, notes=notes)


def pdf_text(value):
    """Normalize typography for print without altering scientific symbols."""
    value = str(value).translate(str.maketrans({'\u2010': '-', '\u2011': '-', '\u2012': '-',
                                              '\u2013': '-', '\u2014': '-', '\u00a0': ' '}))
    return ''.join(c for c in value if ord(c) >= 32 or c in '\n\t')


def write_full_pdf(brief, cfg, path):
    """Locally create a paginated full report; no browser, Word or cloud API."""
    try:
        import reportlab
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak,
                                      HRFlowable, LongTable, TableStyle)
    except ImportError as exc:
        raise ERROR('PDF support needs ReportLab. In your usual Anaconda Prompt, run: '
                    'python -m pip install "reportlab>=4.4.9,<5". Then reopen this program.') from exc

    # ReportLab's bundled Vera lacks Greek letters such as tan delta. Use system
    # fonts with scientific glyph coverage; Windows already supplies Arial.
    candidates = [
        (Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts',
         ['arial.ttf', 'arialbd.ttf', 'ariali.ttf', 'arialbi.ttf']),
        (Path('/usr/share/fonts/truetype/dejavu'),
         ['DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSans-Oblique.ttf', 'DejaVuSans-BoldOblique.ttf']),
        # Minimal Linux installations may omit italic faces; retain legible text.
        (Path('/usr/share/fonts/truetype/dejavu'),
         ['DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSans.ttf', 'DejaVuSans-Bold.ttf']),
    ]
    chosen = next(((folder, names) for folder, names in candidates if all((folder / name).is_file() for name in names)), None)
    if chosen is None:
        raise ERROR('PDF creation needs the Windows Arial fonts (regular, bold, italic and bold italic). '
                    'Check that they are available in the Windows Fonts folder.')
    fonts, filenames = chosen
    font_files = dict(zip(['Watcher', 'Watcher-Bold', 'Watcher-Italic', 'Watcher-BoldItalic'], filenames))
    for name, filename in font_files.items():
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / filename)))
    pdfmetrics.registerFontFamily('Watcher', normal='Watcher', bold='Watcher-Bold',
                                  italic='Watcher-Italic', boldItalic='Watcher-BoldItalic')
    all_text = pdf_text(BeautifulSoup(brief.full_html, 'html.parser').get_text(' '))
    available = pdfmetrics.getFont('Watcher').face.charToGlyph
    missing = sorted({c for c in all_text if not c.isspace() and ord(c) not in available})
    if missing:
        codes = ', '.join(f'U+{ord(c):04X}' for c in missing[:8])
        raise ERROR(f'The PDF font cannot display these source characters: {codes}. '
                    'PDF creation stopped to avoid losing technical symbols. Review the source text.')
    navy, teal, muted = (colors.HexColor(c) for c in ('#142e40', '#087c72', '#536573'))
    normal = ParagraphStyle('Body', fontName='Watcher', fontSize=9.5, leading=14,
                            spaceAfter=7, textColor=navy, alignment=TA_LEFT,
                            splitLongWords=True, allowWidows=0, allowOrphans=0)
    title = ParagraphStyle('Title', parent=normal, fontName='Watcher-Bold', fontSize=25,
                           leading=30, spaceBefore=7, spaceAfter=8)
    sub = ParagraphStyle('Sub', parent=normal, fontSize=12, leading=18, textColor=teal)
    day_style = ParagraphStyle('Day', parent=normal, fontName='Watcher-Bold', fontSize=16,
                               leading=21, spaceBefore=14, spaceAfter=8, keepWithNext=True)
    heading = ParagraphStyle('Heading', parent=normal, fontName='Watcher-Bold', fontSize=11,
                             leading=16, spaceBefore=13, spaceAfter=7, keepWithNext=True)
    meta = ParagraphStyle('Meta', parent=normal, fontSize=8, leading=12, textColor=muted)
    bullet = ParagraphStyle('Bullet', parent=normal, leftIndent=11, firstLineIndent=-9)
    note = ParagraphStyle('Note', parent=meta, backColor=colors.HexColor('#fff5df'),
                          borderPadding=8, spaceBefore=7, spaceAfter=14)

    def paragraph(text, style=normal):
        return Paragraph(escape(pdf_text(text)), style)

    def inline(node):
        if isinstance(node, NavigableString):
            return escape(pdf_text(node))
        if node.name == 'br':
            return '<br/>'
        content = ''.join(inline(child) for child in node.children)
        tag = {'strong': 'b', 'b': 'b', 'em': 'i', 'i': 'i', 'u': 'u',
               'sub': 'sub', 'sup': 'super'}.get(node.name)
        if tag:
            return f'<{tag}>{content}</{tag}>'
        if node.name == 'a':
            href = core.safe_url(node.get('href', ''))
            if href:
                # Preserve Safe Links in annotations, including their original query.
                return f'<link href="{escape(href, quote=True)}" color="#087c72"><u>{content or escape(href)}</u></link>'
        if node.name in ('p', 'div', 'li') and content.strip():
            return content + '<br/>'
        return content

    class ReportDoc(SimpleDocTemplate):
        def afterFlowable(self, flowable):
            outline = getattr(flowable, '_watcher_outline', None)
            if outline:
                label, anchor, level = outline
                self.canv.bookmarkPage(anchor)
                self.canv.addOutlineEntry(label, anchor, level=level, closed=bool(level == 0))

    page_width, page_height = A4
    margin = 44
    width = page_width - 2 * margin
    doc = ReportDoc(str(path), pagesize=A4, leftMargin=margin, rightMargin=margin,
                    topMargin=52, bottomMargin=46, title=pdf_text(brief.digest.subject),
                    author=cfg['mailbox'], pageCompression=1)
    story = [paragraph('TIRE TECHNOLOGY WATCHER  /  WEEKLY REPORT', meta),
             paragraph('The complete weekly report', title),
             paragraph(brief.digest.subject.split('|', 1)[-1].strip(), sub)]
    featured_limit, additional_limit = item_limits(cfg)
    displayed = min(len(brief.articles), featured_limit + additional_limit)
    story.append(paragraph(f'{len(brief.digest.sources)} daily email(s) | {len(brief.articles)} unique developments | '
                           f'{displayed} included in the email brief', meta))
    story.append(HRFlowable(width='100%', thickness=2, color=teal, spaceBefore=5, spaceAfter=12))
    if brief.sample:
        story.append(paragraph('LAYOUT SAMPLE - Based only on the supplied daily email of 11 September 2026. '
                               'This is not a complete seven-day digest.', note))
    story.append(paragraph('Full daily content follows in received-date order, including technical notes, '
                           'follow-up actions and source links. Repeated coverage is retained here. '
                           'Source claims and relevance scores have not been independently verified.', normal))
    if brief.digest.missing_dates:
        story.append(paragraph('No matching email received on: ' + ', '.join(brief.digest.missing_dates) + '.', note))
    for message in brief.notes:
        story.append(paragraph(message, note))
    story.append(paragraph('Daily editions - click a date or use the PDF bookmarks', heading))
    for i, mail in enumerate(brief.digest.sources, 1):
        label = mail.received.astimezone(core.IST).strftime('%A, %d %B %Y')
        story.append(Paragraph(f'<link href="#source-{i}" color="#087c72">{escape(label)}</link>', normal))
    story.append(Spacer(1, 5))
    blocks = {'p', 'div', 'blockquote', 'pre', 'ul', 'ol', 'li', 'table', 'thead',
              'tbody', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr'}
    outline_index = 0

    def walk(node):
        nonlocal outline_index
        if isinstance(node, NavigableString):
            if core.squish(node):
                story.append(paragraph(core.squish(node)))
            return
        if node.name == 'hr':
            story.append(HRFlowable(width='100%', thickness=.5, color=colors.HexColor('#dce5e9'),
                                   spaceBefore=6, spaceAfter=8))
            return
        if re.fullmatch(r'h[1-6]', node.name or ''):
            text = core.squish(node.get_text(' '))
            p = paragraph(text, heading)
            if SCORE_RE.search(text) or re.match(r'^\d+[.)]\s', text):
                outline_index += 1
                p._watcher_outline = (pdf_text(text), f'article-{outline_index}', 1)
            story.append(p)
            return
        if node.name == 'table':
            rows = [[cell for cell in row.find_all(['td', 'th'], recursive=False)]
                    for row in node.find_all('tr') if row.find_parent('table') is node]
            rows = [row for row in rows if row]
            # Compact data tables keep their columns; long layout tables reflow as blocks.
            if (rows and max(len(row) for row in rows) > 1 and not node.find('table')
                    and not any(cell.has_attr('rowspan') or cell.has_attr('colspan') for row in rows for cell in row)
                    and all(len(cell.get_text()) < 400 for row in rows for cell in row)
                    and all(core.squish(cell.get_text()) for row in rows for cell in row)):
                columns = max(len(row) for row in rows)
                data = [[Paragraph(inline(cell), meta) for cell in row] + [''] * (columns - len(row)) for row in rows]
                header = any(cell.name == 'th' for cell in rows[0])
                table = LongTable(data, colWidths=[width / columns] * columns, repeatRows=int(header), hAlign='LEFT')
                commands = [('VALIGN', (0, 0), (-1, -1), 'TOP'), ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#dce5e9')),
                            ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]
                if header:
                    commands.append(('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e9f6f2')))
                table.setStyle(TableStyle(commands))
                story.extend([table, Spacer(1, 9)])
                return
        if node.name in ('p', 'li', 'pre') and not node.find(['p', 'ul', 'ol', 'table', 'div']):
            content = ''.join(inline(child) for child in node.children)
            if core.squish(node.get_text()):
                if node.name == 'li':
                    content = '- ' + re.sub(r'^\s*\*\s*', '', content)
                story.append(Paragraph(content, bullet if node.name == 'li' else normal))
            return
        pending = []
        def flush():
            if pending:
                markup = ''.join(inline(child) for child in pending)
                if core.squish(BeautifulSoup(markup, 'html.parser').get_text()):
                    story.append(Paragraph(markup, normal))
                pending.clear()
        for child in node.children:
            if not isinstance(child, NavigableString) and child.name in blocks:
                flush()
                walk(child)
            else:
                pending.append(child)
        flush()

    for i, mail in enumerate(brief.digest.sources, 1):
        if i > 1:
            story.append(PageBreak())
        day = mail.received.astimezone(core.IST)
        day_heading = paragraph(day.strftime('%A, %d %B %Y'), day_style)
        day_heading._watcher_outline = (day.strftime('%d %B %Y'), f'source-{i}', 0)
        story.extend([day_heading, paragraph(mail.subject, meta),
                      paragraph(f'Received {day:%H:%M} IST | Sender: {mail.sender}', meta)])
        if mail.attachment_count:
            story.append(paragraph(f'The original email has {mail.attachment_count} attachment(s). '
                                   'Refer to the original message for those files.', note))
        soup = core.sanitize(mail.html)
        core.headings_and_format(soup)
        walk(soup)
    if cfg.get('signature'):
        story.append(paragraph(cfg['signature']))

    def page_frame(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(teal)
        canvas.setLineWidth(2)
        canvas.line(margin, page_height - 31, page_width - margin, page_height - 31)
        canvas.setFont('Watcher', 7)
        canvas.setFillColor(muted)
        canvas.drawString(margin, 26, 'Tire Technology Watcher | Full weekly report')
        canvas.drawRightString(page_width - margin, 26, f'Page {document.page}')
        canvas.restoreState()

    temporary = path.with_suffix('.tmp.pdf')
    doc.filename = str(temporary)
    try:
        doc.build(story, onFirstPage=page_frame, onLaterPages=page_frame)
        with temporary.open('rb') as stream:
            valid_header = stream.read(5) == b'%PDF-'
        if temporary.stat().st_size < 1000 or not valid_header:
            raise ERROR('PDF creation did not produce a valid report. Nothing was sent.')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def write_preview(brief, cfg, output):
    output.mkdir(parents=True, exist_ok=True)
    preview = output / 'weekly_brief_preview.html'
    preview.write_text(brief.digest.html, encoding='utf-8')
    (output / 'Full_weekly_report.html').write_text(brief.full_html, encoding='utf-8')
    report_path = write_full_pdf(brief, cfg, output / 'Full_weekly_report.pdf')
    mail = EmailMessage(policy=SMTP)
    mail['Subject'], mail['From'], mail['To'] = brief.digest.subject, cfg['mailbox'], ', '.join(cfg['to'])
    if cfg['cc']:
        mail['Cc'] = ', '.join(cfg['cc'])
    mail['X-Unsent'] = '1'
    mail.set_content(brief.digest.text)
    mail.add_alternative(brief.digest.html, subtype='html')
    mail.add_attachment(report_path.read_bytes(), maintype='application', subtype='pdf', filename=report_path.name)
    (output / 'weekly_brief_preview.eml').write_bytes(mail.as_bytes())
    (output / 'brief_report.json').write_text(json.dumps({
        'subject': brief.digest.subject, 'to': cfg['to'], 'cc': cfg['cc'],
        'source_emails': len(brief.digest.sources), 'unique_articles': len(brief.articles),
        'featured_updates': min(len(brief.articles), item_limits(cfg)[0]),
        'additional_updates': min(max(0, len(brief.articles) - item_limits(cfg)[0]), item_limits(cfg)[1]),
        'attachment': report_path.name, 'layout_version': LAYOUT_VERSION,
        'repeated_articles_removed_from_brief': brief.repeated_articles,
        'missing_receipt_dates': brief.digest.missing_dates,
        'parsing_notes': brief.notes,
        'article_titles': [a.title for a in brief.articles],
        'note': 'All selected daily content remains in the full report, including repeated coverage.'
    }, indent=2, ensure_ascii=False), encoding='utf-8')
    return preview, report_path


def recipient_smtp(recipient):
    entry = recipient.AddressEntry
    if str(entry.Type).upper() == 'EX':
        user = entry.GetExchangeUser()
        if user and user.PrimarySmtpAddress:
            return str(user.PrimarySmtpAddress).lower()
        return str(entry.PropertyAccessor.GetProperty('http://schemas.microsoft.com/mapi/proptag/0x39FE001E')).lower()
    return str(entry.Address or recipient.Address).lower()


def verify_mail(item, cfg):
    if item.SendUsingAccount is None or str(item.SendUsingAccount.SmtpAddress).lower() != cfg['mailbox'].lower():
        raise ERROR('Official From account could not be verified. Nothing was sent.')
    if not item.Recipients.ResolveAll():
        raise ERROR('Outlook could not resolve every recipient. Nothing was sent.')
    expected = sorted((kind, address.lower()) for kind, key in ((1, 'to'), (2, 'cc')) for address in cfg[key])
    actual = sorted((int(r.Type), recipient_smtp(r)) for r in item.Recipients)
    if actual != expected:
        raise ERROR('Outlook resolved a recipient to a different address. Check the draft; nothing was sent.')


def make_mail(client, brief, cfg, attachment):
    item = client.drafts.Items.Add('IPM.Note')
    item.SendUsingAccount = client.account
    if item.SendUsingAccount is None or str(item.SendUsingAccount.SmtpAddress).lower() != cfg['mailbox'].lower():
        item._oleobj_.Invoke(64209, 0, 8, 0, client.account)
    item.Subject, item.BodyFormat, item.HTMLBody = brief.digest.subject, 2, brief.digest.html
    item.SaveSentMessageFolder = client.store.GetDefaultFolder(5)
    for kind, key in ((1, 'to'), (2, 'cc')):
        for address in cfg[key]:
            item.Recipients.Add(address).Type = kind
    item.UserProperties.Add(MARKER, 1, False).Value = brief.digest.key
    item.UserProperties.Add(LAYOUT_MARKER, 1, False).Value = DRAFT_VERSION
    item.Attachments.Add(str(attachment.resolve()))
    verify_mail(item, cfg)
    item.Save()
    if str(item.Parent.StoreID) != str(client.store.StoreID):
        raise ERROR('The saved draft is in an unexpected mailbox. Nothing was sent.')
    verify_mail(item, cfg)
    return item


def matching_mail(client, folder_id, digest, require_brief_marker=False):
    # Restriction is on the full fixed subject, with Outlook literal escaping.
    subject = digest.subject.replace("'", "''")
    items = client.store.GetDefaultFolder(folder_id).Items.Restrict(f"[Subject] = '{subject}'")
    item = items.GetFirst()
    while item is not None:
        if not require_brief_marker:
            return item
        week_marker = item.UserProperties.Find(MARKER)
        layout_marker = item.UserProperties.Find(LAYOUT_MARKER)
        if (week_marker is not None and str(week_marker.Value) == digest.key
                and layout_marker is not None and str(layout_marker.Value) == DRAFT_VERSION):
            return item
        item = items.GetNext()
    return None


class SendJournal:
    """Persist a claim before Outlook.Send. Never automatically retry an attempt."""
    def __init__(self, path):
        self.connection = sqlite3.connect(str(path), timeout=10)
        self.connection.execute('CREATE TABLE IF NOT EXISTS sends (week_key TEXT PRIMARY KEY, state TEXT NOT NULL, updated TEXT NOT NULL)')
        self.connection.commit()

    def claim(self, key):
        try:
            self.connection.execute('INSERT INTO sends VALUES (?, ?, ?)', (key, 'preparing', datetime.now(core.IST).isoformat()))
            self.connection.commit()
        except sqlite3.IntegrityError:
            state = self.connection.execute('SELECT state FROM sends WHERE week_key=?', (key,)).fetchone()[0]
            raise ERROR(f'This week already has a send record ({state}). No second email was sent. Check Sent Items and Outbox.') from None

    def set(self, key, state):
        self.connection.execute('UPDATE sends SET state=?, updated=? WHERE week_key=?', (state, datetime.now(core.IST).isoformat(), key))
        self.connection.commit()

    def release_preparing(self, key):
        self.connection.execute("DELETE FROM sends WHERE week_key=? AND state='preparing'", (key,))
        self.connection.commit()

    def close(self):
        self.connection.close()


def submit_weekly(client, brief, cfg, attachment, journal_path):
    for folder in (4, 5):
        if matching_mail(client, folder, brief.digest) is not None:
            return 'This weekly subject is already in Outbox or Sent Items. No second email was sent.'
    journal = SendJournal(journal_path)
    key, attempted, claimed = brief.digest.key, False, False
    try:
        journal.claim(key)
        claimed = True
        item = make_mail(client, brief, cfg, attachment)
        # Persist before calling Outlook. A crash leaves a hold, not a resend.
        journal.set(key, 'uncertain')
        attempted = True
        item.Send()
        journal.set(key, 'submitted')
        return 'Weekly brief submitted to Outlook for delivery. Check Sent Items for delivery progress.'
    except Exception:
        if claimed and not attempted:
            journal.release_preparing(key)
        raise
    finally:
        journal.close()


def scheduled_week(cfg, now=None):
    now = (now or datetime.now(core.IST)).astimezone(core.IST)
    if not cfg.get('automatic_sending_enabled'):
        raise ERROR('Automatic sending is disabled. No email was sent.')
    try:
        first_due = datetime.fromisoformat(cfg.get('first_scheduled_run') or '')
    except ValueError:
        raise ERROR('Install the Monday task through this program before using automatic sending.') from None
    if first_due.tzinfo is None:
        raise ERROR('first_scheduled_run must include a timezone.')
    monday = now.date() - timedelta(days=now.weekday())
    due = datetime.combine(monday, time(9), tzinfo=core.IST)
    if now < first_due or now < due:
        raise ERROR('The Monday 09:00 IST send time has not arrived. No email was sent.')
    return core.previous_week(now)


def install_schedule(config_path, cfg, enabled=True):
    if sys.platform != 'win32':
        raise ERROR('Task installation requires your Windows PC.')
    if not enabled:
        cfg['automatic_sending_enabled'] = False
        write_config(config_path, cfg)
    import pythoncom
    import win32com.client
    import winreg
    pythoncom.CoInitialize()
    try:
        service = win32com.client.Dispatch('Schedule.Service')
        service.Connect()
        folder = service.GetFolder('\\')
        if not enabled:
            try:
                folder.GetTask(TASK_NAME).Enabled = False
            except Exception:
                return 'Automatic sending is disabled in the configuration. Check Task Scheduler if its task still appears enabled.'
            return 'Monday automatic sending is disabled.'
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\TimeZoneInformation') as key:
            zone = winreg.QueryValueEx(key, 'TimeZoneKeyName')[0].rstrip('\x00')
        if zone != 'India Standard Time':
            raise ERROR('Set Windows time zone to (UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi before installing the 09:00 IST task.')
        now = datetime.now(core.IST)
        next_monday = now.date() + timedelta(days=(7 - now.weekday()) % 7)
        due = datetime.combine(next_monday, time(9), tzinfo=core.IST)
        if due <= now:
            due += timedelta(days=7)
        task = service.NewTask(0)
        task.RegistrationInfo.Description = 'Compile the prior Monday–Sunday Watcher emails and send a concise brief from the official Outlook account.'
        # Resolve the actual process account; environment names may be stale or
        # unsuitable for domain / Entra sign-ins. No password is requested.
        import win32api
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), 0x0008)
        try:
            sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
            identity = win32security.ConvertSidToStringSid(sid)
        finally:
            token.Close()
        task.Principal.UserId = identity
        task.Principal.LogonType = 3  # TASK_LOGON_INTERACTIVE_TOKEN: signed-in user, no password stored.
        task.Principal.RunLevel = 0
        trigger = task.Triggers.Create(3)  # TASK_TRIGGER_WEEKLY
        trigger.StartBoundary = due.isoformat()
        trigger.DaysOfWeek, trigger.WeeksInterval, trigger.Enabled = 2, 1, True
        settings = task.Settings
        settings.Enabled, settings.StartWhenAvailable, settings.WakeToRun = True, True, True
        settings.DisallowStartIfOnBatteries, settings.StopIfGoingOnBatteries = False, False
        settings.MultipleInstances, settings.ExecutionTimeLimit = 2, 'PT15M'
        settings.RestartCount, settings.RestartInterval = 3, 'PT15M'
        action = task.Actions.Create(0)
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        action.Path = str(pythonw if pythonw.exists() else Path(sys.executable))
        action.Arguments = subprocess.list2cmdline([str(Path(__file__).resolve()), '--config', str(config_path.resolve()), '--send-weekly'])
        action.WorkingDirectory = str(BASE)
        cfg['automatic_sending_enabled'] = False
        write_config(config_path, cfg)
        folder.RegisterTaskDefinition(TASK_NAME, task, 6, identity, None, 3)
        cfg['automatic_sending_enabled'] = True
        cfg['first_scheduled_run'] = due.isoformat()
        write_config(config_path, cfg)
        return f'Monday 09:00 IST sending enabled. First run: {due:%d %b %Y, %H:%M}. Windows must be signed in and the PC available.'
    finally:
        pythoncom.CoUninitialize()


def run(config_path, mode, week=None, sample=None, open_preview=True):
    cfg = read_config(config_path)
    if mode in ('enable', 'disable'):
        return install_schedule(config_path, cfg, mode == 'enable')
    if mode == 'send-weekly':
        if sample:
            raise ERROR('Sample files cannot be used for automatic sending.')
        week = scheduled_week(cfg)
    start, end = core.week_bounds(week or core.previous_week())
    client = None
    try:
        if sample:
            if mode != 'preview':
                raise ERROR('Sample files support preview mode only.')
            messages = core.load_sample(sample)
        else:
            client = core.OutlookDesktop(cfg)
            messages = client.read_messages(start, end)
        brief = compile_brief(messages, cfg, start, end, sample=bool(sample))
        if mode == 'self-test':
            cfg = dict(cfg, to=[cfg['mailbox']], cc=[])
            brief.digest.subject = '[TEST TO MYSELF] ' + brief.digest.subject
        preview, attachment = write_preview(brief, cfg, BASE / 'weekly_previews' / str(start.date()))
        if mode == 'preview':
            if open_preview:
                webbrowser.open(preview.as_uri())
            return f'Preview ready: {preview}. {len(brief.articles)} developments; {len(brief.digest.sources)} daily email(s).'
        if mode == 'send-weekly':
            return submit_weekly(client, brief, cfg, attachment, BASE / 'weekly_send_history.sqlite3')
        if mode == 'draft':
            existing = matching_mail(client, 16, brief.digest, require_brief_marker=True)
            if existing is not None:
                existing.Display(False)
                return 'Existing draft for this week and layout opened, preserving your edits. The latest compilation is available through Preview.'
        item = make_mail(client, brief, cfg, attachment)
        if mode == 'self-test':
            item.Send()
            return f'Test submitted to Outlook, addressed only to {cfg["mailbox"]}. The Monday send record is unchanged.'
        item.Display(False)
        return f'Draft created with {len(cfg["to"])} To recipients and the full PDF report attached. Review it in Outlook.'
    finally:
        if client:
            client.close()


def gui(config_path):
    import queue
    import threading
    import tkinter as tk
    from tkinter import ttk, messagebox
    cfg = read_config(config_path)
    root = tk.Tk()
    root.title('Tire Technology Watcher — Weekly Brief ' + DRAFT_VERSION)
    root.geometry('850x650')
    root.minsize(760, 600)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Your weekly technology brief', font=('Segoe UI', 20, 'bold')).pack(anchor='w')
    featured, additional = item_limits(cfg)
    ttk.Label(frame, text=f'Up to {featured} priority updates + {additional} quick reads. Full weekly report attached as a PDF.', wraplength=740).pack(anchor='w', pady=(6, 18))
    ttk.Label(frame, text='From: ' + cfg['mailbox']).pack(anchor='w')
    ttk.Label(frame, text='To (' + str(len(cfg['to'])) + '): ' + '; '.join(cfg['to']), wraplength=750).pack(anchor='w', pady=8)
    ttk.Label(frame, text='Cc: ' + ('; '.join(cfg['cc']) or '(none)')).pack(anchor='w')
    week = tk.StringVar(value=str(core.previous_week()))
    row = ttk.Frame(frame)
    row.pack(anchor='w', pady=18)
    ttk.Label(row, text='Week starting Monday (YYYY-MM-DD): ').pack(side='left')
    ttk.Entry(row, textvariable=week, width=15).pack(side='left')
    ttk.Label(frame, text='Preview and draft use the week above. Automatic sending always uses the previous completed Monday–Sunday week.', wraplength=750).pack(anchor='w')
    controls = ttk.Frame(frame)
    controls.pack(anchor='w', pady=18)
    schedule_controls = ttk.Frame(frame)
    schedule_controls.pack(anchor='w', pady=(0, 14))
    state = 'ENABLED' if cfg.get('automatic_sending_enabled') else 'DISABLED'
    status = tk.StringVar(value=f'Automatic sending: {state}. Opening this program does not send email.')
    ttk.Label(frame, textvariable=status, wraplength=750, justify='left').pack(anchor='w', pady=12)
    ttk.Label(frame, text='Keep this folder in its final location. Scheduled runs need Windows signed in, classic Outlook synchronized, and the PC on or able to wake. Logs: tire_watcher_weekly.log', wraplength=750).pack(anchor='w', pady=10)
    buttons, results = [], queue.Queue()

    def start(mode):
        chosen_week = week.get().strip()
        try:
            if mode not in ('enable', 'disable'):
                _, end = core.week_bounds(chosen_week)
                if end > datetime.now(core.IST) and not messagebox.askyesno('Week still in progress', 'Compile the emails received so far for this week?'):
                    return
            if mode == 'self-test' and not messagebox.askyesno('Send a test to yourself', f'Send this brief only to {cfg["mailbox"]}?'):
                return
            if mode == 'enable' and not messagebox.askyesno('Enable Monday sending', 'Enable automatic sending every Monday at 09:00 IST to the displayed recipient list?\n\nThis installs a Windows scheduled task. It does not send immediately.'):
                return
        except Exception as exc:
            messagebox.showerror('Check the week', str(exc))
            return
        for button in buttons:
            button.config(state='disabled')
        status.set('Working…')
        def worker():
            try:
                results.put((True, run(config_path, mode, chosen_week)))
            except Exception as exc:
                logging.exception('Operation failed: %s', mode)
                results.put((False, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    for label, mode, parent in [('Preview in browser', 'preview', controls), ('Create / open draft', 'draft', controls),
                                ('Send test to myself', 'self-test', controls), ('Enable Monday 9 AM', 'enable', schedule_controls),
                                ('Disable automatic sending', 'disable', schedule_controls)]:
        button = ttk.Button(parent, text=label, command=lambda m=mode: start(m))
        button.pack(side='left', padx=(0, 10))
        buttons.append(button)

    def poll():
        try:
            ok, result = results.get_nowait()
            status.set(result)
            for button in buttons:
                button.config(state='normal')
            if not ok:
                messagebox.showerror('Could not finish', result)
        except queue.Empty:
            pass
        root.after(150, poll)
    root.after(150, poll)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--week', help='Monday date YYYY-MM-DD, for preview/draft/test only')
    parser.add_argument('--sample', type=Path)
    parser.add_argument('--no-open', action='store_true')
    actions = parser.add_mutually_exclusive_group()
    for mode in ('preview', 'draft', 'self-test', 'send-weekly', 'enable', 'disable'):
        actions.add_argument('--' + mode, action='store_true')
    args = parser.parse_args()
    logging.basicConfig(filename=BASE / 'tire_watcher_weekly.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    mode = next((m for m in ('preview', 'draft', 'self-test', 'send-weekly', 'enable', 'disable')
                 if getattr(args, m.replace('-', '_'))), None)
    try:
        if mode:
            result = run(args.config.resolve(), mode, args.week, args.sample, not args.no_open)
            logging.info(result)
            if sys.stdout:
                print(result)
        else:
            gui(args.config.resolve())
        return 0
    except Exception as exc:
        logging.exception('Weekly brief failed')
        if sys.stderr:
            print(str(exc), file=sys.stderr)
        if not mode:
            import tkinter.messagebox
            tkinter.messagebox.showerror('Tire Technology Watcher', str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
