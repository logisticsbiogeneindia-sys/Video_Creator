
import streamlit as st
import fitz
import pandas as pd
import re
import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

st.set_page_config(page_title="Biogene BOE Extractor", layout="wide")

st.title("BIOGENE INDIA | BILL OF ENTRY DATA EXTRACTOR")
st.caption("First Page GST + Item-wise 5. IGST Duty Extraction")


# =====================================================
# COMMON FUNCTIONS
# =====================================================

def clean(s):
    return re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ")).strip()


def to_num(s):
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "").replace("₹", "").strip())
    except:
        return None


def money(s):
    if s is None:
        return ""
    return f"{s:,.2f}"


NUM = r"-?\d[\d,]*(?:\.\d+)?"


# =====================================================
# PDF EXTRACTION
# =====================================================

def read_pdf(pdf_bytes):

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    pages = []

    for page in doc:

        # Preserve original text and layout
        text = page.get_text("text", sort=True)

        pages.append(text or "")

    return doc, pages


# =====================================================
# FIRST PAGE HEADER
# =====================================================

def extract_header(text):

    result = {
        "BOE No.": "",
        "Date": "",
        "Exporter": "",
        "Address": ""
    }

    patterns = [
        r"(?:BE\s*No\.?|BOE\s*No\.?|Bill\s+of\s+Entry\s+No\.?)\s*[:\-]?\s*(\d{5,})",
        r"\b(500\d{4})\b"
    ]

    for p in patterns:

        m = re.search(p, text, re.I)

        if m:
            result["BOE No."] = m.group(1)
            break

    m = re.search(
        r"(?:BE\s*Date|BOE\s*Date|Date)\s*[:\-]?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{4})",
        text,
        re.I
    )

    if m:
        result["Date"] = m.group(1)

    m = re.search(r"BIOLEGEND\s+INC\.?", text, re.I)

    if m:
        result["Exporter"] = "BIOLEGEND INC."

    m = re.search(
        r"8999\s+BIOLEGEND\s+WAY.{0,120}?(?:92121)",
        text,
        re.I | re.S
    )

    if m:
        result["Address"] = clean(m.group(0))

    return result


# =====================================================
# FIRST PAGE GST SUMMARY
# =====================================================

def extract_first_page_gst(text):

    rows = []

    lines = [
        clean(x)
        for x in text.splitlines()
        if clean(x)
    ]

    # HSN and GST rows are detected independently.
    # Expected row:
    # 1 30021500 5 2655868.00 132793.40

    row_regex = re.compile(
        r"^\s*(\d+)\s+"
        r"(\d{8})\s+"
        r"(\d{1,2}(?:\.\d+)?)\s*%?\s+"
        r"([\d,]+\.\d{2})\s+"
        r"([\d,]+\.\d{2})\s*$"
    )

    for line in lines:

        m = row_regex.match(line)

        if m:

            rows.append({
                "SI": int(m.group(1)),
                "HSN": m.group(2),
                "GST Rate (%)": float(m.group(3)),
                "Taxable Amount": to_num(m.group(4)),
                "IGST Amount": to_num(m.group(5)),
                "Source": "First Page"
            })

            continue

        # Alternate row without SI
        m = re.match(
            r"^\s*(\d{8})\s+"
            r"(\d{1,2}(?:\.\d+)?)\s*%?\s+"
            r"([\d,]+\.\d{2})\s+"
            r"([\d,]+\.\d{2})\s*$",
            line
        )

        if m:

            rows.append({
                "SI": len(rows) + 1,
                "HSN": m.group(1),
                "GST Rate (%)": float(m.group(2)),
                "Taxable Amount": to_num(m.group(3)),
                "IGST Amount": to_num(m.group(4)),
                "Source": "First Page"
            })

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.drop_duplicates(
            subset=["HSN", "GST Rate (%)", "Taxable Amount", "IGST Amount"]
        )

    # First-page total IGST
    total_igst = None
    total_taxable = None

    for i, line in enumerate(lines):

        if re.search(r"GRAND\s+TOTAL|TOTAL", line, re.I):

            vals = re.findall(NUM, line)

            if len(vals) >= 2:

                total_taxable = to_num(vals[-2])
                total_igst = to_num(vals[-1])

                break

            # Sometimes total is printed on the next line
            for nxt in lines[i + 1:i + 4]:

                vals = re.findall(NUM, nxt)

                if len(vals) >= 2:

                    total_taxable = to_num(vals[-2])
                    total_igst = to_num(vals[-1])

                    break

            if total_igst is not None:
                break

    # If label detection failed, find a total matching GST row sums
    if not df.empty and total_igst is None:

        sum_tax = round(df["IGST Amount"].sum(), 2)
        sum_taxable = round(df["Taxable Amount"].sum(), 2)

        for line in lines:

            vals = re.findall(NUM, line)

            if len(vals) >= 2:

                a = to_num(vals[-2])
                b = to_num(vals[-1])

                if (
                    a is not None and b is not None
                    and abs(a - sum_taxable) <= 0.05
                    and abs(b - sum_tax) <= 0.05
                ):

                    total_taxable = a
                    total_igst = b
                    break

    return df, total_taxable, total_igst


