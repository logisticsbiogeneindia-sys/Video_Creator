
import re
import io
from datetime import datetime

import fitz  # PyMuPDF
import pandas as pd
import streamlit as st

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.utils import get_column_letter


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="Biogene India - Bill of Entry Extractor",
    page_icon="📄",
    layout="wide"
)

st.title("📄 Biogene India - Bill of Entry Extractor")
st.caption(
    "Bill of Entry PDF → BOE Details → Item Details → "
    "HSN-wise GST Summary → Excel"
)

st.info(
    "Upload your ICEGATE Bill of Entry PDF. "
    "The system extracts item details and prepares an Excel report "
    "with validation checks."
)


# ============================================================
# CONSTANTS
# ============================================================

NUMBER_PATTERN = r"-?\d[\d,]*(?:\.\d+)?"

ITEM_START = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d{8})\s+NOEXCISE\b(.*)$",
    re.IGNORECASE
)


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value):
    value = str(value or "")
    value = value.replace("\xa0", " ")
    value = value.replace("\u200b", "")
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def to_number(value):
    if value is None:
        return None

    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def get_numbers(text):
    return re.findall(NUMBER_PATTERN, text or "")


def safe_sum(series):
    return round(pd.to_numeric(series, errors="coerce").sum(), 2)


def indian_number_format(value):
    return f"{value:,.2f}"


# ============================================================
# PDF TEXT EXTRACTION
# ============================================================

def extract_pdf_text(pdf_bytes):

    try:
        doc = fitz.open(
            stream=pdf_bytes,
            filetype="pdf"
        )

        page_texts = []

        for page in doc:
            text = page.get_text("text", sort=True) or ""
            page_texts.append(text)

        doc.close()

        return page_texts

    except Exception as exc:
        raise RuntimeError(
            f"PDF read nahi ho paayi: {exc}"
        )


# ============================================================
# HEADER EXTRACTION
# ============================================================

def extract_header(page_texts, pdf_bytes):

    doc = fitz.open(
        stream=pdf_bytes,
        filetype="pdf"
    )

    first_page_text = (
        doc[0].get_text("text", sort=True)
        if len(doc) else ""
    )

    first_page_blocks = [
        clean_text(block[4])
        for block in doc[0].get_text("blocks")
        if clean_text(block[4])
    ] if len(doc) else []

    all_text = "\n".join(page_texts[:8])

    be_no = ""
    be_date = ""

    # BOE number and date
    header_patterns = [
        r"\b(?:BE\s*No\.?|BOE\s*No\.?|Bill\s+of\s+Entry\s+No\.?)"
        r"\s*[:\-]?\s*(\d{5,})",

        r"\b(\d{5,})\s+(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})\b"
    ]

    for pattern in header_patterns:

        match = re.search(
            pattern,
            first_page_text,
            re.IGNORECASE
        )

        if match:

            if len(match.groups()) == 2:
                be_no = match.group(1)
                be_date = match.group(2)

            else:
                be_no = match.group(1)

            if be_no:
                break

    if not be_date:

        date_match = re.search(
            r"\b(?:BE\s*Date|BOE\s*Date|Date)"
            r"\s*[:\-]?\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})",
            first_page_text,
            re.IGNORECASE
        )

        if date_match:
            be_date = date_match.group(1)

    # Exporter and address
    exporter = ""
    address = ""

    exporter_match = re.search(
        r"BIOLEGEND\s+INC\.?",
        all_text,
        re.IGNORECASE
    )

    if exporter_match:

        exporter = "BIOLEGEND INC."

        address_match = re.search(
            r"8999\s+BIOLEGEND\s+WAY"
            r"[\s,]+SAN\s+DIEGO"
            r"[\s,]+CA"
            r"[\s,]+92121"
            r"(?:[\s,]+(?:US|USA|UNITED STATES))?",
            all_text,
            re.IGNORECASE
        )

        if address_match:
            address = clean_text(address_match.group(0))

            if not re.search(
                r"UNITED STATES|USA|US$",
                address,
                re.IGNORECASE
            ):
                address += " UNITED STATES"

    # Generic exporter fallback
    if not exporter:

        exporter_match = re.search(
            r"EXPORTER\s*[:\-]?\s*(.+)",
            all_text,
            re.IGNORECASE
        )

        if exporter_match:
            exporter = clean_text(
                exporter_match.group(1).splitlines()[0]
            )

    # Convert date
    if be_date:

        for fmt in (
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%m/%d/%Y"
        ):

            try:
                be_date = datetime.strptime(
                    be_date, fmt
                ).strftime("%d-%m-%Y")

                break

            except ValueError:
                pass

    doc.close()

    return {
        "BOE No.": be_no,
        "Date": be_date,
        "EXPORTER": exporter,
        "ADDRESS": address
    }


