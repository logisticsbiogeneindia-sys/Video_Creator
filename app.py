
import streamlit as st
import fitz
import pandas as pd
import re
import io
from collections import defaultdict
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.utils import get_column_letter


st.set_page_config(
    page_title="BIOGENE INDIA - BOE Extractor",
    page_icon="📄",
    layout="wide"
)

st.title("BIOGENE INDIA")
st.subheader("Bill of Entry – Automatic Data Extraction")
st.caption("Extract BOE details, HSN, IGST Rate, Taxable Amount and IGST Amount")


# --------------------------------------------------
# BASIC HELPERS
# --------------------------------------------------

def clean_text(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def number_value(value):
    if value is None:
        return None

    value = str(value).replace(",", "").strip()

    if not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return None

    try:
        return float(value)
    except ValueError:
        return None


def format_amount(value):
    if value is None:
        return ""

    return f"{value:,.2f}"


def get_page_lines(page):
    """
    Read PDF words with their positions.
    No Poppler or pdftotext required.
    """
    words = page.get_text("words", sort=True)
    groups = defaultdict(list)

    for word in words:
        x0, y0, x1, y1, text = word[:5]
        block_no = word[5]
        line_no = word[6]

        groups[(block_no, line_no)].append({
            "text": str(text),
            "x0": x0,
            "x1": x1,
            "center": (x0 + x1) / 2
        })

    result = []

    for key, items in groups.items():
        items.sort(key=lambda x: x["x0"])

        result.append({
            "words": items,
            "text": " ".join(x["text"] for x in items),
            "y": min(x["x0"] * 0 + 0 for x in items)
        })

    # Preserve the visual reading order.
    result = sorted(
        result,
        key=lambda x: (
            min(w["x0"] * 0 + 0 for w in x["words"]),
            min(w["x0"] for w in x["words"])
        )
    )

    # Re-sort using actual vertical position from PDF words.
    positions = defaultdict(list)

    for word in words:
        positions[(word[5], word[6])].append(word[1])

    for line in result:
        key = next(
            (
                k for k, v in groups.items()
                if " ".join(x["text"] for x in sorted(
                    v, key=lambda x: x["x0"]
                )) == line["text"]
            ),
            None
        )

        if key is not None:
            line["y"] = min(positions[key])

    return sorted(result, key=lambda x: x["y"])


def numeric_words(line):
    output = []

    for word in line["words"]:
        value = number_value(word["text"])

        if value is not None:
            output.append({
                "value": value,
                "center": word["center"],
                "text": word["text"]
            })

    return output


def nearest_number(line, target_x):
    """
    Select the number physically located under a PDF column.
    """
    candidates = numeric_words(line)

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: abs(item["center"] - target_x)
    )

    return candidates[0]["value"]


def find_column_x(lines, pattern):
    """
    Locate the horizontal center of a column heading.
    """
    for line in lines:
        for word in line["words"]:
            if re.search(pattern, word["text"], re.I):
                return word["center"]

    return None


def is_number_line(line):
    return bool(numeric_words(line))


# --------------------------------------------------
# HEADER EXTRACTION
# --------------------------------------------------

def extract_header(full_text):

    header = {
        "BOE No.": "",
        "Date": "",
        "EXPORTER": "",
        "ADDRESS": ""
    }

    text = clean_text(full_text)

    # BOE Number and Date
    match = re.search(
        r"BE\s*No\.?\s+(\d{5,})\s+BE\s*Date\s+"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
        text,
        re.I
    )

    if match:
        header["BOE No."] = match.group(1)
        header["Date"] = match.group(2)

    # Extract exporter and address from page text.
    lines = [clean_text(x) for x in full_text.splitlines()]
    lines = [x for x in lines if x]

    for i, line in enumerate(lines):

        if re.search(r"\bEXPORTER\b", line, re.I):

            value = re.sub(
                r"^.*?\bEXPORTER\b\s*:?\s*",
                "",
                line,
                flags=re.I
            )

            if value and not re.search(r"\bADDRESS\b", value, re.I):
                header["EXPORTER"] = value

            elif i + 1 < len(lines):
                header["EXPORTER"] = lines[i + 1]

        if re.search(r"\bADDRESS\b", line, re.I):

            value = re.sub(
                r"^.*?\bADDRESS\b\s*:?\s*",
                "",
                line,
                flags=re.I
            )

            if value:
                header["ADDRESS"] = value

            elif i + 1 < len(lines):
                header["ADDRESS"] = lines[i + 1]

    return header


