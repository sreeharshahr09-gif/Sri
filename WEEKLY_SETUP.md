# Tire Technology Watcher — concise weekly brief

The email now has **10 priority updates plus up to 10 additional updates**, short source links and a navy/teal design with category colors. Up to **20 developments** appear in the email. The attached **Full_weekly_report.pdf** contains all selected daily email content, technical notes, suggested follow-ups and clickable source links. Its pages are numbered, and PDF bookmarks let you jump to daily editions and articles.

Your eight supplied addresses are in To. Cc is empty. From is `shreeharsha.r@apollotyres.com`.

## Start with the updated draft

**If you already use the weekly program:** close its window, replace only `tire_watcher_weekly.py` in its current folder, and install ReportLab using the command below. Keep your current `tire_watcher_weekly_config.json` and `weekly_send_history.sqlite3`. The program upgrades older layout settings to 10+10 automatically when reading them, while preserving your recipients and automatic-send settings. You do not need to re-enable a task already pointing to this same program path. The old configuration file may still show `max_items_in_email: 9` until settings are saved; the upgraded program uses 10+10. A completed week's send record stays completed, so the layout update does not trigger a resend.

**For a fresh installation:**

1. Save `tire_watcher_weekly.py` and `tire_watcher_weekly_config.json` in the **same folder as your working `tire_watcher_draft.py`**. Keep all three filenames exactly as shown. The existing `config.json` belongs to the original program; the weekly program uses its own configuration. The supplied fresh configuration has automatic sending disabled.
2. Open classic Outlook and wait for your Inbox to synchronize.
3. Run `tire_watcher_weekly.py` using the same Python / Anaconda environment that worked before. From Anaconda Prompt, change into your folder and run:

   ```text
   python tire_watcher_weekly.py
   ```

4. Select the Monday date for the week you want. For example, `2026-09-07` covers 7–13 September. Selecting a week still in progress asks whether to compile the emails received so far.
5. Click **Preview in browser** to see the new layout, or **Create / open draft** to review it in Outlook with the full PDF attached. Earlier drafts are preserved; the first run of this layout creates its own revised draft. Subsequent runs reopen an existing draft for this layout and week to preserve your edits.
6. **Send test to myself** sends only to your official address. It clears Cc and does not count as the team's weekly email.

Opening the program does not send email. You can continue using drafts only.

**One new package is required: ReportLab.** In the same Anaconda environment that runs the existing program, install it once:

```text
python -m pip install "reportlab>=4.4.9,<5"
```

The full dependencies are `beautifulsoup4`, `pywin32` and `reportlab`. PDF creation happens locally and needs no Word installation, browser automation, LLM or cloud account. If setting up a new environment, use the updated requirements file:

```text
python -m pip install -r tire_watcher_requirements.txt
```

Requires Python 3.10+, classic Outlook on Windows, and the official mailbox configured as an Outlook account. The new Outlook app does not provide the COM interface used by this program.

## Enable Monday 9 AM sending when ready

Place the folder in its permanent location first. Disable any earlier Watcher task that could send the same weekly email; leave unrelated tasks alone.