# ============================================================
# ITEM DESCRIPTION EXTRACTION
# ============================================================

def extract_description(first_line, block_lines):

    description = first_line

    # Remove initial item identifiers
    description = re.sub(
        r"^\s*\d+\s+\d+\s+\d{8}\s+NOEXCISE\b",
        "",
        description,
        flags=re.IGNORECASE
    )

    # Remove trailing item flags
    description = re.sub(
        r"\s+N\s+N\s+Y\s+N\s+N\s*$",
        "",
        description,
        flags=re.IGNORECASE
    )

    description = clean_text(description)

    # Use continuation text only if first line is empty
    if not description:

        for line in block_lines[1:]:

            line = clean_text(line)

            if not line:
                continue

            if re.search(
                r"\b(?:Rate|Amount|Assessable|Duty|Invoice)\b",
                line,
                re.IGNORECASE
            ):
                break

            if re.fullmatch(
                r"[\d,.\s]+",
                line
            ):
                continue

            if re.search(
                r"^\d+\s+\d+\s+\d{8}\s+NOEXCISE",
                line,
                re.IGNORECASE
            ):
                continue

            description = line
            break

    return description or "Description not captured"


# ============================================================
# ASSESSABLE VALUE EXTRACTION
# ============================================================

def extract_assessable_values(block):

    # Primary pattern
    pattern = re.compile(
        r"\bY\s+N\s+Y\s+N\s+Y\s+Y\s+"
        rf"({NUMBER_PATTERN})\s+"
        rf"({NUMBER_PATTERN})",
        re.IGNORECASE | re.DOTALL
    )

    match = pattern.search(block)

    if match:

        return (
            to_number(match.group(1)),
            to_number(match.group(2))
        )

    # Flexible whitespace fallback
    compact = re.sub(
        r"\s+",
        " ",
        block
    )

    match = pattern.search(compact)

    if match:

        return (
            to_number(match.group(1)),
            to_number(match.group(2))
        )

    return None, None


# ============================================================
# RATE AND AMOUNT EXTRACTION
# ============================================================

def extract_labeled_numbers(block, label, min_count=1):

    lines = block.splitlines()

    for i, line in enumerate(lines):

        match = re.search(
            rf"\b{re.escape(label)}\b",
            line,
            re.IGNORECASE
        )

        if not match:
            continue

        content = line[match.end():]

        values = get_numbers(content)

        # Check following line if current line is incomplete
        if len(values) < min_count:

            for next_line in lines[i + 1:i + 4]:

                next_line = clean_text(next_line)

                if not next_line:
                    continue

                if re.search(
                    r"\b(?:Rate|Amount|Assessable|Description)\b",
                    next_line,
                    re.IGNORECASE
                ):
                    break

                values.extend(get_numbers(next_line))

                if len(values) >= min_count:
                    break

        if len(values) >= min_count:

            return [
                to_number(x)
                for x in values
            ]

    return []


# ============================================================
# ITEM PARSING ENGINE
# ============================================================

