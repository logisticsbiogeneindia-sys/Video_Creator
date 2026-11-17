
import re
import io
from datetime import datetime

import fitz
import pandas as pd
import streamlit as st

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.utils import get_column_letter


# ==========================================================
# CONFIG
# ==========================================================

st.set_page_config(
    page_title="Biogene India - BOE Extractor",
    page_icon="📄",
    layout="wide"
)

st.title("📄 Biogene India - Bill of Entry Extractor")
st.caption("First Page GST Summary + Item-wise Extraction + Reconciliation")

NUMBER = r"-?\d[\d,]*(?:\.\d+)?"

ITEM_START = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d{8})\s+NOEXCISE\b(.*)$",
    re.IGNORECASE
)


# ==========================================================
# HELPERS
# ==========================================================

def clean(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "").replace("\xa0", " ")
    ).strip()


def num(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def numbers(text):
    return re.findall(NUMBER, text or "")


def money(value):
    if value is None:
        return ""
    return f"{value:,.2f}"


def safe_sum(series):
    return round(
        pd.to_numeric(series, errors="coerce").sum(),
        2
    )


# ==========================================================
# PDF READER
# ==========================================================

def read_pdf(pdf_bytes):

    try:
        doc = fitz.open(
            stream=pdf_bytes,
            filetype="pdf"
        )

        pages = []

        for page in doc:
            pages.append(
                page.get_text("text", sort=True) or ""
            )

        doc.close()

        return pages

    except Exception as e:
        raise RuntimeError(f"PDF reading error: {e}")


# ==========================================================
# POSITION-AWARE TEXT LINES
# ==========================================================

def get_position_lines(page):

    words = page.get_text("words", sort=True)

    grouped = {}

    for word in words:

        x0, y0, x1, y1, text = word[:5]

        key = round(y0, 1)

        grouped.setdefault(key, []).append(
            (x0, clean(text))
        )

    result = []

    for y in sorted(grouped):

        row = sorted(
            grouped[y],
            key=lambda x: x[0]
        )

        result.append(
            (y, " ".join(item[1] for item in row))
        )

    return result


# ==========================================================
# HEADER EXTRACTION
# ==========================================================

def extract_header(doc, pages):

    first_text = pages[0] if pages else ""

    be_no = ""
    be_date = ""

    # BOE number
    patterns = [
        r"(?:BE\s*No\.?|BOE\s*No\.?|Bill\s+of\s+Entry\s+No\.?)"
        r"\s*[:\-]?\s*(\d{5,})",

        r"\b(\d{5,})\s+(\d{1,2}[-/]\d{1,2}[-/]\d{4})"
    ]

    for pattern in patterns:

        m = re.search(
            pattern,
            first_text,
            re.IGNORECASE
        )

        if m:

            be_no = m.group(1)

            if len(m.groups()) > 1:
                be_date = m.group(2)

            break

    if not be_date:

        m = re.search(
            r"(?:BE\s*Date|BOE\s*Date|Date)"
            r"\s*[:\-]?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{4})",
            first_text,
            re.IGNORECASE
        )

        if m:
            be_date = m.group(1)

    # Exporter / address
    all_text = "\n".join(pages[:8])

    exporter = ""
    address = ""

    m = re.search(
        r"BIOLEGEND\s+INC\.?",
        all_text,
        re.IGNORECASE
    )

    if m:

        exporter = "BIOLEGEND INC."

        am = re.search(
            r"8999\s+BIOLEGEND\s+WAY"
            r"[\s,]+SAN\s+DIEGO"
            r"[\s,]+CA"
            r"[\s,]+92121"
            r"(?:[\s,]+(?:US|USA|UNITED STATES))?",
            all_text,
            re.IGNORECASE
        )

        if am:

            address = clean(am.group(0))

            if not re.search(
                r"UNITED STATES|USA|US$",
                address,
                re.IGNORECASE
            ):
                address += " UNITED STATES"

    if not exporter:

        m = re.search(
            r"EXPORTER\s*[:\-]?\s*(.+)",
            all_text,
            re.IGNORECASE
        )

        if m:
            exporter = clean(m.group(1).splitlines()[0])

    if be_date:

        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):

            try:
                be_date = datetime.strptime(
                    be_date,
                    fmt
                ).strftime("%d-%m-%Y")
                break

            except ValueError:
                pass

    return {
        "BOE No.": be_no,
        "Date": be_date,
        "EXPORTER": exporter,
        "ADDRESS": address
    }


# ==========================================================
# FIRST PAGE GST SUMMARY
# THIS IS THE MASTER SOURCE
# ==========================================================