1. Check the preview, From and recipient list.
2. Set Windows time zone to **(UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi**.
3. Click **Enable Monday 9 AM**. The confirmation lists what will be enabled. It creates the Windows task **Tire Technology Watcher - Weekly Brief**, then shows its first scheduled run.
4. The first run is the next future Monday at 09:00 IST. It compiles the preceding Monday 00:00 through the following Monday 00:00, based on received time in IST, then submits the brief through your official Outlook account.

The task runs as your signed-in Windows user, with ordinary permissions and without saving your password. It requests wake-up and a late start if a scheduled run was missed. Wake-up depends on your PC's power settings and hardware. It cannot turn on a shut-down PC. Keep Windows signed in and Outlook synchronized; keeping Outlook open is the most reliable setup. A locked signed-in session can run the task. Organizational settings may prevent task creation or Outlook automation; the program reports the failure rather than bypassing those settings.

If the PC becomes available on Tuesday, the task can send that Monday's digest late. It does not send a backlog of several missed weeks. A retry before Monday 09:00 is blocked. Task Scheduler may retry an unsuccessful run three times, 15 minutes apart.

Click **Disable automatic sending** to stop future runs. If you move the folder or change Python environments, disable the old task and re-enable it from the new location. Do not copy the program to a second PC and enable both schedules.

This download prepares the scheduler installer; no task has been installed on your PC by ChatGPT.

## How the content stays short

- Up to 10 priority cards lead with an update and a short “Why it matters” excerpt.
- Up to 10 additional items appear below them, numbered 11–20. Fewer available articles produce fewer entries; the program never fills unused slots with repeated or invented content.
- Every matching daily email is read; the number of incoming updates is not capped at 20. For example, 63 unique updates produce 20 email entries, and all 63 are included in the PDF. The full report retains repeated coverage from different editions as well.
- In a configuration with `layout_version: 2`, the optional `featured_items` and `additional_items` settings control the two sections (defaults 10 and 10; supported ranges 1–25 and 0–25). `max_items_in_email` is derived from their sum; changing only that older field has no effect.
- Priority follows relevance scores already supplied in the source emails. Ties use receipt time. Unscored items follow scored ones, ordered by receipt time. These are not newly calculated technical rankings.
- Summaries extract the first sentence, with a word limit and visible ellipses. They do not use an AI service, independently verify claims, or upload your mailbox content to one.
- Exact repeated articles are removed from the short brief; changed coverage remains. The PDF preserves the readable content of every selected daily email body, including recurring themes and actions, reformatted for print. Original source attachments are referenced but are not copied. Remote images are excluded as in the original compiler. A full HTML copy is also retained locally.
- Microsoft Safe Links are retained in clickable URLs. Remote graphics and trackers are excluded; the design uses email tables and colors.
- Every matching daily email is compiled, whether or not it carries updates. A day that reports nothing above the relevance threshold, or whose layout has drifted, no longer stops the week: it contributes no cards, its full text still appears in the PDF, and a short note naming that email appears in the email footer, in the PDF and under `parsing_notes` in `brief_report.json`. A week in which no day carried updates produces a brief headed **No new updates this week** with the PDF attached.
- Recognized item labels include `Segment`, `Topic`, `Technology topic`, `Development`, `What is new`, `Why it matters`, `Suggested follow-up`, `Background`, `Scope` and `Source(s)`. The visible summary is taken from the `Development` / `What is new` sentence when present, so label text never leaks into the card. Titles numbered `1.` or `1)`, scores written `Relevance 8.6/10` or `Relevance score: 8.6 / 10`, and titles wrapped across lines by Outlook are all read. Closing sections (`Developing theme(s)`, `Event / learning scan`, `Priority actions`, `Key takeaways`, `Patent scan`) end the article list instead of being mistaken for updates.
- Run `python -m unittest test_outlook_formats -v` to re-check all of the above offline after any change to the daily email format.

Only available, matching emails are included. Missing receipt dates are listed in the footer and full report. The program cannot establish that Outlook has downloaded every expected message; keep it synchronized before the scheduled run.

## Recipient changes

Close the GUI before editing `tire_watcher_weekly_config.json`. The `to` array has your eight addresses; `cc` is empty. Save valid JSON and reopen the program to check the displayed list. Every run reads the file again. Outlook resolves each address; if it resolves to an unexpected SMTP address, sending stops for review. No Bcc recipients are added.

## Duplicate protection and troubleshooting

The program checks the official Outbox and Sent Items for the same weekly subject, and keeps a local `weekly_send_history.sqlite3` journal beside the program. Concurrent runs can claim a week only once. A successful `Send()` call means **submitted to Outlook**, not confirmed delivery; check Sent Items, Outbox and any nondelivery notification.

If an error occurs after a send attempt starts, the week is held as `uncertain` and will not be automatically retried. A process interruption during preparation can similarly leave a `preparing` hold. Check Sent Items and Outbox before taking any recovery action. Do not delete the journal just to retry: it provides duplicate protection. If you have confirmed that nothing was sent or queued, you can create a draft and send it manually after checking the recipients. The hold affects that week only; future weeks have separate records.

Automatic sending compiles a fresh email from the configured recipient list and source messages. Edits to a review draft are not incorporated into the automatic email. If you manually send a draft with the unchanged weekly subject, the Sent Items/Outbox check blocks another scheduled copy. Changing its subject prevents that particular check from matching it.

The journal protects one installation. It cannot detect another PC using a different local journal, a deleted Sent Items copy after a lost journal, or manual sends with a changed subject.

Runtime files beside the program:

- `tire_watcher_weekly.log`: status and error details.
- `weekly_send_history.sqlite3`: weekly send records; keep it.
- `weekly_previews/YYYY-MM-DD/`: HTML email preview, full PDF report, full HTML backup, unsent `.eml` preview with the PDF attached, and compilation information. These contain email content; store the program folder in your usual company-approved location.

The supplied HTML and PDF previews are **layout samples based on one daily email containing six updates**, not a completed seven-day digest. They therefore show six priority cards; a week with at least 20 unique updates uses the full 10+10 layout. A browser preview and Outlook can render some spacing differently. Use **Send test to myself** for the final visual check in your Outlook.

## Optional commands

```text
python tire_watcher_weekly.py --preview --week 2026-09-07
python tire_watcher_weekly.py --draft --week 2026-09-07
python tire_watcher_weekly.py --self-test --week 2026-09-07
python tire_watcher_weekly.py --enable
python tire_watcher_weekly.py --disable
```

`--send-weekly` is the scheduled command. Once enabled and due, it sends to the entire configured To/Cc list without a prompt. Do not use it as a preview test.

Offline sample preview and checks, if `sample_email.json` and the test files are present. The PDF checks also require `pypdf` (`python -m pip install pypdf`); normal program operation does not require it:

```text
python tire_watcher_weekly.py --preview --sample sample_email.json --week 2026-09-07 --no-open
python -m unittest test_tire_watcher test_tire_watcher_weekly -v
```

The offline tests do not connect to Outlook, send messages or install tasks. Live Outlook delivery and task registration must be checked on your Windows PC.

Microsoft references: [weekly trigger days](https://learn.microsoft.com/en-us/windows/win32/taskschd/weeklytrigger-daysofweek), [interactive logon task types](https://learn.microsoft.com/en-us/windows/win32/taskschd/principal-logontype), [MailItem.Send](https://learn.microsoft.com/en-us/office/vba/api/outlook.mailitem.send%28method%29).