def parse_item_blocks(page_texts):

    full_text = "\n".join(page_texts)
    lines = full_text.splitlines()

    starts = []

    for i, line in enumerate(lines):

        match = ITEM_START.match(line)

        if match:
            starts.append((i, match))

    rows = []
    parse_issues = []

    if not starts:

        return pd.DataFrame(), [
            "No Item Start Pattern Found. "
            "PDF layout needs verification."
        ]

    for index, (start_line, match) in enumerate(starts):

        end_line = (
            starts[index + 1][0]
            if index + 1 < len(starts)
            else len(lines)
        )

        block_lines = lines[start_line:end_line]

        block = "\n".join(block_lines)

        invoice_si = int(match.group(1))
        item_si = int(match.group(2))
        hsn = match.group(3)

        description = extract_description(
            match.group(4),
            block_lines
        )

        # Assessable Value and Total Duty
        assessable, total_duty = extract_assessable_values(
            block
        )

        # Rate section
        rate_values = extract_labeled_numbers(
            block,
            "Rate",
            min_count=5
        )

        # Amount section
        amount_values = extract_labeled_numbers(
            block,
            "Amount",
            min_count=3
        )

        # Default values
        bcd = None
        sws = None
        igst = None
        igst_rate = None

        if len(rate_values) >= 5:
            igst_rate = rate_values[2]

        if len(amount_values) >= 3:

            bcd = amount_values[0]
            sws = amount_values[1]
            igst = amount_values[2]

        # GST taxable base
        gst_taxable = None

        if (
            assessable is not None
            and bcd is not None
            and sws is not None
        ):

            gst_taxable = round(
                assessable + bcd + sws,
                2
            )

        # Validation
        missing = []

        if not description or description == "Description not captured":
            missing.append("Description")

        if assessable is None:
            missing.append("Assessable Value")

        if total_duty is None:
            missing.append("Total Duty")

        if igst_rate is None:
            missing.append("GST Rate")

        if bcd is None:
            missing.append("BCD")

        if sws is None:
            missing.append("SWS")

        if igst is None:
            missing.append("IGST")

        if gst_taxable is None:
            missing.append("GST Taxable Amount")

        status = (
            "OK"
            if not missing
            else "CHECK REQUIRED"
        )

        if missing:

            parse_issues.append(
                f"Item SI {item_si} | HSN {hsn} | "
                f"Missing: {', '.join(missing)}"
            )

        rows.append({

            "Invoice SI": invoice_si,
            "Item SI": item_si,
            "HSN": hsn,
            "Description": description,
            "GST Rate (%)": igst_rate,
            "Assessable Value": assessable,
            "BCD": bcd,
            "SWS": sws,
            "GST Taxable Amount": gst_taxable,
            "IGST Amount": igst,
            "Total Duty": total_duty,
            "Extraction Status": status,
            "Missing Fields": ", ".join(missing)

        })

    return pd.DataFrame(rows), parse_issues


# ============================================================
# GST SUMMARY
# ============================================================

def create_summary(detail):

    if detail.empty:
        return pd.DataFrame()

    valid = detail.dropna(
        subset=[
            "HSN",
            "GST Rate (%)",
            "GST Taxable Amount",
            "IGST Amount"
        ]
    ).copy()

    if valid.empty:
        return pd.DataFrame()

    summary = (
        valid.groupby(
            ["HSN", "GST Rate (%)"],
            as_index=False
        )
        .agg(
            Items=("Item SI", "count"),
            Taxable_Amount=("GST Taxable Amount", "sum"),
            Tax_Amount=("IGST Amount", "sum")
        )
        .sort_values(
            ["HSN", "GST Rate (%)"]
        )
        .reset_index(drop=True)
    )

    summary["Taxable_Amount"] = summary[
        "Taxable_Amount"
    ].round(2)

    summary["Tax_Amount"] = summary[
        "Tax_Amount"
    ].round(2)

    return summary


# ============================================================
# RECONCILIATION
# ============================================================

def create_checks(detail, summary, issues):

    complete_items = detail[
        detail["Extraction Status"] == "OK"
    ]

    taxable_total = (
        safe_sum(summary["Taxable_Amount"])
        if not summary.empty else 0
    )

    tax_total = (
        safe_sum(summary["Tax_Amount"])
        if not summary.empty else 0
    )

    expected_gst = 0

    if not summary.empty:

        for _, row in summary.iterrows():

            expected_gst += (
                row["Taxable_Amount"]
                * row["GST Rate (%)"] / 100
            )

    expected_gst = round(expected_gst, 2)

    difference = round(
        tax_total - expected_gst,
        2
    )

    checks = [

        ("Total detected item blocks", len(detail)),

        ("Successfully extracted items", len(complete_items)),

        ("Items requiring verification", len(issues)),

        ("Total taxable amount", taxable_total),

        ("Total IGST amount", tax_total),

        ("GST calculated from rate", expected_gst),

        ("GST calculation difference", difference),

        (
            "GST mathematical status",
            "PASS" if abs(difference) <= 0.05 else "CHECK"
        )

    ]

    return checks


