import re
import io
from datetime import datetime

import fitz  # PyMuPDF
import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.utils import get_column_letter


st.set_page_config(
    page_title="Biogene India - Bill of Entry Extractor",
    page_icon="📄",
    layout="wide"
)

st.title("📄 Bill of Entry PDF → Excel Extractor")
st.caption("PDF upload karein, BOE details aur HSN-wise GST summary extract karein, phir Excel download karein.")

st.info(
    "Upload par file-selection window browser khud kholta hai. Download ke waqt Save As location popup "
    "Chrome ki download setting se control hota hai; Streamlit app ise zabardasti open nahi kar sakta."
)


def clean_lines(text):
    return [re.sub(r"[ \t]+", " ", x).strip() for x in text.splitlines() if x.strip()]


def extract_pdf_text(pdf_bytes):
    """Read each PDF page separately with PyMuPDF. No Poppler dependency."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [page.get_text("text", sort=True) or "" for page in doc]
    finally:
        doc.close()


def extract_first_page_gst(pdf_bytes):
    """Read the HSN/GST table from page 1 using text lines and word coordinates."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[0]
        words = page.get_text("words", sort=True)
        grouped = {}
        for w in words:
            x0, y0, x1, y1, txt = w[:5]
            key = round(y0 / 2.5) * 2.5
            grouped.setdefault(key, []).append((x0, str(txt)))

        lines = []
        for y in sorted(grouped):
            line = " ".join(t for _, t in sorted(grouped[y], key=lambda z: z[0]))
            lines.append(re.sub(r"\s+", " ", line).strip())

        # Also use PyMuPDF's sorted text lines as a fallback.
        raw = page.get_text("text", sort=True) or ""
        lines += [re.sub(r"\s+", " ", x).strip() for x in raw.splitlines() if x.strip()]

        row_patterns = [
            re.compile(r"^\s*(\d+)\s+(\d{8})\s+(\d{1,2}(?:\.\d+)?)\s*%?\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$"),
            re.compile(r"^\s*(\d{8})\s+(\d{1,2}(?:\.\d+)?)\s*%?\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$")
        ]
        records = []
        seen = set()
        for line in lines:
            m = row_patterns[0].match(line)
            if m:
                si, hsn, rate, taxable, tax = m.groups()
            else:
                m = row_patterns[1].match(line)
                if not m:
                    continue
                hsn, rate, taxable, tax = m.groups()
                si = len(records) + 1
            key = (hsn, rate, taxable, tax)
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "SI": int(si), "HSN": hsn,
                "GST Rate (%)": float(rate),
                "Taxable Amount": float(taxable.replace(",", "")),
                "IGST Amount": float(tax.replace(",", ""))
            })

        df = pd.DataFrame(records, columns=["SI", "HSN", "GST Rate (%)", "Taxable Amount", "IGST Amount"])

        # Total row is taken from the first page only. If no explicit total row is
        # machine-readable, the HSN rows remain available and their sum is shown
        # separately rather than inventing a PDF total.
        total_taxable = None
        total_igst = None
        for i, line in enumerate(lines):
            if re.search(r"\b(?:GRAND\s+)?TOTAL\b", line, re.I):
                nums = re.findall(r"-?\d[\d,]*(?:\.\d{2})", line)
                if len(nums) >= 2:
                    total_taxable = float(nums[-2].replace(",", ""))
                    total_igst = float(nums[-1].replace(",", ""))
                    break
                for nxt in lines[i+1:i+4]:
                    nums = re.findall(r"-?\d[\d,]*(?:\.\d{2})", nxt)
                    if len(nums) >= 2:
                        total_taxable = float(nums[-2].replace(",", ""))
                        total_igst = float(nums[-1].replace(",", ""))
                        break
                if total_igst is not None:
                    break
        return df, total_taxable, total_igst, raw
    finally:
        doc.close()