def extract_first_page_gst(doc):

    if len(doc) == 0:
        return pd.DataFrame(), None, []

    page = doc[0]

    position_lines = get_position_lines(page)

    text = page.get_text("text", sort=True) or ""

    # Use position-based lines first
    lines = [line for _, line in position_lines]

    if not lines:
        lines = text.splitlines()

    records = []
    total_taxable = None
    total_igst = None
    issues = []

    # Find GST table area
    gst_start = None

    for i, line in enumerate(lines):

        if (
            re.search(r"\bHSN\b", line, re.IGNORECASE)
            and re.search(r"GST", line, re.IGNORECASE)
        ):
            gst_start = i
            break

    if gst_start is None:

        # Fallback if headings are split across PDF lines
        for i, line in enumerate(lines):

            if re.search(
                r"\bHSN\b",
                line,
                re.IGNORECASE
            ):
                gst_start = i
                break

    if gst_start is None:

        return pd.DataFrame(), None, [
            "First page GST table heading not detected."
        ]

    # Process lines following GST heading
    for line in lines[gst_start + 1:]:

        line = clean(line)

        if not line:
            continue

        # Skip heading fragments
        if re.search(
            r"DETAILS|TAXABLE|TAX AMOUNT|GST\s*@|HSN",
            line,
            re.IGNORECASE
        ):
            continue

        # GST row:
        # SI HSN RATE TAXABLE TAX
        # Example:
        # 1 30021500 5 2655868.00 132793.40
        row_pattern = re.compile(
            r"^\s*(?:\d+\s+)?"
            r"(?P<hsn>\d{8})\s+"
            r"(?P<rate>\d{1,2}(?:\.\d+)?)\s*%?\s+"
            r"(?P<taxable>[\d,]+\.\d{2})\s+"
            r"(?P<tax>[\d,]+\.\d{2})\s*$"
        )

        m = row_pattern.search(line)

        if m:

            records.append({

                "HSN": m.group("hsn"),

                "GST Rate (%)": float(
                    m.group("rate")
                ),

                "Taxable Amount": num(
                    m.group("taxable")
                ),

                "IGST Amount": num(
                    m.group("tax")
                ),

                "Source": "First Page PDF"

            })

            continue

        # Alternate: line may have SI separated from HSN
        # or a GST rate with decimal
        alt = re.search(
            r"(?<!\d)(\d{8})\s+"
            r"(\d{1,2}(?:\.\d+)?)\s*%?\s+"
            r"([\d,]+\.\d{2})\s+"
            r"([\d,]+\.\d{2})(?!\d)",
            line
        )

        if alt:

            records.append({

                "HSN": alt.group(1),

                "GST Rate (%)": float(alt.group(2)),

                "Taxable Amount": num(alt.group(3)),

                "IGST Amount": num(alt.group(4)),

                "Source": "First Page PDF"

            })

            continue

        # Total row: no HSN, two monetary values
        # Stop after finding total
        if re.search(
            r"\bTOTAL\b|\bGRAND\s+TOTAL\b",
            line,
            re.IGNORECASE
        ):

            vals = numbers(line)

            if len(vals) >= 2:

                total_taxable = num(vals[-2])
                total_igst = num(vals[-1])

                break

    # Fallback total detection:
    # Find amount pair below GST rows, without HSN
    if records and (
        total_taxable is None or total_igst is None
    ):

        first_hsn_index = None

        for i, line in enumerate(lines):

            if re.search(r"\b\d{8}\b", line):
                first_hsn_index = i
                break

        if first_hsn_index is not None:

            for line in lines[first_hsn_index + 1:]:

                line = clean(line)

                if re.search(
                    r"\b\d{8}\b",
                    line
                ):
                    continue

                vals = numbers(line)

                if len(vals) == 2:

                    a = num(vals[0])
                    b = num(vals[1])

                    if (
                        a is not None
                        and b is not None
                        and a > 100
                        and b > 0
                    ):

                        total_taxable = a
                        total_igst = b

    # Duplicate HSN rows must not silently disappear
    if records:

        df = pd.DataFrame(records)

        duplicates = df.duplicated(
            subset=[
                "HSN",
                "GST Rate (%)",
                "Taxable Amount",
                "IGST Amount"
            ]
        )

        if duplicates.any():

            df = df.drop_duplicates(
                subset=[
                    "HSN",
                    "GST Rate (%)",
                    "Taxable Amount",
                    "IGST Amount"
                ]
            )

        return df.reset_index(drop=True), {
            "PDF Total Taxable": total_taxable,
            "PDF Total IGST": total_igst
        }, issues

    return pd.DataFrame(), {
        "PDF Total Taxable": total_taxable,
        "PDF Total IGST": total_igst
    }, [
        "No HSN-wise GST rows could be read from the first page. "
        "Check the PDF text layout."
    ]