# ============================================================
# EXCEL GENERATION
# ============================================================

def make_excel(header, detail, issues):

    summary = create_summary(detail)

    checks = create_checks(
        detail,
        summary,
        issues
    )

    wb = Workbook()

    # --------------------------------------------------------
    # SHEET 1: GST SUMMARY
    # --------------------------------------------------------

    ws = wb.active
    ws.title = "GST Summary"

    widths = {
        "A": 18,
        "B": 10,
        "C": 18,
        "D": 15,
        "E": 23,
        "F": 20
    }

    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    header_rows = [
        ("BOE No.", ":", header.get("BOE No.", "")),
        ("Date", ":", header.get("Date", "")),
        ("EXPORTER", ":", header.get("EXPORTER", "")),
        ("ADDRESS", ":", header.get("ADDRESS", ""))
    ]

    for r, values in enumerate(header_rows, start=1):

        for c, value in enumerate(values, start=1):

            cell = ws.cell(r, c, value)

            cell.alignment = Alignment(
                vertical="center",
                wrap_text=True
            )

        ws.merge_cells(
            start_row=r,
            start_column=3,
            end_row=r,
            end_column=6
        )

        ws.row_dimensions[r].height = (
            30 if r == 4 else 22
        )

    ws.row_dimensions[5].height = 12

    table_headers = [
        "DETAILS",
        "SI",
        "HSN",
        "GST @ %",
        "TAXABLE AMOUNT",
        "TAX AMOUNT"
    ]

    for col, value in enumerate(
        table_headers,
        start=1
    ):

        cell = ws.cell(6, col, value)

        cell.font = Font(
            bold=True,
            color="FFFFFF"
        )

        cell.fill = PatternFill(
            "solid",
            fgColor="1F4E78"
        )

        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True
        )

    for idx, row in summary.iterrows():

        r = 7 + idx

        ws.cell(r, 1, idx + 1)
        ws.cell(r, 2, "")
        ws.cell(r, 3, str(row["HSN"]))

        ws.cell(
            r,
            4,
            f'{row["GST Rate (%)"]:g}%'
        )

        ws.cell(
            r,
            5,
            float(row["Taxable_Amount"])
        )

        ws.cell(
            r,
            6,
            float(row["Tax_Amount"])
        )

        for col in range(1, 7):

            ws.cell(r, col).alignment = Alignment(
                vertical="center",
                horizontal=(
                    "right" if col in (5, 6)
                    else "center"
                )
            )

        ws.cell(r, 5).number_format = '#,##,##0.00'
        ws.cell(r, 6).number_format = '#,##,##0.00'

    total_row = 7 + len(summary) + 1

    ws.cell(
        total_row,
        5,
        safe_sum(summary["Taxable_Amount"])
        if not summary.empty else 0
    )

    ws.cell(
        total_row,
        6,
        safe_sum(summary["Tax_Amount"])
        if not summary.empty else 0
    )

    for col in range(1, 7):

        cell = ws.cell(total_row, col)

        cell.font = Font(bold=True)

        cell.fill = PatternFill(
            "solid",
            fgColor="D9EAF7"
        )

        cell.alignment = Alignment(
            horizontal=(
                "right" if col in (5, 6)
                else "center"
            )
        )

        cell.border = Border(
            top=Side(
                style="thin",
                color="1F1F1F"
            )
        )

    ws.cell(
        total_row,
        5
    ).number_format = '#,##,##0.00'

    ws.cell(
        total_row,
        6
    ).number_format = '#,##,##0.00'

    note_row = total_row + 2

    ws.merge_cells(
        start_row=note_row,
        start_column=1,
        end_row=note_row,
        end_column=6
    )

    ws.cell(
        note_row,
        1,
        "GST Taxable Amount = Assessable Value + BCD + SWS. "
        "IGST Amount is extracted from the item duty Amount section. "
        "Verify the final summary against the original BOE."
    )

    ws.cell(note_row, 1).font = Font(
        italic=True,
        size=9,
        color="666666"
    )

    ws.cell(note_row, 1).alignment = Alignment(
        wrap_text=True
    )

    ws.row_dimensions[note_row].height = 32

    thin = Side(
        style="thin",
        color="B7C9D6"
    )

    for row in ws.iter_rows(
        min_row=1,
        max_row=total_row,
        min_col=1,
        max_col=6
    ):

        for cell in row:
            cell.border = Border(bottom=thin)

    ws.freeze_panes = "A7"
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    ws.print_area = f"A1:F{note_row}"

    # --------------------------------------------------------
    # SHEET 2: ITEM DETAILS
    # --------------------------------------------------------

    wd = wb.create_sheet("Item Details")

    for col, name in enumerate(
        detail.columns,
        start=1
    ):

        cell = wd.cell(1, col, name)

        cell.font = Font(
            bold=True,
            color="FFFFFF"
        )

        cell.fill = PatternFill(
            "solid",
            fgColor="1F4E78"
        )

        cell.alignment = Alignment(
            wrap_text=True,
            horizontal="center"
        )

    for row_idx, row in enumerate(
        detail.itertuples(index=False, name=None),
        start=2
    ):

        for col_idx, value in enumerate(
            row,
            start=1
        ):

            if pd.isna(value):
                value = None

            wd.cell(
                row_idx,
                col_idx,
                value
            )

            if isinstance(value, (int, float)):

                if col_idx >= 6 and col_idx <= 11:

                    wd.cell(
                        row_idx,
                        col_idx
                    ).number_format = '#,##,##0.00'

    detail_widths = [
        12, 10, 14, 58, 14, 18, 14,
        14, 20, 18, 16, 20, 35
    ]

    for idx, width in enumerate(
        detail_widths,
        start=1
    ):

        wd.column_dimensions[
            get_column_letter(idx)
        ].width = width

    wd.freeze_panes = "A2"

    wd.auto_filter.ref = (
        f"A1:{get_column_letter(len(detail.columns))}"
        f"{len(detail) + 1}"
    )

    wd.sheet_view.showGridLines = False

    # --------------------------------------------------------
    # SHEET 3: CHECKS
    # --------------------------------------------------------

    wr = wb.create_sheet("Checks")

    wr.append(["CHECK", "VALUE"])

    for cell in wr[1]:

        cell.font = Font(
            bold=True,
            color="FFFFFF"
        )

        cell.fill = PatternFill(
            "solid",
            fgColor="1F4E78"
        )

    for item in checks:
        wr.append(list(item))

    wr.column_dimensions["A"].width = 38
    wr.column_dimensions["B"].width = 25

    for row in range(2, wr.max_row + 1):

        value = wr.cell(row, 2).value

        if isinstance(value, (int, float)):

            wr.cell(
                row,
                2
            ).number_format = '#,##,##0.00'

    wr.freeze_panes = "A2"
    wr.sheet_view.showGridLines = False

    # --------------------------------------------------------
    # SAVE EXCEL
    # --------------------------------------------------------

    output = io.BytesIO()

    wb.save(output)

    output.seek(0)

    return output.getvalue(), summary