# --------------------------------------------------
# ITEM DETAILS EXTRACTION
# --------------------------------------------------

def extract_item_identity(line):

    text = line["text"]

    match = re.match(
        r"^\s*(\d+)\s+(\d+)\s+(\d{4,10})\s+",
        text
    )

    if not match:
        return None

    return {
        "invoice_no": match.group(1),
        "si": int(match.group(2)),
        "hsn": match.group(3)
    }


def extract_assess_value(lines, start, end):

    for i in range(start, min(end, len(lines))):

        text = lines[i]["text"]

        if re.search(r"29\.?\s*ASSESS", text, re.I):

            # Normally the next line contains:
            # Flags | Assess Value | Total Duty

            for j in range(i + 1, min(i + 5, end)):

                values = numeric_words(lines[j])

                if len(values) >= 2:
                    return values[-2]["value"]

    return None


# --------------------------------------------------
# IGST RATE AND AMOUNT
# --------------------------------------------------

def extract_igst(lines, start, end):

    igst_x = None
    rate = None
    amount = None

    # Find the IGST column position.
    for i in range(start, min(end, len(lines))):

        if re.search(r"5\.?\s*IGST", lines[i]["text"], re.I):

            igst_x = find_column_x(
                [lines[i]],
                r"IGST"
            )

            break

    if igst_x is None:
        return rate, amount

    # Find Rate and Amount rows in B. ITEM DUTY.
    for i in range(start, min(end, len(lines))):

        text = lines[i]["text"].strip()

        if re.match(r"^Rate\b", text, re.I):
            rate = nearest_number(lines[i], igst_x)

        elif re.match(r"^Amount\b", text, re.I):
            amount = nearest_number(lines[i], igst_x)

        if rate is not None and amount is not None:
            break

    return rate, amount


# --------------------------------------------------
# PART III EXTRACTION
# --------------------------------------------------

def extract_items(pdf):

    extracted = []

    for page_no in range(len(pdf)):

        page = pdf[page_no]
        lines = get_page_lines(page)

        # Only process pages containing Part III.
        page_text = page.get_text("text")

        if not re.search(r"PART\s*[-–]?\s*III", page_text, re.I):
            continue

        # Identify all item starting lines.
        item_positions = []

        for i, line in enumerate(lines):

            identity = extract_item_identity(line)

            if identity:
                item_positions.append((i, identity))

        for index, (start, identity) in enumerate(item_positions):

            if index + 1 < len(item_positions):
                end = item_positions[index + 1][0]
            else:
                end = len(lines)

            # Keep each item's extraction within its own block.
            assess_value = extract_assess_value(
                lines, start, end
            )

            igst_rate, igst_amount = extract_igst(
                lines, start, end
            )

            extracted.append({
                "SI": identity["si"],
                "HSN": identity["hsn"],
                "GST @ %": igst_rate,
                "TAXABLE AMOUNT": assess_value,
                "TAX AMOUNT": igst_amount,
                "Invoice Sr. No.": identity["invoice_no"],
                "PDF Page": page_no + 1
            })

    return extracted


# --------------------------------------------------
# SUMMARY EXTRACTION
# --------------------------------------------------