# ==========================================================
# ITEM EXTRACTION
# ==========================================================

def extract_assessable(block):

    pattern = re.compile(
        r"\bY\s+N\s+Y\s+N\s+Y\s+Y\s+"
        rf"({NUMBER})\s+"
        rf"({NUMBER})",
        re.IGNORECASE | re.DOTALL
    )

    m = pattern.search(block)

    if m:
        return num(m.group(1)), num(m.group(2))

    return None, None


def extract_labeled(block, label, minimum):

    lines = block.splitlines()

    for i, line in enumerate(lines):

        m = re.search(
            rf"\b{re.escape(label)}\b",
            line,
            re.IGNORECASE
        )

        if not m:
            continue

        values = numbers(line[m.end():])

        if len(values) < minimum:

            for next_line in lines[i + 1:i + 4]:

                if re.search(
                    r"\b(?:Rate|Amount|Assessable|Description)\b",
                    next_line,
                    re.IGNORECASE
                ):
                    break

                values.extend(numbers(next_line))

                if len(values) >= minimum:
                    break

        if len(values) >= minimum:
            return [num(x) for x in values]

    return []


def parse_items(pages):

    full_text = "\n".join(pages)

    lines = full_text.splitlines()

    starts = []

    for i, line in enumerate(lines):

        m = ITEM_START.match(line)

        if m:
            starts.append((i, m))

    rows = []
    issues = []

    if not starts:

        return pd.DataFrame(), [
            "Item start pattern was not found."
        ]

    for index, (start, match) in enumerate(starts):

        end = (
            starts[index + 1][0]
            if index + 1 < len(starts)
            else len(lines)
        )

        block_lines = lines[start:end]

        block = "\n".join(block_lines)

        invoice_si = int(match.group(1))
        item_si = int(match.group(2))
        hsn = match.group(3)

        description = clean(match.group(4))

        if not description:
            description = "Description not captured"

        assessable, total_duty = extract_assessable(
            block
        )

        rates = extract_labeled(
            block,
            "Rate",
            5
        )

        amounts = extract_labeled(
            block,
            "Amount",
            3
        )

        igst_rate = rates[2] if len(rates) >= 5 else None

        bcd = amounts[0] if len(amounts) >= 3 else None
        sws = amounts[1] if len(amounts) >= 3 else None
        igst = amounts[2] if len(amounts) >= 3 else None

        taxable = None

        if all(x is not None for x in [assessable, bcd, sws]):

            taxable = round(
                assessable + bcd + sws,
                2
            )

        missing = []

        fields = {
            "Assessable Value": assessable,
            "Total Duty": total_duty,
            "GST Rate": igst_rate,
            "BCD": bcd,
            "SWS": sws,
            "IGST": igst,
            "GST Taxable Amount": taxable
        }

        for field, value in fields.items():

            if value is None:
                missing.append(field)

        status = (
            "OK"
            if not missing
            else "CHECK REQUIRED"
        )

        if missing:

            issues.append(
                f"Item SI {item_si}, HSN {hsn}: "
                + ", ".join(missing)
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
            "GST Taxable Amount": taxable,
            "IGST Amount": igst,
            "Total Duty": total_duty,
            "Extraction Status": status,
            "Missing Fields": ", ".join(missing)

        })

    return pd.DataFrame(rows), issues


# ==========================================================
# RECONCILIATION
# ==========================================================

def reconcile(first_summary, pdf_totals, detail):

    results = []

    # First page summary totals
    summary_taxable = (
        safe_sum(first_summary["Taxable Amount"])
        if not first_summary.empty else None
    )

    summary_igst = (
        safe_sum(first_summary["IGST Amount"])
        if not first_summary.empty else None
    )

    pdf_taxable = (
        pdf_totals.get("PDF Total Taxable")
        if pdf_totals else None
    )

    pdf_igst = (
        pdf_totals.get("PDF Total IGST")
        if pdf_totals else None
    )

    item_taxable = (
        safe_sum(detail["GST Taxable Amount"])
        if not detail.empty else None
    )

    item_igst = (
        safe_sum(detail["IGST Amount"])
        if not detail.empty else None
    )

    def add(label, a, b, description):

        difference = (
            round(a - b, 2)
            if a is not None and b is not None
            else None
        )

        status = (
            "PASS"
            if difference is not None and abs(difference) <= 0.05
            else "CHECK"
        )

        results.append({

            "Check": label,
            "First / Master Value": a,
            "Compared Value": b,
            "Difference": difference,
            "Status": status,
            "Description": description

        })

    add(
        "First Page Taxable Total vs HSN Rows",
        pdf_taxable,
        summary_taxable,
        "PDF total against HSN summary rows"
    )

    add(
        "First Page IGST Total vs HSN Rows",
        pdf_igst,
        summary_igst,
        "PDF IGST total against HSN summary rows"
    )

    add(
        "Item Taxable vs First Page",
        summary_taxable,
        item_taxable,
        "Item level taxable total against first page"
    )

    add(
        "Item IGST vs First Page",
        summary_igst,
        item_igst,
        "Item level IGST against first page"
    )

    return pd.DataFrame(results)