# =====================================================
# ITEM BLOCK DETECTION
# =====================================================

ITEM_START = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d{8})\s+NOEXCISE\b(.*)$",
    re.I
)


def split_item_blocks(pages):

    all_lines = []

    for page_no, text in enumerate(pages, 1):

        for line in text.splitlines():

            all_lines.append((page_no, clean(line)))

    starts = []

    for i, (page_no, line) in enumerate(all_lines):

        m = ITEM_START.match(line)

        if m:
            starts.append((i, page_no, m))

    blocks = []

    for idx, (start, page_no, match) in enumerate(starts):

        end = (
            starts[idx + 1][0]
            if idx + 1 < len(starts)
            else len(all_lines)
        )

        block = [
            line
            for _, line in all_lines[start:end]
            if line
        ]

        blocks.append({
            "page": page_no,
            "match": match,
            "lines": block,
            "text": "\n".join(block)
        })

    return blocks


# =====================================================
# EXTRACT ONLY 5. IGST SECTION
# =====================================================

def extract_igst_section(block_text):

    lines = [
        clean(x)
        for x in block_text.splitlines()
        if clean(x)
    ]

    start = None

    for i, line in enumerate(lines):

        if re.search(
            r"(?:5\s*[\.\)]\s*)?IGST\b",
            line,
            re.I
        ):

            start = i
            break

    if start is None:
        return None, None, "5. IGST section not found", ""

    section_lines = lines[start:start + 18]

    # Stop when next numbered duty section begins
    selected = []

    for line in section_lines:

        if selected and re.match(
            r"^\s*[6-9]\s*[\.\)]",
            line
        ):
            break

        selected.append(line)

    section = "\n".join(selected)

    # Rate and Amount must be taken from the IGST section only
    rate = None
    amount = None

    # Pattern: Rate ... Amount ...
    for i, line in enumerate(selected):

        if re.search(r"\bRate\b", line, re.I):

            vals = re.findall(NUM, line)

            if vals:
                rate = to_num(vals[-1])

            if rate is None and i + 1 < len(selected):

                vals = re.findall(NUM, selected[i + 1])

                if vals:
                    rate = to_num(vals[-1])

        if re.search(r"\bAmount\b", line, re.I):

            vals = re.findall(NUM, line)

            if vals:
                amount = to_num(vals[-1])

            if amount is None and i + 1 < len(selected):

                vals = re.findall(NUM, selected[i + 1])

                if vals:
                    amount = to_num(vals[-1])

    # Some PDFs print headings and values in separate lines.
    # Read numeric values after Rate / Amount labels.
    if rate is None:

        m = re.search(
            r"\bRate\b\s*[:\-]?\s*(" + NUM + r")",
            section,
            re.I
        )

        if m:
            rate = to_num(m.group(1))

    if amount is None:

        m = re.search(
            r"\bAmount\b\s*[:\-]?\s*(" + NUM + r")",
            section,
            re.I
        )

        if m:
            amount = to_num(m.group(1))

    # Layout fallback: section contains Rate and Amount headings,
    # with values on subsequent lines.
    if rate is None or amount is None:

        flat = " ".join(selected)

        m = re.search(
            r"Rate\s+(" + NUM + r").{0,100}?Amount\s+(" + NUM + r")",
            flat,
            re.I
        )

        if m:

            if rate is None:
                rate = to_num(m.group(1))

            if amount is None:
                amount = to_num(m.group(2))

    if rate is None or amount is None:

        status = "IGST section found, Rate/Amount incomplete"

    else:

        status = "OK"

    return rate, amount, status, section