def extract_igst_from_component(block):
    """Read Rate and Amount only inside the explicitly numbered 5. IGST section.

    No generic GST-rate fallback is permitted. If the section/field is not
    unambiguously present in the PDF text, return None so it remains blank.
    """
    lines = clean_lines(block)
    start = None
    heading_remainder = ""
    for i, line in enumerate(lines):
        heading = re.match(r"^5\s*[.)]\s*IGST\b(.*)$", line, re.I)
        if heading:
            start = i
            heading_remainder = heading.group(1).strip()
            break
    if start is None:
        return None, None

    section = [heading_remainder] if heading_remainder else []
    for line in lines[start + 1:]:
        # The next numbered duty component terminates section 5.
        if re.match(r"^\s*[1-9]\s*[.)]\s*\S", line):
            break
        section.append(line)
    text = " ".join(section)

    def exact_field(label):
        # Require a field label followed by its numeric value. Do not search
        # outside the section and do not accept a percentage from another row.
        matches = re.findall(
            rf"(?<![A-Za-z]){label}\s*[:=]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
            text, re.I
        )
        if len(matches) != 1:
            return None
        return float(matches[0].replace(",", ""))

    return exact_field("Rate"), exact_field("Amount")


def first_match(pattern, text, default=""):
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else default


def extract_header(page_texts, pdf_bytes):
    # Use PDF text blocks for header fields because ICEGATE places values and
    # labels in separate columns, which may not remain on one line.
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    first_page_blocks = [b[4].strip() for b in doc[0].get_text("blocks") if b[4].strip()]

    be_no = ""
    be_date = ""
    for block in first_page_blocks:
        m = re.search(r"\b(\d{5,})\s+(\d{1,2}/\d{1,2}/\d{4})\b", re.sub(r"\s+", " ", block))
        if m:
            be_no, be_date = m.group(1), m.group(2)
            break

    exporter = ""
    address = ""
    # Supplier details normally appear in Part II, usually page 2.
    for page in list(doc)[:8]:
        for block in page.get_text("blocks"):
            block_text = re.sub(r"\s+", " ", block[4]).strip()
            if re.search(r"BIOLEGEND INC\.", block_text, re.IGNORECASE):
                exporter = "BIOLEGEND INC."
                address_match = re.search(
                    r"BIOLEGEND INC\.\s+(8999 BIOLEGEND WAY\s+SAN DIEGO CA 92121(?:\s+UNITED STATES)?)",
                    block_text,
                    re.IGNORECASE
                )
                if address_match:
                    address = re.sub(r"\s+", " ", address_match.group(1)).strip()
                break
        if exporter:
            break

    if not address:
        # Fallback: locate the supplier block from page text.
        all_text = "\n".join(page_texts[:8])
        m = re.search(
            r"BIOLEGEND INC\.\s*\n\s*(8999 BIOLEGEND WAY)\s*\n\s*(SAN DIEGO CA 92121)",
            all_text,
            re.IGNORECASE
        )
        if m:
            exporter = "BIOLEGEND INC."
            address = f"{m.group(1)} {m.group(2)} UNITED STATES"

    if address and re.search(r"UNITED STATES", "\\n".join(page_texts[:8]), re.IGNORECASE):
        if "UNITED STATES" not in address.upper():
            address += " UNITED STATES"

    if be_date:
        try:
            be_date = datetime.strptime(be_date, "%d/%m/%Y").strftime("%d-%m-%Y")
        except ValueError:
            pass

    return {
        "BOE No.": be_no,
        "Date": be_date,
        "EXPORTER": exporter,
        "ADDRESS": address
    }

ITEM_START = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d{8})\s+NOEXCISE\b(.*)$",
    flags=re.IGNORECASE
)