# ============================================================
# STREAMLIT UPLOAD SECTION
# ============================================================

uploaded_file = st.file_uploader(
    "1. Upload Bill of Entry PDF",
    type=["pdf"],
    accept_multiple_files=False,
    help="ICEGATE Bill of Entry PDF upload karein."
)

if uploaded_file is not None:

    st.success(
        f"Uploaded: {uploaded_file.name}"
    )

    if st.button(
        "🔍 Extract Data",
        type="primary",
        use_container_width=True
    ):

        # Clear previous extraction
        for key in [
            "boe_header",
            "boe_detail",
            "boe_issues",
            "boe_filename"
        ]:

            st.session_state.pop(key, None)

        with st.spinner(
            "PDF processing and extracting item details..."
        ):

            try:

                pdf_bytes = uploaded_file.getvalue()

                page_texts = extract_pdf_text(
                    pdf_bytes
                )

                header = extract_header(
                    page_texts,
                    pdf_bytes
                )

                detail, issues = parse_item_blocks(
                    page_texts
                )

                if detail.empty:

                    st.error(
                        "No item records extracted. "
                        "PDF may be scanned or have a different layout."
                    )

                    st.warning(
                        "OCR or a PDF-specific extraction pattern "
                        "may be required."
                    )

                else:

                    st.session_state["boe_header"] = header

                    st.session_state["boe_detail"] = detail

                    st.session_state["boe_issues"] = issues

                    st.session_state["boe_filename"] = uploaded_file.name

                    st.success(
                        f"{len(detail)} item blocks detected."
                    )

            except Exception as exc:

                st.error(
                    f"PDF processing error: {exc}"
                )


