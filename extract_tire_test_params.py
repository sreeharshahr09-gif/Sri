"""
Extract tire test parameters from Excel test files across an entire folder
tree, writing one consolidated file per subfolder.

Layout expected in each source file (on the first sheet):
    A2:A21    -> parameter names
    B2:B21    -> parameter values
The file name (without extension) is used as the column label.

How to use:
    1. Keep this script anywhere you like (only ONE copy needed).
    2. Run it:   python extract_tire_test_params.py
    3. A folder-picker window opens -> choose the PARENT folder that holds
       the subfolders of Excel test files.
    4. For every folder in the tree that contains valid test files, a
       "tire_test_parameters.xlsx" is created IN that same folder.

Each output file has two sheets:
    * "Tire Test Parameters" - one column per test case, one row per parameter.
    * "Report"               - per-file Success/Failure status and totals.
"""

import os
import sys
from openpyxl import load_workbook, Workbook

# Rows to read (1-based, matching Excel). A2:A21 / B2:B21 -> rows 2..21.
FIRST_DATA_ROW = 2
LAST_DATA_ROW = 21

OUTPUT_FILENAME = "tire_test_parameters.xlsx"


def choose_folder():
    """Open a folder-picker dialog and return the selected path (or None)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        # tkinter unavailable (rare) -> fall back to the current directory.
        print("Folder picker unavailable; using the current folder.")
        return os.getcwd()

    root = tk.Tk()
    root.withdraw()          # hide the empty root window
    root.attributes("-topmost", True)  # bring dialog to the front
    folder = filedialog.askdirectory(
        title="Select the PARENT folder containing the test subfolders")
    root.destroy()
    return folder or None    # returns "" if the user cancels


def extract_from_file(path):
    """Return (label, [(param_name, value), ...]) from one Excel file's sheet 1.

    Raises ValueError if the file does not match the expected structure
    (no parameter names found in A2:A21).
    """
    wb = load_workbook(path, data_only=True)
    ws = wb.worksheets[0]  # sheet 1

    # Use the file name (without extension) as the label.
    label = os.path.splitext(os.path.basename(path))[0]

    params = []
    for row in range(FIRST_DATA_ROW, LAST_DATA_ROW + 1):
        name = ws.cell(row=row, column=1).value   # column A
        value = ws.cell(row=row, column=2).value  # column B
        if name is None or str(name).strip() == "":
            continue
        params.append((str(name).strip(), value))

    wb.close()

    if not params:
        raise ValueError("No parameter names found in A2:A21")

    return str(label).strip(), params


def list_candidate_files(folder):
    """Return the Excel files in one folder, ignoring temp files and output."""
    files = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith((".xlsx", ".xlsm", ".xls")):
            continue
        if name.startswith("~$"):          # Excel lock/temp files
            continue
        if name == OUTPUT_FILENAME:        # don't re-ingest our own output
            continue
        files.append(os.path.join(folder, name))
    return files


def process_folder(folder):
    """Extract every valid file in one folder and write its output workbook.

    Returns (n_success, n_failure) or None if the folder has no candidate
    Excel files at all.
    """
    candidates = list_candidate_files(folder)
    if not candidates:
        return None

    cases = []            # list of (label, {param_name: value})
    param_order = []      # preserves the order parameters first appear
    report_rows = []      # list of (file_name, status, details)

    for path in candidates:
        fname = os.path.basename(path)
        try:
            label, params = extract_from_file(path)
        except Exception as exc:  # unreadable or wrong structure -> failure
            report_rows.append((fname, "Failure", str(exc)))
            continue

        param_map = {}
        for name, value in params:
            if name not in param_order:
                param_order.append(name)
            param_map[name] = value

        cases.append((label, param_map))
        report_rows.append((fname, "Success", "%d parameter(s)" % len(params)))

    n_success = len(cases)
    n_failure = len(report_rows) - n_success

    _write_output(folder, cases, param_order, report_rows, n_success, n_failure)
    return n_success, n_failure


def _write_output(folder, cases, param_order, report_rows, n_success, n_failure):
    """Write the consolidated data sheet and the report sheet to disk."""
    out_wb = Workbook()

    # --- Sheet 1: consolidated parameters ---
    data_ws = out_wb.active
    data_ws.title = "Tire Test Parameters"

    data_ws.cell(row=1, column=1, value="Parameter")
    for col, (label, _) in enumerate(cases, start=2):
        data_ws.cell(row=1, column=col, value=label)

    for r, param_name in enumerate(param_order, start=2):
        data_ws.cell(row=r, column=1, value=param_name)
        for col, (_, param_map) in enumerate(cases, start=2):
            data_ws.cell(row=r, column=col, value=param_map.get(param_name))

    # --- Sheet 2: report ---
    rep_ws = out_wb.create_sheet("Report")
    rep_ws.cell(row=1, column=1, value="Extraction report for folder:")
    rep_ws.cell(row=1, column=2, value=folder)

    rep_ws.cell(row=3, column=1, value="File Name")
    rep_ws.cell(row=3, column=2, value="Status")
    rep_ws.cell(row=3, column=3, value="Details")
    for i, (fname, status, details) in enumerate(report_rows, start=4):
        rep_ws.cell(row=i, column=1, value=fname)
        rep_ws.cell(row=i, column=2, value=status)
        rep_ws.cell(row=i, column=3, value=details)

    summary_row = 4 + len(report_rows) + 1
    rep_ws.cell(row=summary_row, column=1, value="Total files")
    rep_ws.cell(row=summary_row, column=2, value=len(report_rows))
    rep_ws.cell(row=summary_row + 1, column=1, value="Success")
    rep_ws.cell(row=summary_row + 1, column=2, value=n_success)
    rep_ws.cell(row=summary_row + 2, column=1, value="Failure")
    rep_ws.cell(row=summary_row + 2, column=2, value=n_failure)

    out_wb.save(os.path.join(folder, OUTPUT_FILENAME))


def main():
    parent = choose_folder()
    if not parent:
        print("No folder selected. Exiting.")
        sys.exit(0)

    folders_done = 0
    grand_success = 0
    grand_failure = 0

    # Walk the whole tree so both immediate and nested subfolders are handled.
    for dirpath, _dirnames, _filenames in os.walk(parent):
        result = process_folder(dirpath)
        if result is None:
            continue
        n_success, n_failure = result
        folders_done += 1
        grand_success += n_success
        grand_failure += n_failure
        print("Processed:", dirpath,
              "-> success: %d, failure: %d" % (n_success, n_failure))

    if folders_done == 0:
        print("No folders with Excel test files were found under:", parent)
        sys.exit(1)

    print("\nDone. {0} folder(s) processed.".format(folders_done))
    print("Total files -> success: {0}, failure: {1}".format(
        grand_success, grand_failure))


if __name__ == "__main__":
    main()
