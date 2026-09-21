#!/usr/bin/env python3
"""Format-tolerance tests for the weekly brief parser.

These run offline. They assert the two properties that matter for automation:
every daily email is read, and a day without updates never stops the week.

    python -m unittest test_outlook_formats -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import re
import unittest

import tire_watcher_draft as core
import tire_watcher_weekly as weekly

SRC = 'https://www.example.com/article-one'


def outlook_plain_html(body):
    """Reproduce Outlook's HTMLBody for a plain-text mail: one <p> per wrapped line."""
    def line(raw):
        if not raw.strip():
            return '<p class=MsoPlainText><o:p>&nbsp;</o:p></p>'
        parts, cursor = [], 0
        for match in re.finditer(r'https?://[^\s<>]+', raw):
            parts.append(html.escape(raw[cursor:match.start()]))
            url = match.group(0)
            parts.append(f'<a href="{html.escape(url, True)}">{html.escape(url)}</a>')
            cursor = match.end()
        parts.append(html.escape(raw[cursor:]))
        return '<p class=MsoPlainText>' + ''.join(parts) + '<o:p></o:p></p>'
    return ('<html xmlns:o="urn:schemas-microsoft-com:office:office"><head>'
            '<meta name=Generator content="Microsoft Word 15 (filtered medium)"></head>'
            '<body lang=EN-IN><div class=WordSection1>'
            + '\n'.join(line(l) for l in body.split('\n')) + '</div></body></html>')


def mail(body, day=21, subject=None):
    return core.SourceMail(f'uid{day}', subject or f'Tire Technology Watcher — {day} September 2026',
                           datetime(2026, 9, day, 4, 30, tzinfo=timezone.utc),
                           'sreeharsha.hr09@gmail.com', outlook_plain_html(body), 0)


ITEM = f"""Top developments

1) Hankook closes IAA Transportation 2026 with three new TBR products
— Relevance 8.6/10
Segment: TBR
Technology topic: Winter performance | Trailer efficiency
Development: Hankook reported on 21 September that it unveiled three new
truck/bus tires at IAA Transportation 2026.
Why it matters: This reinforces two developing competitive themes.
Suggested follow-up: Benchmark the DW53/AW53 worn-state groove concept.
Source: {SRC}
"""

NO_UPDATES = """Tire Technology Watcher — 21 September 2026
Scope: TBR | PCR | OHT | 2E | Patents excluded

Top developments
No new developments exceeded the relevance threshold today.

Event / learning scan
No newly discovered guest lecture, webinar or workshop exceeded the threshold.

Developing theme
The strongest continuing TBR signal is the move toward lifecycle performance.
"""


class ParsesRealLayouts(unittest.TestCase):
    def test_wrapped_title_and_labelled_development(self):
        articles, notes = weekly.extract_articles(mail(ITEM))
        self.assertEqual(len(articles), 1)
        self.assertEqual(notes, [])
        article = articles[0]
        self.assertEqual(article.score, 8.6)
        self.assertEqual(article.title, 'Hankook closes IAA Transportation 2026 with three new TBR products')
        # The visible summary must be the Development sentence, not a label line.
        self.assertTrue(article.lead.startswith('Hankook reported on 21 September'))
        for label in ('Technology topic:', 'Development:', 'Segment:'):
            self.assertNotIn(label, article.lead)
        self.assertIn(SRC, article.links)

    def test_numbered_with_period_and_relevance_score_label(self):
        body = ITEM.replace('1)', '1.').replace('— Relevance 8.6/10', 'Relevance score: 8.6 / 10')
        articles, _ = weekly.extract_articles(mail(body))
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].score, 8.6)

    def test_item_without_any_relevance_score(self):
        body = ITEM.replace('\n— Relevance 8.6/10', '')
        articles, _ = weekly.extract_articles(mail(body))
        self.assertEqual(len(articles), 1)
        self.assertIsNone(articles[0].score)

    def test_closing_sections_are_not_articles(self):
        body = ITEM + '\nEvent / learning scan\nNothing exceeded the threshold.\n' \
                      '\nDeveloping theme\nLifecycle performance keeps rising.\n'
        articles, _ = weekly.extract_articles(mail(body))
        self.assertEqual(len(articles), 1)


class NoUpdateEmails(unittest.TestCase):
    def test_no_update_email_is_read_and_reported_not_rejected(self):
        articles, notes = weekly.extract_articles(mail(NO_UPDATES))
        self.assertEqual(articles, [])
        self.assertTrue(notes, 'a no-update email must produce an explanatory note')
        self.assertIn('No updates were recognized', notes[0])

    def test_no_update_day_does_not_stop_the_week(self):
        cfg = dict(core.DEFAULTS, to=['shreeharsha.r@apollotyres.com'], cc=[],
                   layout_version=2, featured_items=10, additional_items=10)
        cfg = core.validate_config(cfg)
        start = datetime(2026, 9, 14, tzinfo=core.IST)
        end = start + timedelta(days=7)
        messages = [mail(ITEM.replace('Hankook', f'Brand{day}'), day) for day in (14, 15, 16)]
        messages.append(mail(NO_UPDATES, 17))
        brief = weekly.compile_brief(messages, cfg, start, end)
        self.assertEqual(len(brief.articles), 3)
        self.assertEqual(len(brief.digest.sources), 4, 'every email stays in the PDF')
        self.assertTrue(brief.notes)

    def test_week_where_no_day_had_updates_still_compiles(self):
        cfg = core.validate_config(dict(core.DEFAULTS, to=['shreeharsha.r@apollotyres.com'], cc=[],
                                        layout_version=2, featured_items=10, additional_items=10))
        start = datetime(2026, 9, 14, tzinfo=core.IST)
        brief = weekly.compile_brief([mail(NO_UPDATES, 14), mail(NO_UPDATES, 15)],
                                     cfg, start, start + timedelta(days=7))
        self.assertEqual(brief.articles, [])
        self.assertIn('No new updates this week', brief.digest.html)


class RichHtmlStillWorks(unittest.TestCase):
    RICH = ('<html><body><div><h2>Top developments</h2>'
            '<p><b>1. Goodyear scales SoyFoam tread compound &mdash; Relevance 9.1/10</b></p>'
            '<p>Segment: PCR</p><p>Topic: Sustainable materials</p>'
            '<p>What is new: Goodyear confirmed volume production of a soy-oil tread compound.</p>'
            '<p>Why it matters: Bio-based plasticizers shift the wet-grip balance.</p>'
            '<p>Source: <a href="https://example.com/soyfoam">Read more</a></p>'
            '<h2>Developing themes</h2><p>Sustainable materials continue to dominate.</p>'
            '</div></body></html>')

    def test_html_newsletter_layout(self):
        message = core.SourceMail('rich', 'Tire Technology Watcher — 18 September 2026',
                                  datetime(2026, 9, 18, 5, 0, tzinfo=timezone.utc),
                                  'sreeharsha.hr09@gmail.com', self.RICH, 0)
        articles, notes = weekly.extract_articles(message)
        self.assertEqual(len(articles), 1)
        self.assertEqual(notes, [])
        self.assertEqual(articles[0].score, 9.1)
        self.assertEqual(articles[0].title, 'Goodyear scales SoyFoam tread compound')
        self.assertTrue(articles[0].lead.startswith('Goodyear confirmed volume production'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
