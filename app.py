import io
import re
from collections import defaultdict
from datetime import datetime

import fitz
import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


st.set_page_config(page_title="Biogene India - BOE HSN Duty Summary", page_icon="📄", layout="wide")
st.title("📄 Bill of Entry PDF → HSN-wise Duty Summary")
st.caption("Part II se HSN mapping + Part III ke har item ke exact '5. IGST' section se GST Rate aur IGST Amount.")

NUM_RE = re.compile(r"^[0-9][0-9,]*(?:\.[0-9]+)?$")
HSN_RE = re.compile(r"^\d{8}$")


def number(token):
    try:
        return float(str(token).replace(",", ""))
    except Exception:
        return None


def words_on_line(words, x_min, x_max, y, tolerance=2.0, predicate=None):
    found = [
        w for w in words
        if x_min <= w[0] <= x_max
        and abs(w[1] - y) <= tolerance
        and (predicate(w[4]) if predicate else True)
    ]
    return found


def unique_numeric(words, x_min, x_max, y, tolerance=2.2):
    found = words_on_line(words, x_min, x_max, y, tolerance, lambda t: bool(NUM_RE.fullmatch(str(t))))
    return number(found[0][4]) if len(found) == 1 else None


def extract_header_and_page1(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        p = doc[0]
        words = p.get_text("words")
        be_no, be_date = "", ""
        raw = p.get_text("text", sort=True) or ""
        m = re.search(r"\b(\d{5,})\s+(\d{1,2}/\d{1,2}/\d{4})\b", raw)
        if m:
            be_no, be_date = m.group(1), m.group(2)
        # Page-1 duty-summary IGST value is positioned directly below the 7. IGST column.
        igst_candidates = [
            w for w in words
            if 395 <= w[0] <= 445 and 252 <= w[1] <= 259 and NUM_RE.fullmatch(str(w[4]))
        ]
        page1_igst = number(igst_candidates[0][4]) if len(igst_candidates) == 1 else None
        if be_date:
            try:
                be_date = datetime.strptime(be_date, "%d/%m/%Y").strftime("%d-%m-%Y")
            except ValueError:
                pass
        return {"BOE No.": be_no, "Date": be_date}, page1_igst, raw
    finally:
        doc.close()


def extract_part2_hsn_map(doc, expected_items):
    """Read item serial and 8-digit CTH/HSN from Part II pages 2-6."""
    mapping = {}
    issues = []
    for page_index in range(1, min(6, len(doc))):
        words = doc[page_index].get_text("words")
        item_words = [
            w for w in words
            if 65 <= w[0] <= 95 and str(w[4]).isdigit()
            and 1 <= int(w[4]) <= expected_items
            and 175 <= w[1] <= 715
        ]
        for item_word in item_words:
            item_no = int(item_word[4])
            same_line_hsn = [
                w for w in words
                if 100 <= w[0] <= 145
                and abs(w[1] - item_word[1]) <= 2.2
                and HSN_RE.fullmatch(str(w[4]))
            ]
            if len(same_line_hsn) == 1:
                hsn = str(same_line_hsn[0][4])
                if item_no in mapping and mapping[item_no] != hsn:
                    issues.append(f"Part II: item {item_no} ke liye multiple HSN mile.")
                else:
                    mapping[item_no] = hsn
            else:
                issues.append(f"Part II PDF page {page_index + 1}: item {item_no} ka HSN missing/ambiguous.")
    for item_no in range(1, expected_items + 1):
        if item_no not in mapping:
            issues.append(f"Part II: item {item_no} ka HSN map nahi hua.")
    return mapping, issues


def extract_part3_igst(doc, hsn_map, expected_items):
    """Read only the numeric Rate and Amount belonging to the explicit 5.IGST block."""
    rows = []
    issues = []
    for page_index in range(6, min(86, len(doc))):
        words = doc[page_index].get_text("words")
        item_words = [
            w for w in words
            if 112 <= w[0] <= 135 and str(w[4]).isdigit()
            and 1 <= int(w[4]) <= expected_items
            and (120 <= w[1] <= 145 or 335 <= w[1] <= 360)
        ]
        for iw in item_words:
            item_no = int(iw[4])
            y = iw[1]
            hsn_words = [
                w for w in words
                if 145 <= w[0] <= 190 and abs(w[1] - y) <= 2.2
                and HSN_RE.fullmatch(str(w[4]))
            ]
            # The Part III page itself carries CTH, so cross-check it against Part II.
            hsn_part3 = str(hsn_words[0][4]) if len(hsn_words) == 1 else None
            hsn_part2 = hsn_map.get(item_no)
            if hsn_part3 and hsn_part2 and hsn_part3 != hsn_part2:
                issues.append(f"Item {item_no}: Part II HSN {hsn_part2} aur Part III HSN {hsn_part3} match nahi.")
            hsn = hsn_part2 or hsn_part3

            headings = [
                w for w in words
                if 300 <= w[0] <= 360 and y + 40.0 <= w[1] <= y + 65.0
                and re.fullmatch(r"5\.IGST", str(w[4]), re.I)
            ]
            rate = amount = None
            if len(headings) == 1:
                hy = headings[0][1]
                rate = unique_numeric(words, 300, 345, hy + 27.0)
                amount = unique_numeric(words, 300, 345, hy + 36.0)
            missing = []
            if not hsn:
                missing.append("HSN")
            if rate is None:
                missing.append("5. IGST Rate")
            if amount is None:
                missing.append("5. IGST Amount")
            if missing:
                issues.append(f"Part III PDF page {page_index + 1}, item {item_no}: missing/ambiguous " + ", ".join(missing))
            rows.append({
                "Item No.": item_no,
                "HSN": hsn,
                "GST Rate (%)": rate,
                "IGST Amount": amount,
                "PDF Page": page_index + 1,
            })

    detail = pd.DataFrame(rows)
    if not detail.empty:
        # Duplicate appearances are treated as an ambiguity, not silently summed twice.
        dupes = detail[detail.duplicated("Item No.", keep=False)]["Item No."].astype(int).unique().tolist()
        for item_no in dupes:
            issues.append(f"Part III: item {item_no} multiple baar mila; manual verification zaroori.")
        detail = detail.drop_duplicates("Item No.", keep="first").sort_values("Item No.").reset_index(drop=True)
    found = set(detail["Item No."].astype(int)) if not detail.empty else set()
    for item_no in sorted(set(range(1, expected_items + 1)) - found):
        issues.append(f"Part III: item {item_no} ka duty block nahi mila.")
    return detail, issues


def build_hsn_summary(detail):
    rows = []
    if detail.empty:
        return pd.DataFrame(columns=["HSN Code", "GST Rate (%)", "Item Count", "HSN-wise IGST Amount"])
    valid = detail[detail["HSN"].notna() & detail["IGST Amount"].notna()].copy()
    for hsn, group in valid.groupby("HSN", sort=True):
        rates = sorted({float(x) for x in group["GST Rate (%)"].dropna().tolist()})
        rate_label = ", ".join(f"{r:g}%" for r in rates) if rates else ""
        rows.append({
            "HSN Code": str(hsn),
            "GST Rate (%)": rate_label,
            "Item Count": int(group["Item No."].nunique()),
            "HSN-wise IGST Amount": round(float(group["IGST Amount"].sum()), 2),
        })
    return pd.DataFrame(rows)


def style_sheet(ws):
    ws.sheet_view.showGridLines = False
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="17365D")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    thin = Side(style="thin", color="D9E2F3")
    for row in ws.iter_rows():
        for c in row:
            c.border = Border(bottom=thin)
            c.alignment = Alignment(vertical="center", wrap_text=True)


