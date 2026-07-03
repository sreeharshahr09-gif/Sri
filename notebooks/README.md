# Monthly Patent Report

`monthly_patent_report.ipynb` builds the monthly tire-patent analysis report
(PatSeer export → classification → charts → Word report).

## Every month, change only this (Cell 1)

```python
TARGET_MONTH = pd.Period("2026-06", freq="M")
REPORT_BASE_DIR = r"C:\Users\...\Patent_Monthly_report"
AUTHOR = "..."
```

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

After cleaning, the notebook checks that the publication dates in the
export actually fall inside `TARGET_MONTH` and raises an error if none do
(catches loading the wrong month's file or forgetting to update
`TARGET_MONTH`). If only some rows fall outside the target month it prints a
warning and continues.

## Known follow-up work (not yet implemented)

See project history for the full roadmap. Not done in this pass:
- Persistent cross-month master dataset (cumulative charts are still driven
  by hand-transcribed values in the last cell).
- Patent-family deduplication using `Simple Family ID` / `Extended Family ID`
  (present in the PatSeer export, unused).
- CPC/IPC-code-assisted classification (present in the export, unused).
- Assignee alias table / taxonomy restructuring.
