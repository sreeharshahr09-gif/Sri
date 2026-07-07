# Monthly Patent Report

`monthly_patent_report.ipynb` builds the monthly tire-patent analysis report
(PatSeer export → classification → charts → Word report).

## Every month, change only this (Cell 1)

```python
TARGET_MONTH = pd.Period("2026-06", freq="M")
REPORT_BASE_DIR = r"C:\Users\...\Patent_Monthly_report"
AUTHOR = "..."
MAX_NOTABLE_PATENTS = 25   # hard ceiling on patents that get PDF drawings/links
```

`MAX_NOTABLE_PATENTS` is your monthly PDF-handling capacity: however busy
the month, at most this many patents are selected (Top-5 Interest and
Top-5 Novelty always kept, remaining slots filled by Interest Score) for
PDF download, drawing embedding, and folder links. The notebook prints
how many candidates the cap dropped so you can raise it consciously.

`MONTH_NAME`, `MONTH_FOLDER`, `FILE_DIR`, `FILE_NAME`, and `PDF_FOLDER_LINK`
are all derived from `TARGET_MONTH` — do not edit them directly, or they can
silently drift out of sync with the data again (this happened with the
May/June 2026 report).

The notebook expects:

```
REPORT_BASE_DIR/
  <MonthYYYY>/                       e.g. June2026/
    Patent_report_<MonthYYYY>.xlsx   e.g. Patent_report_June2026.xlsx
    Downloaded_Patent_PDFs/          local PDF copies for figure extraction
```

## PatSeer query (source of record)

`patseer_query.txt` is the frozen search query that produces the monthly
export — paste it into PatSeer as-is after editing **only** the `PBD:[...]`
date line to the target month. Do not tweak terms casually: volume and
trend charts are only comparable month-over-month if the query stays
stable. If the query must change, commit the new version here with a note
of what changed and why, and expect a step change in monthly counts.

The query is stored in pairwise-nested form (`((((TAC AND FT) AND CPC)
AND PBD) AND LSN)`) because PatSeer's validator rejects a flat chain of
`AND`s between the field blocks ("Invalid search query! You have used
either (OR with AND)...").

## Safety guard

Each Section-5 patent card starts on a fresh page (`SECTION5_ONE_CARD_PER_PAGE`
in Cell 1) and its Abstract/Claims are budgeted so a card never exceeds one
page; lower the two `SECTION5_*_MAX_CHARS` values if a card ever looks tight.

## Safety guard note

After cleaning, the notebook checks that the publication dates in the
export actually fall inside `TARGET_MONTH` and raises an error if none do
(catches loading the wrong month's file or forgetting to update
`TARGET_MONTH`). If only some rows fall outside the target month it prints a
warning and continues.

## Downloading notable-patent PDFs (Cell 12)

Cell 12 downloads the actual patent PDFs (from the export's "PDF Link"
column) for the patents shown in Section 4, into
`Downloaded_Patent_PDFs/`, named by Record Number. A later Run All then
embeds their drawing pages into the Section-5 cards.

- Run it on a machine logged into PatSeer.
- `DOWNLOAD_SCOPE`: `"section4"` (default — shortlist + each thematic
  table's top-N), `"notable25"` (shortlist only), or `"all"`.
- The PatSeer PDF URLs open the file directly in a browser, so a plain
  request usually works. If you get HTTP 401/403, paste your PatSeer
  session cookie into `PATSEER_COOKIE` (browser DevTools -> Network ->
  any request -> copy the `Cookie:` header) and re-run.
- The cell skips PDFs already present, validates each is a real PDF, and
  prints a success/failure summary.

Two-pass workflow: Run All (report + this cell downloads PDFs) -> Run
All again (drawings now embed into Section 5).

## Known follow-up work (not yet implemented)

See project history for the full roadmap. Not done in this pass:
- Persistent cross-month master dataset (cumulative charts are still driven
  by hand-transcribed values in the last cell).
- Patent-family deduplication using `Simple Family ID` / `Extended Family ID`
  (present in the PatSeer export, unused).
- CPC/IPC-code-assisted classification (present in the export, unused).
- Assignee alias table / taxonomy restructuring.