def make_workbook(header, summary, page1_total, detail, issues, expected_items):
    wb = Workbook()
    ws = wb.active
    ws.title = "HSN Duty Summary"
    ws.append(["HSN Code", "GST Rate (%)", "Item Count", "HSN-wise IGST Amount"])
    for row in summary.itertuples(index=False, name=None):
        ws.append(list(row))
    total_row = ws.max_row + 1
    ws.cell(total_row, 1, "TOTAL HSN-WISE IGST")
    ws.cell(total_row, 4, f"=SUM(D2:D{total_row-1})" if total_row > 2 else 0)
    style_sheet(ws)
    for r in range(2, total_row + 1):
        ws.cell(r, 4).number_format = '₹#,##,##0.00'
    for col, width in {"A": 18, "B": 20, "C": 14, "D": 26}.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:D{total_row-1}" if total_row > 2 else "A1:D1"

    rec = wb.create_sheet("Reconciliation")
    rec.append(["Particulars", "Amount / Count"])
    item_sum = round(float(summary["HSN-wise IGST Amount"].sum()), 2) if not summary.empty else 0.0
    diff = round(float(page1_total) - item_sum, 2) if page1_total is not None else None
    rec.append(["BOE Number", header.get("BOE No.", "")])
    rec.append(["BOE Date", header.get("Date", "")])
    rec.append(["Expected item count (PDF header)", expected_items])
    rec.append(["Unique HSN codes", int(summary["HSN Code"].nunique()) if not summary.empty else 0])
    rec.append(["Page 1 printed IGST total", page1_total])
    rec.append(["Sum of HSN-wise IGST from Part III", item_sum])
    rec.append(["Difference (Page 1 minus Part III sum)", diff])
    rec.append(["Items with IGST values extracted", int(detail["IGST Amount"].notna().sum()) if not detail.empty else 0])
    rec.append(["Items with missing/ambiguous IGST amount", int(detail["IGST Amount"].isna().sum()) if not detail.empty else expected_items])
    style_sheet(rec)
    rec.column_dimensions["A"].width = 54
    rec.column_dimensions["B"].width = 26
    for r in range(6, 9):
        rec.cell(r, 2).number_format = '₹#,##,##0.00'

    src = wb.create_sheet("Source & Notes")
    src.append(["Source / Rule", "Details"])
    src.append(["Page 1", "Overall IGST duty total is read directly from the page-1 Duty Summary."])
    src.append(["Part II (pages 2-6)", "Item number is mapped to the 8-digit CTH/HSN printed in the item table."])
    src.append(["Part III", "GST rate and IGST amount are read only from the same item's explicit '5. IGST' duty section."])
    src.append(["No assumptions", "No GST rate is inferred from HSN or general tax rules. Missing/ambiguous values remain blank and are listed in Extraction Issues."])
    src.append(["Output format", "Main HSN Duty Summary sheet contains one row per unique HSN. No item-wise detail table is included in the main report."])
    style_sheet(src)
    src.column_dimensions["A"].width = 26
    src.column_dimensions["B"].width = 105

    err = wb.create_sheet("Extraction Issues")
    err.append(["Issue"])
    for issue in issues:
        err.append([issue])
    if not issues:
        err.append(["No extraction issues detected."])
    style_sheet(err)
    err.column_dimensions["A"].width = 110

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def run_extraction(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page1_text = doc[0].get_text("text", sort=True) or ""
        header, page1_igst, page1_raw = extract_header_and_page1(pdf_bytes)
        # ICEGATE page-1 header prints the item count directly below the ITEM label.
        words = doc[0].get_text("words")
        item_count_words = [w for w in words if 430 <= w[0] <= 460 and 80 <= w[1] <= 90 and str(w[4]).isdigit()]
        expected_items = int(item_count_words[0][4]) if len(item_count_words) == 1 else 0
        if expected_items <= 0:
            raise ValueError("Page 1 se total item count detect nahi hua.")
        hsn_map, issues1 = extract_part2_hsn_map(doc, expected_items)
        detail, issues2 = extract_part3_igst(doc, hsn_map, expected_items)
        summary = build_hsn_summary(detail)
        issues = issues1 + issues2
        return header, page1_igst, page1_raw, expected_items, hsn_map, detail, summary, issues
    finally:
        doc.close()


uploaded = st.file_uploader("Bill of Entry PDF upload karein", type=["pdf"])
if uploaded:
    st.success(f"Uploaded: {uploaded.name}")
    if st.button("🔍 Extract HSN-wise Duty Summary", type="primary", use_container_width=True):
        try:
            with st.spinner("PDF ke Part II aur Part III ko item-wise cross-check kiya ja raha hai..."):
                result = run_extraction(uploaded.getvalue())
            header, page1_total, page1_raw, expected, hsn_map, detail, summary, issues = result
            st.session_state["boe_result"] = result
            st.session_state["boe_filename"] = uploaded.name
        except Exception as exc:
            st.error(f"PDF process karte waqt error aaya: {exc}")

if "boe_result" in st.session_state:
    header, page1_total, page1_raw, expected, hsn_map, detail, summary, issues = st.session_state["boe_result"]
    st.subheader("BOE Details")
    c1, c2, c3 = st.columns(3)
    c1.metric("BOE Number", header.get("BOE No.") or "Not found")
    c2.metric("BOE Date", header.get("Date") or "Not found")
    c3.metric("Unique HSN Codes", int(summary["HSN Code"].nunique()) if not summary.empty else 0)

    st.subheader("HSN-wise Duty Summary")
    st.dataframe(summary, use_container_width=True, hide_index=True)
    hsn_sum = round(float(summary["HSN-wise IGST Amount"].sum()), 2) if not summary.empty else 0.0
    d1, d2, d3 = st.columns(3)
    d1.metric("Page 1 IGST Total", f"₹{page1_total:,.2f}" if page1_total is not None else "Not detected")
    d2.metric("HSN-wise IGST Total", f"₹{hsn_sum:,.2f}")
    d3.metric("Difference", f"₹{page1_total-hsn_sum:,.2f}" if page1_total is not None else "Not available")

    st.caption(f"Part II HSN mapped: {len(hsn_map)}/{expected} | Part III items found: {detail['Item No.'].nunique()}/{expected} | IGST amounts found: {detail['IGST Amount'].notna().sum()}/{expected}")
    if issues:
        with st.expander(f"⚠️ Extraction issues / manual verification ({len(issues)})"):
            for issue in issues:
                st.write("• " + issue)
    else:
        st.success("Sabhi item HSN aur 5. IGST fields successfully extract hue; koi ambiguity detect nahi hui.")

    excel = make_workbook(header, summary, page1_total, detail, issues, expected)
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", st.session_state.get("boe_filename", "BOE").rsplit(".", 1)[0])
    st.download_button("⬇️ Download HSN-wise Excel Report", excel, f"{base}_HSN_Duty_Summary.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)
else:
    st.info("PDF upload karein aur Extract HSN-wise Duty Summary par click karein. Excel mein main sheet sirf unique HSN-wise hogi.")