# =====================================================
# ITEM DETAILS
# =====================================================

def extract_items(pages):

    blocks = split_item_blocks(pages)

    records = []
    issues = []
    debug = []

    for item in blocks:

        m = item["match"]

        invoice_si = m.group(1)
        item_si = m.group(2)
        hsn = m.group(3)

        description = clean(m.group(4))

        rate, igst, status, section = extract_igst_section(
            item["text"]
        )

        debug.append({
            "Item SI": item_si,
            "HSN": hsn,
            "IGST Section": section
        })

        if status != "OK":

            issues.append(
                f"Item {item_si} / HSN {hsn}: {status}"
            )

        records.append({

            "Invoice SI": invoice_si,
            "Item SI": item_si,
            "HSN": hsn,
            "Description": description,
            "GST Rate (%)": rate,
            "IGST Amount": igst,
            "IGST Extraction Status": status,
            "Page": item["page"]

        })

    return (
        pd.DataFrame(records),
        issues,
        pd.DataFrame(debug)
    )


# =====================================================
# ITEM WISE HSN SUMMARY
# =====================================================

def item_hsn_summary(items):

    if items.empty:
        return pd.DataFrame()

    valid = items.dropna(
        subset=["GST Rate (%)", "IGST Amount"]
    ).copy()

    if valid.empty:
        return pd.DataFrame()

    summary = (
        valid.groupby(
            ["HSN", "GST Rate (%)"],
            as_index=False
        )
        .agg({
            "IGST Amount": "sum"
        })
    )

    return summary


# =====================================================
# RECONCILIATION
# =====================================================

def make_checks(first_page, items, first_total):

    results = []

    first_igst = (
        round(first_page["IGST Amount"].sum(), 2)
        if not first_page.empty else None
    )

    item_igst = (
        round(items["IGST Amount"].dropna().sum(), 2)
        if not items.empty else None
    )

    def add(name, a, b):

        diff = (
            round(a - b, 2)
            if a is not None and b is not None
            else None
        )

        status = (
            "MATCH"
            if diff is not None and abs(diff) <= 0.05
            else "MISMATCH / NOT FOUND"
        )

        results.append({
            "Check": name,
            "First Value": a,
            "Second Value": b,
            "Difference": diff,
            "Status": status
        })

    add(
        "First Page Total IGST vs HSN Rows",
        first_total,
        first_igst
    )

    add(
        "First Page IGST vs Item-wise IGST",
        first_total,
        item_igst
    )

    return pd.DataFrame(results)


# =====================================================
# EXCEL
# =====================================================

def create_excel(header, gst, items, checks, debug):

    wb = Workbook()

    ws = wb.active
    ws.title = "GST Summary"

    ws.append(["BOE FIELD", "VALUE"])

    for key, value in header.items():
        ws.append([key, value])

    ws.append([])
    ws.append([
        "SI",
        "HSN",
        "GST Rate (%)",
        "Taxable Amount",
        "IGST Amount",
        "Source"
    ])

    for _, row in gst.iterrows():

        ws.append([
            row["SI"],
            row["HSN"],
            row["GST Rate (%)"],
            row["Taxable Amount"],
            row["IGST Amount"],
            row["Source"]
        ])

    wi = wb.create_sheet("Item Details")

    wi.append(list(items.columns))

    for row in items.itertuples(index=False, name=None):
        wi.append(list(row))

    wc = wb.create_sheet("Checks")

    wc.append(list(checks.columns))

    for row in checks.itertuples(index=False, name=None):
        wc.append(list(row))

    wd = wb.create_sheet("IGST Debug")

    wd.append(list(debug.columns))

    for row in debug.itertuples(index=False, name=None):
        wd.append(list(row))

    for sheet in wb.worksheets:

        sheet.freeze_panes = "A2"
        sheet.sheet_view.showGridLines = False
        sheet.auto_filter.ref = sheet.dimensions

        for cell in sheet[1]:

            cell.font = Font(
                bold=True,
                color="FFFFFF"
            )

            cell.fill = PatternFill(
                "solid",
                fgColor="17365D"
            )

            cell.alignment = Alignment(
                horizontal="center",
                vertical="center"
            )

        for column in sheet.columns:

            width = max(
                len(str(c.value or ""))
                for c in column
            )

            sheet.column_dimensions[
                get_column_letter(column[0].column)
            ].width = min(width + 3, 50)

    output = io.BytesIO()

    wb.save(output)

    output.seek(0)

    return output.getvalue()