# ==========================================================
# EXCEL STYLING
# ==========================================================

def style_sheet(ws):

    ws.sheet_view.showGridLines = False

    for cell in ws[1]:

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

    ws.freeze_panes = "A2"

    ws.auto_filter.ref = ws.dimensions

    for column in ws.columns:

        max_length = max(
            len(str(cell.value or ""))
            for cell in column
        )

        ws.column_dimensions[
            get_column_letter(column[0].column)
        ].width = min(max_length + 4, 45)


# ==========================================================
# EXCEL GENERATOR
# ==========================================================

def make_excel(header, gst, pdf_totals, detail, checks):

    wb = Workbook()

    # GST SUMMARY
    ws = wb.active
    ws.title = "GST Summary"

    header_rows = [
        ("BOE No.", ":", header.get("BOE No.", "")),
        ("Date", ":", header.get("Date", "")),
        ("EXPORTER", ":", header.get("EXPORTER", "")),
        ("ADDRESS", ":", header.get("ADDRESS", ""))
    ]

    for r, values in enumerate(header_rows, 1):

        for c, value in enumerate(values, 1):
            ws.cell(r, c, value)

        ws.merge_cells(
            start_row=r,
            start_column=3,
            end_row=r,
            end_column=6
        )

    ws.append([])
    ws.append([
        "DETAILS",
        "SI",
        "HSN",
        "GST @ %",
        "TAXABLE AMOUNT",
        "TAX AMOUNT"
    ])

    for cell in ws[6]:

        cell.font = Font(
            bold=True,
            color="FFFFFF"
        )

        cell.fill = PatternFill(
            "solid",
            fgColor="1F4E78"
        )

    for i, row in gst.iterrows():

        ws.append([
            i + 1,
            "",
            str(row["HSN"]),
            f'{row["GST Rate (%)"]:g}%',
            row["Taxable Amount"],
            row["IGST Amount"]
        ])

    total_row = ws.max_row + 1

    ws.cell(
        total_row,
        5,
        pdf_totals.get("PDF Total Taxable")
        if pdf_totals else None
    )

    ws.cell(
        total_row,
        6,
        pdf_totals.get("PDF Total IGST")
        if pdf_totals else None
    )

    for cell in ws[total_row]:

        cell.font = Font(bold=True)

        cell.fill = PatternFill(
            "solid",
            fgColor="D9EAF7"
        )

    for row in ws.iter_rows(
        min_row=7,
        max_row=total_row,
        min_col=5,
        max_col=6
    ):

        for cell in row:
            cell.number_format = '#,##,##0.00'

    for col, width in {
        "A": 18,
        "B": 10,
        "C": 18,
        "D": 15,
        "E": 23,
        "F": 20
    }.items():

        ws.column_dimensions[col].width = width

    ws.freeze_panes = "A7"
    ws.sheet_view.showGridLines = False

    # ITEM DETAILS
    wd = wb.create_sheet("Item Details")

    for row in [detail.columns.tolist()] + detail.values.tolist():
        wd.append([
            None if pd.isna(x) else x
            for x in row
        ])

    style_sheet(wd)

    # CHECKS
    wc = wb.create_sheet("Checks")

    for row in [checks.columns.tolist()] + checks.values.tolist():

        wc.append([
            None if pd.isna(x) else x
            for x in row
        ])

    style_sheet(wc)

    # FIRST PAGE RAW VALUES
    wr = wb.create_sheet("PDF Totals")

    wr.append(["Field", "PDF Value"])

    wr.append([
        "First Page Taxable Total",
        pdf_totals.get("PDF Total Taxable")
        if pdf_totals else None
    ])

    wr.append([
        "First Page IGST Total",
        pdf_totals.get("PDF Total IGST")
        if pdf_totals else None
    ])

    style_sheet(wr)

    output = io.BytesIO()

    wb.save(output)

    output.seek(0)

    return output.getvalue()