def parse_item_blocks(page_texts):
    # Preserve the original item-start logic and item fields. Only IGST rate and
    # amount are now sourced from the item's explicit 5. IGST component.
    full_text = "\n".join(page_texts)
    lines = full_text.splitlines()
    starts = []
    for i, line in enumerate(lines):
        match = ITEM_START.match(line)
        if match:
            starts.append((i, match))

    rows = []
    parse_issues = []

    for index, (start_line, match) in enumerate(starts):
        end_line = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        block_lines = lines[start_line:end_line]
        block = "\n".join(block_lines)

        invoice_si = int(match.group(1))
        item_si = int(match.group(2))
        hsn = match.group(3)
        description = re.sub(r"\s+N\s+N\s+Y\s+N\s+N\s*$", "", match.group(4), flags=re.IGNORECASE).strip()
        description = re.sub(r"\s+", " ", description).strip() or "Description not captured"

        assess_match = re.search(
            r"Y\s+N\s+Y\s+N\s+Y\s+Y\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)",
            block, re.I
        )
        amount_match = re.search(
            r"\bAmount\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)",
            block, re.I
        )

        def num(value):
            return float(value.replace(",", ""))

        assessable = num(assess_match.group(1)) if assess_match else None
        total_duty = num(assess_match.group(2)) if assess_match else None
        bcd = num(amount_match.group(1)) if amount_match else None
        sws = num(amount_match.group(2)) if amount_match else None

        igst_rate, igst = extract_igst_from_component(block)

        # Compatibility fallback: if this PDF prints the IGST component's
        # Rate/Amount labels in a wrapped form, use the third value from the
        # original duty rows only when an explicit 5. IGST heading exists.
        if igst_rate is None or igst is None:
            igst_heading = re.search(r"(?:^|\n)\s*5\s*[.)]?\s*IGST\b", block, re.I)
            if igst_heading and amount_match:
                # Do not silently substitute unrelated values. Leave missing
                # values blank so the reconciliation exposes the issue.
                pass

        # Taxable amount is NOT calculated. It is taken from the PDF's first-page
        # GST summary only; item-level value remains blank unless explicitly parsed.
        gst_taxable = None

        missing = []
        for label, value in [
            ("Assessable Value", assessable), ("BCD", bcd), ("SWS", sws),
            ("5. IGST Rate", igst_rate), ("5. IGST Amount", igst)
        ]:
            if value is None:
                missing.append(label)

        if missing:
            parse_issues.append(
                f"Item SI {item_si} (HSN {hsn}): missing " + ", ".join(missing)
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
            "Total Duty": total_duty
        })

    return pd.DataFrame(rows), parse_issues


def indian_number_format(value):
    return f"{value:,.2f}"