def extract_summary(pdf):

    result = {
        "taxable_total": None,
        "igst_total": None
    }

    # Summary is generally present near the beginning.
    for page_no in range(min(5, len(pdf))):

        page = pdf[page_no]
        lines = get_page_lines(page)

        for i, line in enumerate(lines):

            text = line["text"]

            if not re.search(r"7\.?\s*IGST", text, re.I):
                continue

            igst_x = find_column_x(
                [line],
                r"IGST"
            )

            assess_x = find_column_x(
                lines,
                r"18\.?\s*TOT\.?\s*ASS"
            )

            if igst_x is None:
                continue

            # Find the values below the summary headings.
            for j in range(i + 1, min(i + 5, len(lines))):

                if not is_number_line(lines[j]):
                    continue

                if result["igst_total"] is None:
                    result["igst_total"] = nearest_number(
                        lines[j], igst_x
                    )

                if assess_x is not None:
                    result["taxable_total"] = nearest_number(
                        lines[j], assess_x
                    )

                if (
                    result["igst_total"] is not None
                    and result["taxable_total"] is not None
                ):
                    return result

    return result


# --------------------------------------------------
# EXCEL GENERATION
# --------------------------------------------------

def create_excel(header, items, summary):

    wb = Workbook()
    ws = wb.active
    ws.title = "BOE DATA"

    blue = "5B9BD5"
    light_blue = "D9EAF7"
    white = "FFFFFF"

    # Header section
    header_rows = [
        ["BOE No.", ":", header["BOE No."]],
        ["Date", ":", header["Date"]],
        ["EXPORTER", ":", header["EXPORTER"]],
        ["ADDRESS", ":", header["ADDRESS"]]
    ]

    for row in header_rows:
        ws.append(row)

    ws.append([])

    # Main table
    columns = [
        "DETAILS",
        "SI",
        "HSN",
        "GST @ %",
        "TAXABLE AMOUNT",
        "TAX AMOUNT"
    ]

    ws.append(columns)

    for item in items:

        ws.append([
            "",
            item["SI"],
            item["HSN"],
            item["GST @ %"] if item["GST @ %"] is not None else "",
            item["TAXABLE AMOUNT"] if item["TAXABLE AMOUNT"] is not None else "",
            item["TAX AMOUNT"] if item["TAX AMOUNT"] is not None else ""
        ])

    # Total row: use PDF summary values only.
    ws.append([
        "",
        "",
        "",
        "",
        summary["taxable_total"]
        if summary["taxable_total"] is not None else "",
        summary["igst_total"]
        if summary["igst_total"] is not None else ""
    ])

    # Formatting
    for cell in ws[6]:
        cell.fill = PatternFill("solid", fgColor=blue)
        cell.font = Font(bold=True, color=white)
        cell.alignment = Alignment(horizontal="center")

    for row in ws.iter_rows(min_row=1, max_row=4, max_col=3):
        row[0].font = Font(bold=True)
        row[2].font = Font(bold=True)

    thin = Side(style="thin", color="808080")

    for row in ws.iter_rows():
        for cell in row:
            cell.border = Border(
                left=thin,
                right=thin,
                top=thin,
                bottom=thin
            )

    for row in range(7, ws.max_row + 1):

        for col in [5, 6]:
            ws.cell(row, col).number_format = '#,##0.00'

        for col in range(1, 7):
            ws.cell(row, col).alignment = Alignment(
                horizontal="center" if col < 5 else "right"
            )

    # Total row
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor=light_blue)

    widths = {
        "A": 18,
        "B": 12,
        "C": 18,
        "D": 16,
        "E": 24,
        "F": 22
    }

    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    ws.freeze_panes = "A7"
    ws.auto_filter.ref = f"A6:F{max(6, ws.max_row - 1)}"

    # Add a separate audit sheet.
    audit = wb.create_sheet("EXTRACTION CHECK")

    audit.append([
        "SI",
        "HSN",
        "GST RATE",
        "TAXABLE AMOUNT",
        "IGST AMOUNT",
        "INVOICE SR NO.",
        "PDF PAGE",
        "STATUS"
    ])

    for item in items:

        missing = []

        for field in [
            "HSN",
            "GST @ %",
            "TAXABLE AMOUNT",
            "TAX AMOUNT"
        ]:
            if item.get(field) is None or item.get(field) == "":
                missing.append(field)

        audit.append([
            item["SI"],
            item["HSN"],
            item["GST @ %"] if item["GST @ %"] is not None else "",
            item["TAXABLE AMOUNT"] if item["TAXABLE AMOUNT"] is not None else "",
            item["TAX AMOUNT"] if item["TAX AMOUNT"] is not None else "",
            item["Invoice Sr. No."],
            item["PDF Page"],
            "CHECK: " + ", ".join(missing) if missing else "OK"
        ])

    for cell in audit[1]:
        cell.fill = PatternFill("solid", fgColor=blue)
        cell.font = Font(bold=True, color=white)

    for column in audit.columns:
        letter = get_column_letter(column[0].column)
        audit.column_dimensions[letter].width = 22

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return output