# ==========================================================
# STREAMLIT UI
# ==========================================================

uploaded_file = st.file_uploader(
    "Upload Bill of Entry PDF",
    type=["pdf"]
)

if uploaded_file:

    st.success(
        f"Uploaded: {uploaded_file.name}"
    )

    if st.button(
        "🔍 EXTRACT DATA",
        type="primary",
        use_container_width=True
    ):

        for key in [
            "header",
            "gst",
            "pdf_totals",
            "detail",
            "checks",
            "issues",
            "excel"
        ]:

            st.session_state.pop(key, None)

        with st.spinner(
            "Reading first page GST summary and item details..."
        ):

            try:

                pdf_bytes = uploaded_file.getvalue()

                doc = fitz.open(
                    stream=pdf_bytes,
                    filetype="pdf"
                )

                pages = [
                    page.get_text("text", sort=True) or ""
                    for page in doc
                ]

                header = extract_header(
                    doc,
                    pages
                )

                gst, pdf_totals, gst_issues = (
                    extract_first_page_gst(doc)
                )

                detail, item_issues = parse_items(
                    pages
                )

                checks = reconcile(
                    gst,
                    pdf_totals,
                    detail
                )

                issues = gst_issues + item_issues

                excel = make_excel(
                    header,
                    gst,
                    pdf_totals,
                    detail,
                    checks
                )

                st.session_state["header"] = header
                st.session_state["gst"] = gst
                st.session_state["pdf_totals"] = pdf_totals
                st.session_state["detail"] = detail
                st.session_state["checks"] = checks
                st.session_state["issues"] = issues
                st.session_state["excel"] = excel

                doc.close()

            except Exception as e:

                st.error(
                    f"Processing Error: {e}"
                )


# ==========================================================
# DISPLAY RESULTS
# ==========================================================

if "gst" in st.session_state:

    header = st.session_state["header"]
    gst = st.session_state["gst"]
    pdf_totals = st.session_state["pdf_totals"]
    detail = st.session_state["detail"]
    checks = st.session_state["checks"]
    issues = st.session_state["issues"]

    st.divider()

    st.subheader("1. BOE Details")

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "BOE Number",
        header["BOE No."] or "Not Found"
    )

    c2.metric(
        "Date",
        header["Date"] or "Not Found"
    )

    c3.metric(
        "Items Detected",
        len(detail)
    )

    st.write("**Exporter:**", header["EXPORTER"])
    st.write("**Address:**", header["ADDRESS"])

    st.divider()

    # FIRST PAGE MASTER SUMMARY
    st.subheader("2. First Page HSN-wise GST Summary")

    if not gst.empty:

        st.dataframe(
            gst,
            use_container_width=True,
            hide_index=True
        )

        st.metric(
            "First Page IGST Total",
            (
                f'₹{money(pdf_totals["PDF Total IGST"])}'
                if pdf_totals and pdf_totals.get("PDF Total IGST") is not None
                else "Not detected"
            )
        )

    else:

        st.error(
            "First Page GST Summary could not be extracted."
        )

    # ITEM DETAILS
    st.subheader("3. Item-wise Details")

    if not detail.empty:

        st.dataframe(
            detail,
            use_container_width=True,
            hide_index=True
        )

    else:

        st.warning(
            "No item blocks were detected."
        )

    # RECONCILIATION
    st.subheader("4. Reconciliation Checks")

    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True
    )

    if not gst.empty and pdf_totals:

        master = pdf_totals.get("PDF Total IGST")

        if master is not None:

            extracted = safe_sum(
                gst["IGST Amount"]
            )

            difference = round(
                master - extracted,
                2
            )

            if abs(difference) <= 0.05:

                st.success(
                    f"First Page IGST matched. Difference: ₹{money(difference)}"
                )

            else:

                st.error(
                    f"First Page IGST mismatch. Difference: ₹{money(difference)}"
                )

        else:

            st.warning(
                "PDF total IGST could not be independently detected."
            )

    # ISSUES
    if issues:

        with st.expander(
            f"⚠️ Extraction Issues ({len(issues)})",
            expanded=True
        ):

            for issue in issues:
                st.write("•", issue)

    # DOWNLOAD
    st.divider()

    st.subheader("5. Download Excel")

    base_name = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        uploaded_file.name.rsplit(".", 1)[0]
    )

    st.download_button(
        "⬇️ Download Complete Excel Report",
        data=st.session_state["excel"],
        file_name=f"{base_name}_BOE_Report.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=True
    )

else:

    st.info(
        "Upload your Bill of Entry PDF and click EXTRACT DATA."
    )