# =====================================================
# USER INTERFACE
# =====================================================

uploaded = st.file_uploader(
    "Upload Bill of Entry PDF",
    type=["pdf"]
)

if uploaded:

    if st.button(
        "EXTRACT COMPLETE DATA",
        type="primary",
        use_container_width=True
    ):

        try:

            doc, pages = read_pdf(
                uploaded.getvalue()
            )

            first_text = pages[0] if pages else ""

            header = extract_header(
                first_text
            )

            gst, first_taxable, first_igst = (
                extract_first_page_gst(first_text)
            )

            items, issues, debug = extract_items(
                pages
            )

            checks = make_checks(
                gst,
                items,
                first_igst
            )

            excel = create_excel(
                header,
                gst,
                items,
                checks,
                debug
            )

            st.session_state["result"] = {
                "header": header,
                "gst": gst,
                "items": items,
                "checks": checks,
                "issues": issues,
                "debug": debug,
                "first_taxable": first_taxable,
                "first_igst": first_igst,
                "excel": excel
            }

            doc.close()

        except Exception as e:

            st.error(
                f"Extraction Error: {e}"
            )


# =====================================================
# RESULTS
# =====================================================

if "result" in st.session_state:

    r = st.session_state["result"]

    st.divider()

    st.subheader("1. BOE Header")

    st.json(r["header"])

    st.subheader("2. First Page GST Summary")

    if not r["gst"].empty:

        st.dataframe(
            r["gst"],
            use_container_width=True,
            hide_index=True
        )

    else:

        st.error(
            "First Page GST table not detected. "
            "See First Page Raw Text below."
        )

    a, b = st.columns(2)

    a.metric(
        "First Page Taxable Total",
        (
            "₹" + money(r["first_taxable"])
            if r["first_taxable"] is not None
            else "NOT FOUND"
        )
    )

    b.metric(
        "First Page Total IGST",
        (
            "₹" + money(r["first_igst"])
            if r["first_igst"] is not None
            else "NOT FOUND"
        )
    )

    st.divider()

    st.subheader("3. Item-wise IGST Extraction")

    if not r["items"].empty:

        st.dataframe(
            r["items"],
            use_container_width=True,
            hide_index=True
        )

        item_total = r["items"]["IGST Amount"].dropna().sum()

        st.metric(
            "Total IGST from All Items",
            "₹" + money(item_total)
        )

    else:

        st.error("No Items Detected")

    st.divider()

    st.subheader("4. Reconciliation")

    st.dataframe(
        r["checks"],
        use_container_width=True,
        hide_index=True
    )

    if r["issues"]:

        with st.expander(
            f"Extraction Issues ({len(r['issues'])})",
            expanded=True
        ):

            for issue in r["issues"]:
                st.warning(issue)

    st.divider()

    st.subheader("5. IGST Debug - Actual Extracted Section")

    st.caption(
        "Is sheet mein har Item ka wahi text dikhega "
        "jahan se 5. IGST Rate aur Amount read kiya gaya."
    )

    st.dataframe(
        r["debug"],
        use_container_width=True,
        hide_index=True
    )

    st.divider()

    st.download_button(
        "DOWNLOAD COMPLETE EXCEL",
        data=r["excel"],
        file_name="Biogene_BOE_Complete_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

    # Raw first page helps diagnose a different PDF layout
    with st.expander("First Page Raw Extracted Text"):

        st.text_area(
            "First Page Text",
            value=(
                r["header"].get("BOE No.", "")
                + "\n\n"
                + str(r["gst"].to_string(index=False))
            ),
            height=200
        )