def make_excel(header, detail, first_page_gst=None, first_page_total_taxable=None, first_page_total_igst=None):
    # Excel generation only: keep Streamlit display code outside this function.
    if first_page_gst is None:
        first_page_gst = pd.DataFrame()

    # The GST summary must reproduce page 1, not a reconstructed/calculated table.
    summary = first_page_gst.copy() if first_page_gst is not None else pd.DataFrame()
    if not summary.empty:
        summary = summary.rename(columns={
            "Taxable Amount": "Taxable_Amount",
            "IGST Amount": "Tax_Amount"
        })
        summary["Items"] = ""
    else:
        summary = pd.DataFrame(columns=["SI", "HSN", "GST Rate (%)", "Taxable_Amount", "Tax_Amount", "Items"])

    wb = Workbook()
    ws = wb.active
    ws.title = "GST Summary"

    widths = {"A": 18, "B": 5, "C": 18, "D": 18, "E": 23, "F": 20}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    header_rows = [
        ("BOE No.", ":", header.get("BOE No.", "")),
        ("Date", ":", header.get("Date", "")),
        ("EXPORTER", ":", header.get("EXPORTER", "")),
        ("ADDRESS", ":", header.get("ADDRESS", "")),
    ]
    for r, values in enumerate(header_rows, start=1):
        for c, value in enumerate(values, start=1):
            ws.cell(r, c, value)
            ws.cell(r, c).alignment = Alignment(vertical="center", wrap_text=True)
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=6)
        ws.row_dimensions[r].height = 27 if r == 4 else 22

    ws.row_dimensions[5].height = 12
    table_headers = ["DETAILS", "SI", "HSN", "GST @ %", "TAXABLE AMOUNT", "TAX AMOUNT"]
    for col, value in enumerate(table_headers, start=1):
        cell = ws.cell(6, col, value)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for idx, row in summary.iterrows():
        r = 7 + idx
        ws.cell(r, 1, idx + 1)
        ws.cell(r, 2, "")
        ws.cell(r, 3, str(row["HSN"]))
        ws.cell(r, 4, f'{row["GST Rate (%)"]:g}%')
        ws.cell(r, 5, float(row["Taxable_Amount"]))
        ws.cell(r, 6, float(row["Tax_Amount"]))
        for col in range(1, 7):
            ws.cell(r, col).alignment = Alignment(
                vertical="center",
                horizontal="right" if col in (5, 6) else "center"
            )
        ws.cell(r, 5).number_format = '#,##,##0.00'
        ws.cell(r, 6).number_format = '#,##,##0.00'

    total_row = 7 + len(summary) + 1
    ws.cell(total_row, 5, first_page_total_taxable)
    ws.cell(total_row, 6, first_page_total_igst)
    for col in range(1, 7):
        cell = ws.cell(total_row, col)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9EAF7")
        cell.alignment = Alignment(horizontal="right" if col in (5, 6) else "center")
        cell.border = Border(top=Side(style="thin", color="1F1F1F"))
    ws.cell(total_row, 5).number_format = '#,##,##0.00'
    ws.cell(total_row, 6).number_format = '#,##,##0.00'

    note_row = total_row + 2
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=6)
    ws.cell(
        note_row, 1,
        "GST rates, taxable amounts and IGST totals in this sheet are transcribed from the PDF first-page GST summary. "
        "Item-level IGST Rate and Amount are read only from the explicit 5. IGST section. Missing values are left blank."
    )
    ws.cell(note_row, 1).font = Font(italic=True, size=9, color="666666")
    ws.cell(note_row, 1).alignment = Alignment(wrap_text=True)
    ws.row_dimensions[note_row].height = 30

    thin = Side(style="thin", color="B7C9D6")
    for row in ws.iter_rows(min_row=1, max_row=total_row, min_col=1, max_col=6):
        for cell in row:
            cell.border = Border(bottom=thin)

    ws.freeze_panes = "A7"
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_area = f"A1:F{note_row}"

    # First-page GST table is kept separately as the PDF's own declared summary.
    if first_page_gst is not None:
        wf = wb.create_sheet("First Page GST")
        wf.append(["SI", "HSN", "GST Rate (%)", "Taxable Amount", "IGST Amount"])
        for c in wf[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F4E78")
        for row in first_page_gst.itertuples(index=False, name=None):
            wf.append(list(row))
        wf.append(["PDF TOTAL", "", "", first_page_total_taxable, first_page_total_igst])
        wf.column_dimensions["A"].width = 14
        wf.column_dimensions["B"].width = 16
        wf.column_dimensions["C"].width = 18
        wf.column_dimensions["D"].width = 22
        wf.column_dimensions["E"].width = 20
        wf.freeze_panes = "A2"
        wf.sheet_view.showGridLines = False

    # All extracted line-item data for audit / verification.
    wd = wb.create_sheet("Item Details")
    for col, name in enumerate(detail.columns, start=1):
        cell = wd.cell(1, col, name)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    for row_idx, row in enumerate(detail.itertuples(index=False), start=2):
        for col_idx, value in enumerate(row, start=1):
            wd.cell(row_idx, col_idx, value)
            if isinstance(value, (int, float)) and col_idx >= 6:
                wd.cell(row_idx, col_idx).number_format = '#,##,##0.00'

    detail_widths = [12, 10, 14, 58, 14, 18, 14, 14, 20, 18, 16]
    for idx, width in enumerate(detail_widths, start=1):
        wd.column_dimensions[get_column_letter(idx)].width = width
    wd.freeze_panes = "A2"
    wd.auto_filter.ref = f"A1:{get_column_letter(len(detail.columns))}{len(detail) + 1}"
    wd.sheet_view.showGridLines = False

    # Validation / reconciliation sheet
    wr = wb.create_sheet("Checks")
    checks = [
        ("Total extracted items", len(detail)),
        ("PDF first-page taxable total", first_page_total_taxable),
        ("PDF first-page IGST total", first_page_total_igst),
        ("Total assessable value", float(detail["Assessable Value"].sum())),
    ]
    wr.append(["Check", "Value"])
    for c in wr[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")
    for item in checks:
        wr.append(list(item))
    wr.column_dimensions["A"].width = 30
    wr.column_dimensions["B"].width = 22
    for row in range(3, 6):
        wr.cell(row, 2).number_format = '#,##,##0.00'

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue(), summary


uploaded_file = st.file_uploader(
    "1. Bill of Entry PDF upload karein",
    type=["pdf"],
    accept_multiple_files=False,
    help="ICEGATE Bill of Entry PDF upload karein. Text-based PDF best result dega."
)

if uploaded_file is not None:
    st.success(f"Uploaded: {uploaded_file.name}")

    if st.button("🔍 Extract Data", type="primary", use_container_width=True):
        with st.spinner("PDF read ho rahi hai aur 160/available items extract ho rahe hain..."):
            try:
                pdf_bytes = uploaded_file.getvalue()
                page_texts = extract_pdf_text(pdf_bytes)
                header = extract_header(page_texts, pdf_bytes)
                first_page_gst, first_page_total_taxable, first_page_total_igst, first_page_raw = extract_first_page_gst(pdf_bytes)
                detail, issues = parse_item_blocks(page_texts)

                if detail.empty:
                    st.error(
                        "PDF se item data extract nahi hua. Ho sakta hai PDF scanned/image-based ho "
                        "ya uska layout supported format se alag ho."
                    )
                    st.warning("Aise PDF ke liye OCR support add karna hoga.")
                else:
                    st.session_state["boe_header"] = header
                    st.session_state["boe_detail"] = detail
                    st.session_state["boe_issues"] = issues
                    st.session_state["boe_filename"] = uploaded_file.name
                    st.session_state["boe_first_page_gst"] = first_page_gst
                    st.session_state["boe_first_page_total_taxable"] = first_page_total_taxable
                    st.session_state["boe_first_page_total_igst"] = first_page_total_igst
                    st.session_state["boe_first_page_raw"] = first_page_raw

            except Exception as exc:
                st.error(f"PDF process karte waqt error aaya: {exc}")

if "boe_detail" in st.session_state:
    header = st.session_state["boe_header"]
    detail = st.session_state["boe_detail"]
    issues = st.session_state.get("boe_issues", [])

    st.divider()
    st.subheader("2. Extracted BOE Details")
    c1, c2, c3 = st.columns(3)
    c1.metric("BOE Number", header.get("BOE No.") or "Not found")
    c2.metric("Date", header.get("Date") or "Not found")
    c3.metric("Items extracted", len(detail))

    st.write("**Exporter:**", header.get("EXPORTER") or "Not found")
    st.write("**Address:**", header.get("ADDRESS") or "Not found")

    first_page_gst = st.session_state.get("boe_first_page_gst", pd.DataFrame())
    first_page_total_taxable = st.session_state.get("boe_first_page_total_taxable")
    first_page_total_igst = st.session_state.get("boe_first_page_total_igst")

    st.subheader("3. First Page GST Summary (PDF se direct)")
    if not first_page_gst.empty:
        st.dataframe(first_page_gst, use_container_width=True, hide_index=True)
    else:
        st.warning("First page HSN-wise GST table text layout se detect nahi hui. Raw first-page text neeche available hai.")
    t1, t2 = st.columns(2)
    t1.metric("First Page Taxable Total", f"₹{first_page_total_taxable:,.2f}" if first_page_total_taxable is not None else "Not detected")
    t2.metric("First Page IGST Total", f"₹{first_page_total_igst:,.2f}" if first_page_total_igst is not None else "Not detected")

    st.subheader("4. HSN-wise GST Summary (PDF first page — direct extraction)")
    if not first_page_gst.empty:
        st.dataframe(first_page_gst, use_container_width=True, hide_index=True)
    else:
        st.error("PDF ke first page se GST summary reliably extract nahi hui. Koi GST rate/amount assume nahi kiya gaya.")

    st.subheader("5. Item-wise Extracted Details")
    st.dataframe(detail, use_container_width=True, hide_index=True)

    if issues:
        with st.expander(f"⚠️ Items needing manual verification ({len(issues)})"):
            for issue in issues:
                st.write("•", issue)

    excel_bytes, _ = make_excel(header, detail, first_page_gst, first_page_total_taxable, first_page_total_igst)
    base_name = re.sub(r"[^A-Za-z0-9_-]+", "_", uploaded_file.name.rsplit(".", 1)[0])
    download_name = f"{base_name}_GST_Summary.xlsx"

    st.divider()
    st.subheader("5. Download Excel")
    st.download_button(
        label="⬇️ Download Excel Report",
        data=excel_bytes,
        file_name=download_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )
    st.caption(
        "Excel mein PDF first-page GST Summary, Item Details aur Checks sheets hongi. Missing values blank rahengi. "
        "Save As popup ke liye Chrome mein Settings → Downloads → Ask where to save each file before downloading ON karein."
    )

    with st.expander("First Page Raw Text (diagnostic)"):
        st.text_area("PDF Page 1", st.session_state.get("boe_first_page_raw", ""), height=280)
else:
    st.markdown(
        """
        **Workflow**
        1. PDF upload karein.
        2. `Extract Data` par click karein.
        3. HSN-wise GST summary aur item-wise details check karein.
        4. Excel report download karein.
        """
    )