# --------------------------------------------------
# STREAMLIT USER INTERFACE
# --------------------------------------------------

uploaded_file = st.file_uploader(
    "Upload Bill of Entry PDF",
    type=["pdf"]
)

if uploaded_file:

    if st.button("EXTRACT DATA", type="primary"):

        with st.spinner("Reading PDF and extracting item details..."):

            try:

                pdf_bytes = uploaded_file.read()
                pdf = fitz.open(
                    stream=pdf_bytes,
                    filetype="pdf"
                )

                full_text = "\n".join(
                    page.get_text("text")
                    for page in pdf
                )

                if not full_text.strip():
                    st.error(
                        "PDF contains no readable text. "
                        "OCR is required for this scanned PDF."
                    )
                    st.stop()

                header = extract_header(full_text)

                items = extract_items(pdf)

                summary = extract_summary(pdf)

                if not items:
                    st.error(
                        "No item records found. "
                        "Please check the PDF layout."
                    )
                    st.stop()

                # Sort by item serial number.
                items.sort(
                    key=lambda x: (
                        x["Invoice Sr. No."],
                        x["SI"]
                    )
                )

                st.success(
                    f"Extraction completed: {len(items)} item records found."
                )

                # Header preview
                st.markdown("### BOE DETAILS")

                st.table(pd.DataFrame([
                    ["BOE No.", header["BOE No."]],
                    ["Date", header["Date"]],
                    ["EXPORTER", header["EXPORTER"]],
                    ["ADDRESS", header["ADDRESS"]]
                ], columns=["FIELD", "VALUE"]))

                # Main output
                st.markdown("### EXTRACTED ITEM DETAILS")

                display_data = []

                for item in items:
                    display_data.append({
                        "DETAILS": "",
                        "SI": item["SI"],
                        "HSN": item["HSN"],
                        "GST @ %": (
                            item["GST @ %"]
                            if item["GST @ %"] is not None
                            else ""
                        ),
                        "TAXABLE AMOUNT": format_amount(
                            item["TAXABLE AMOUNT"]
                        ),
                        "TAX AMOUNT": format_amount(
                            item["TAX AMOUNT"]
                        )
                    })

                st.dataframe(
                    pd.DataFrame(display_data),
                    use_container_width=True,
                    hide_index=True
                )

                st.markdown("### TOTALS FROM PDF SUMMARY")

                st.write({
                    "Total Taxable Amount": format_amount(
                        summary["taxable_total"]
                    ),
                    "Total IGST Amount": format_amount(
                        summary["igst_total"]
                    )
                })

                # Missing value report
                missing_items = [
                    item for item in items
                    if any(
                        item[field] is None or item[field] == ""
                        for field in [
                            "HSN",
                            "GST @ %",
                            "TAXABLE AMOUNT",
                            "TAX AMOUNT"
                        ]
                    )
                ]

                if missing_items:
                    st.warning(
                        f"{len(missing_items)} item(s) need manual verification. "
                        "See EXTRACTION CHECK sheet."
                    )
                else:
                    st.success("All extracted item fields are populated.")

                # Excel
                excel_file = create_excel(
                    header,
                    items,
                    summary
                )

                st.download_button(
                    label="DOWNLOAD EXCEL FILE",
                    data=excel_file,
                    file_name="BOE_Extracted_Data.xlsx",
                    mime=(
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    )
                )

                pdf.close()

            except Exception as e:
                st.error(f"Extraction Error: {str(e)}")
                st.exception(e)
