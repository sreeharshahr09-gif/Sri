"""
Extract tire test parameters from several Excel test files and consolidate
them into a single new Excel file.

Layout expected in each source file (on the first sheet):
    B1        -> unique label that identifies the test file / case
    A2:A21    -> parameter names
    B2:B21    -> parameter values

How to use:
    1. Drop this script into the folder that holds all the test Excel files.
    2. Run it:   python extract_tire_test_params.py
    3. A new file "tire_test_parameters.xlsx" is created in the same folder.

The output has one column per test case (headed by the B1 label) and one row
per parameter, so every test can be compared side by side.
"""

import os
import sys
from openpyxl import load_workbook, Workbook

# Rows to read (1-based, matching Excel). A2:A21 / B2:B21 -> rows 2..21.
FIRST_DATA_ROW = 2
LAST_DATA_ROW = 21
LABEL_CELL = "B1"

OUTPUT_FILENAME = "tire_test_parameters.xlsx"


def extract_from_file(path):
    """Return (label, [(param_name, value), ...]) from one Excel file's sheet 1."""
    wb = load_workbook(path, data_only=True)
    ws = wb.worksheets[0]  # sheet 1

    label = ws[LABEL_CELL].value
    if label is None or str(label).strip() == "":
        # Fall back to the file name (without extension) when B1 is empty.
        label = os.path.splitext(os.path.basename(path))[0]

    params = []
    for row in range(FIRST_DATA_ROW, LAST_DATA_ROW + 1):
        name = ws.cell(row=row, column=1).value   # column A
        value = ws.cell(row=row, column=2).value  # column B
        if name is None or str(name).strip() == "":
            continue
        params.append((str(name).strip(), value))

    wb.close()
    return str(label).strip(), params


def main():
    folder = os.path.dirname(os.path.abspath(__file__))
    script_name = os.path.basename(__file__)

    # Collect Excel files, skipping temp/lock files and any prior output.
    excel_files = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith((".xlsx", ".xlsm", ".xls")):
            continue
        if name.startswith("~$"):          # Excel lock/temp files
            continue
        if name == OUTPUT_FILENAME:        # don't re-ingest our own output
            continue
        excel_files.append(os.path.join(folder, name))

    if not excel_files:
        print("No Excel test files found in:", folder)
        sys.exit(1)

    # Read every file. Use the parameter names from the first file as the
    # canonical row order; other files are matched by name.
    cases = []            # list of (label, {param_name: value})
    param_order = []      # preserves the order parameters first appear

    for path in excel_files:
        try:
            label, params = extract_from_file(path)
        except Exception as exc:  # keep going even if one file is malformed
            print("Skipped (could not read):", os.path.basename(path), "->", exc)
            continue

        param_map = {}
        for name, value in params:
            if name not in param_order:
                param_order.append(name)
            param_map[name] = value

        cases.append((label, param_map))
        print("Read:", os.path.basename(path), "(label:", label + ")")

    if not cases:
        print("No readable Excel test files found.")
        sys.exit(1)

    # Build the consolidated workbook.
    out_wb = Workbook()
    out_ws = out_wb.active
    out_ws.title = "Tire Test Parameters"

    # Header row: "Parameter" + one column per test label.
    out_ws.cell(row=1, column=1, value="Parameter")
    for col, (label, _) in enumerate(cases, start=2):
        out_ws.cell(row=1, column=col, value=label)

    # One row per parameter.
    for r, param_name in enumerate(param_order, start=2):
        out_ws.cell(row=r, column=1, value=param_name)
        for col, (_, param_map) in enumerate(cases, start=2):
            out_ws.cell(row=r, column=col, value=param_map.get(param_name))

    output_path = os.path.join(folder, OUTPUT_FILENAME)
    out_wb.save(output_path)

    print("\nDone. {0} test case(s), {1} parameter(s) written to:".format(
        len(cases), len(param_order)))
    print(output_path)


if __name__ == "__main__":
    main()