# ============================================================
# RESULTS SECTION
# ============================================================

if "boe_detail" in st.session_state:

    header = st.session_state["boe_header"]

    detail = st.session_state["boe_detail"]

    issues = st.session_state["boe_issues"]

    st.divider()

    st.subheader("2. Extracted BOE Details")

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "BOE Number",
        header.get("BOE No.") or "Not found"
    )

    c2.metric(
        "BOE Date",
        header.get("Date") or "Not found"
    )

    c3.metric(
        "Items detected",
        len(detail)
    )

    st.write(
        "**Exporter:**",
        header.get("EXPORTER") or "Not found"
    )

    st.write(
        "**Address:**",
        header.get("ADDRESS") or "Not found"
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    summary = create_summary(detail)

    st.subheader("3. HSN-wise GST Summary")

    if not summary.empty:

        display_summary = summary.copy()

        display_summary.insert(
            0,
            "SI",
            range(1, len(display_summary) + 1)
        )

        display_summary["GST Rate (%)"] = (
            display_summary["GST Rate (%)"]
            .map(lambda x: f"{x:g}%")
        )

        display_summary = display_summary.rename(
            columns={
                "Taxable_Amount": "TAXABLE AMOUNT",
                "Tax_Amount": "TAX AMOUNT"
            }
        )

        st.dataframe(
            display_summary[
                [
                    "SI",
                    "HSN",
                    "GST Rate (%)",
                    "Items",
                    "TAXABLE AMOUNT",
                    "TAX AMOUNT"
                ]
            ],
            use_container_width=True,
            hide_index=True
        )

        taxable_total = safe_sum(
            summary["Taxable_Amount"]
        )

        tax_total = safe_sum(
            summary["Tax_Amount"]
        )

        col1, col2 = st.columns(2)

        col1.metric(
            "Total Taxable Amount",
            f"₹{indian_number_format(taxable_total)}"
        )

        col2.metric(
            "Total IGST Amount",
            f"₹{indian_number_format(tax_total)}"
        )

    else:

        st.error(
            "HSN summary could not be generated."
        )

    # --------------------------------------------------------
    # ITEM DETAILS
    # --------------------------------------------------------

    st.subheader("4. Item-wise Extracted Details")

    st.dataframe(
        detail,
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------
    # ERROR REPORT
    # --------------------------------------------------------

    if issues:

        st.error(
            f"{len(issues)} item records require verification."
        )

        with st.expander(
            "⚠️ View Missing / Incorrect Item Details",
            expanded=True
        ):

            for issue in issues:
                st.write("•", issue)

    else:

        st.success(
            "All detected item records passed the extraction checks."
        )

    # --------------------------------------------------------
    # EXTRACTION COUNT CHECK
    # --------------------------------------------------------

    st.subheader("5. Extraction Validation")

    complete_count = len(
        detail[
            detail["Extraction Status"] == "OK"
        ]
    )

    incomplete_count = len(detail) - complete_count

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "Total Item Blocks",
        len(detail)
    )

    col2.metric(
        "Complete Items",
        complete_count
    )

    col3.metric(
        "Items Requiring Check",
        incomplete_count
    )

    # --------------------------------------------------------
    # DOWNLOAD EXCEL
    # --------------------------------------------------------

    excel_bytes, _ = make_excel(
        header,
        detail,
        issues
    )

    base_name = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        uploaded_file.name.rsplit(".", 1)[0]
    )

    download_name = (
        f"{base_name}_GST_Summary.xlsx"
    )

    st.divider()

    st.subheader("6. Download Excel")

    st.download_button(
        label="⬇️ Download Complete Excel Report",
        data=excel_bytes,
        file_name=download_name,
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=True
    )

    st.caption(
        "Excel contains GST Summary, Item Details and Checks sheets."
    )

else:

    st.markdown(
        """
        ### Workflow

        1. Upload Bill of Entry PDF.
        2. Click Extract Data.
        3. Check BOE Details.
        4. Verify HSN-wise GST Summary.
        5. Check incomplete item records.
        6. Download Excel Report.
        """
    )
