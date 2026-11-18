import streamlit as st
import fitz  # PyMuPDF
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
st.subheader("Bill of Entry – Accurate PDF Extraction")
st.caption("Coordinate-based extraction of HSN, IGST rate, taxable value and IGST amount")


# =========================================================
# COMMON PDF HELPERS
# =========================================================

def get_words(page):
    """Return PDF words with their real X/Y coordinates."""
    result = []

    for w in page.get_text("words"):
        x0, y0, x1, y1, text = w[:5]
        result.append({
            "x0": float(x0),
            "y0": float(y0),
            "x1": float(x1),
            "y1": float(y1),
            "cx": (float(x0) + float(x1)) / 2,
            "cy": (float(y0) + float(y1)) / 2,
            "text": str(text).strip()
        })

    return result


def is_numeric_text(value):
    return bool(re.fullmatch(r"-?\d[\d,]*(?:\.\d+)?", str(value).strip()))


def number_near(words, target_x, target_y, x_tolerance=38, y_tolerance=3.2):
    """
    Pick the numeric PDF word closest to the expected table cell.
    It does not pick a value from a different row or distant column.
    """
    candidates = []

    for w in words:
        if not is_numeric_text(w["text"]):
            continue

        dx = abs(w["cx"] - target_x)
        dy = abs(w["cy"] - target_y)

        if dx <= x_tolerance and dy <= y_tolerance:
            candidates.append((dx + (dy * 4), w))

    if not candidates:
        return None

    selected = min(candidates, key=lambda item: item[0])[1]
    return float(selected["text"].replace(",", ""))


def find_word(words, pattern, min_y=0, max_y=1000, min_x=0, max_x=612):
    matches = [
        w for w in words
        if min_y <= w["cy"] <= max_y
        and min_x <= w["cx"] <= max_x
        and re.search(pattern, w["text"], re.I)
    ]
    return sorted(matches, key=lambda w: (w["cy"], w["cx"]))


def group_words_by_y(words, tolerance=3.0):
    """Group words into visual text lines using their vertical coordinates."""
    ordered = sorted(words, key=lambda w: (w["cy"], w["x0"]))
    lines = []

    for word in ordered:
        placed = False

        for line in lines:
            if abs(word["cy"] - line["y"]) <= tolerance:
                line["words"].append(word)
                line["y"] = sum(x["cy"] for x in line["words"]) / len(line["words"])
                placed = True
                break

        if not placed:
            lines.append({"y": word["cy"], "words": [word]})

    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])

    return sorted(lines, key=lambda line: line["y"])


def line_text(line):
    return " ".join(w["text"] for w in line["words"])


def parse_date(value):
    value = str(value).strip().replace("/", "-")
    parts = value.split("-")

    if len(parts) == 3:
        a, b, c = parts
        if len(a) == 4:
            return f"{c.zfill(2)}-{b.zfill(2)}-{a}"
        return f"{a.zfill(2)}-{b.zfill(2)}-{c}"

    return value


# =========================================================
# BOE HEADER
# =========================================================

def extract_boe_header(pdf):
    header = {
        "BOE No.": "",
        "Date": "",
        "EXPORTER": "",
        "ADDRESS": ""
    }

    # BOE number and date are in the top-right header of page 1.
    first_words = get_words(pdf[0])

    be_no_label = find_word(first_words, r"^BE$", min_y=20, max_y=55, min_x=450)
    be_date_label = find_word(first_words, r"^Date$", min_y=20, max_y=55, min_x=420)

    if be_no_label:
        y = be_no_label[0]["cy"] + 9
        value = number_near(first_words, 390, y, x_tolerance=35, y_tolerance=3)
        if value is not None:
            header["BOE No."] = str(int(value))

    if be_date_label:
        y = be_date_label[0]["cy"] + 9
        date_words = [
            w for w in first_words
            if abs(w["cy"] - y) <= 3
            and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", w["text"])
        ]
        if date_words:
            header["Date"] = parse_date(date_words[0]["text"])

    # Supplier/exporter details appear in PART-II invoice details.
    # Search early pages for the "3.SUPPLIER NAME & ADDRESS" block.
    for page in pdf[:6]:
        words = get_words(page)
        supplier_labels = find_word(words, r"SUPPLIER", min_y=100, max_y=500)

        if not supplier_labels:
            continue

        label_y = supplier_labels[0]["cy"]

        # Supplier name and address are directly below this heading.
        supplier_words = [
            w for w in words
            if 55 <= w["x0"] <= 185
            and label_y + 5 <= w["cy"] <= label_y + 42
        ]

        lines = group_words_by_y(supplier_words, tolerance=3.2)
        text_lines = [line_text(line) for line in lines]
        text_lines = [x.strip() for x in text_lines if x.strip()]

        if text_lines:
            header["EXPORTER"] = text_lines[0]

        if len(text_lines) > 1:
            address_parts = text_lines[1:]

            # The country is printed below the supplier address in this BOE.
            country_words = [
                w for w in words
                if w["cy"] > label_y + 42
                and w["cy"] < label_y + 58
                and w["text"].upper() in ("UNITED", "STATES")
            ]
            if country_words:
                address_parts.append("US")

            header["ADDRESS"] = " ".join(address_parts)

        if header["EXPORTER"]:
            break

    return header


# =========================================================
# ITEM EXTRACTION – PART III
# =========================================================

def extract_items_from_page(page, page_number):
    words = get_words(page)

    # In PART-III, CTH is consistently in the horizontal band x=135..190.
    # This avoids mistaking product codes, amounts or other 8-digit values for HSN.
    hsn_words = [
        w for w in words
        if 135 <= w["cx"] <= 190
        and 110 <= w["cy"] <= 735
        and re.fullmatch(r"\d{6,8}", w["text"])
    ]

    hsn_words.sort(key=lambda w: w["cy"])

    extracted = []

    for item_index, hsn_word in enumerate(hsn_words):
        item_y = hsn_word["cy"]
        next_item_y = (
            hsn_words[item_index + 1]["cy"]
            if item_index + 1 < len(hsn_words)
            else 740
        )

        # Invoice Sr. No. and Item Sr. No. are on the same visual row as CTH.
        invoice_no = number_near(
            words, target_x=81, target_y=item_y,
            x_tolerance=28, y_tolerance=3
        )
        si = number_near(
            words, target_x=121, target_y=item_y,
            x_tolerance=28, y_tolerance=3
        )

        # 29. ASSESS VALUE label; the amount is on the next visual row.
        assess_labels = [
            w for w in words
            if item_y < w["cy"] < next_item_y
            and re.search(r"29\.ASSESS", w["text"], re.I)
        ]

        taxable_amount = None

        if assess_labels:
            assess_y = min(w["cy"] for w in assess_labels)
            taxable_amount = number_near(
                words, target_x=430, target_y=assess_y + 9,
                x_tolerance=42, y_tolerance=3.2
            )

        # Locate 5.IGST heading for this item.
        igst_labels = [
            w for w in words
            if item_y < w["cy"] < next_item_y
            and re.search(r"5\.IGST", w["text"], re.I)
        ]

        igst_rate = None
        igst_amount = None

        if igst_labels:
            igst_heading = min(igst_labels, key=lambda w: w["cy"])
            igst_x = igst_heading["cx"]
            igst_y = igst_heading["cy"]

            # The Rate and Amount labels belong to B. ITEM DUTY.
            # Only look below the IGST heading and before the next section.
            rate_labels = [
                w for w in words
                if w["text"].lower() == "rate"
                and w["x0"] < 105
                and igst_y < w["cy"] < min(next_item_y, igst_y + 90)
            ]

            amount_labels = [
                w for w in words
                if w["text"].lower() == "amount"
                and w["x0"] < 105
                and igst_y < w["cy"] < min(next_item_y, igst_y + 90)
            ]

            if rate_labels:
                rate_y = min(w["cy"] for w in rate_labels)
                igst_rate = number_near(
                    words, target_x=igst_x, target_y=rate_y,
                    x_tolerance=38, y_tolerance=3.2
                )

            if amount_labels:
                amount_y = min(w["cy"] for w in amount_labels)
                igst_amount = number_near(
                    words, target_x=igst_x, target_y=amount_y,
                    x_tolerance=38, y_tolerance=3.2
                )

        extracted.append({
            "SI": int(si) if si is not None else None,
            "HSN": hsn_word["text"],
            "GST @ %": igst_rate,
            "TAXABLE AMOUNT": taxable_amount,
            "TAX AMOUNT": igst_amount,
            "Invoice Sr. No.": int(invoice_no) if invoice_no is not None else None,
            "PDF Page": page_number
        })

    return extracted


def extract_all_items(pdf):
    all_items = []

    for page_number, page in enumerate(pdf, start=1):
        page_text = page.get_text("text")

        if re.search(r"PART\s*-\s*III\s*-\s*DUTIES", page_text, re.I):
            all_items.extend(
                extract_items_from_page(page, page_number)
            )

    all_items.sort(
        key=lambda item: (
            item["Invoice Sr. No."] if item["Invoice Sr. No."] is not None else 999999,
            item["SI"] if item["SI"] is not None else 999999
        )
    )

    return all_items


# =========================================================
# PART-I DUTY SUMMARY TOTALS
# =========================================================

def extract_summary_totals(pdf):
    result = {
        "taxable_total": None,
        "igst_total": None
    }

    page = pdf[0]
    words = get_words(page)

    igst_heading = find_word(
        words, r"7\.IGST", min_y=220, max_y=290
    )
    assess_heading = find_word(
        words, r"18\.TOT\.ASS", min_y=220, max_y=290
    )

    if igst_heading:
        result["igst_total"] = number_near(
            words,
            target_x=igst_heading[0]["cx"],
            target_y=igst_heading[0]["cy"] + 9,
            x_tolerance=42,
            y_tolerance=3
        )

    if assess_heading:
        result["taxable_total"] = number_near(
            words,
            target_x=assess_heading[0]["cx"],
            target_y=assess_heading[0]["cy"] + 9,
            x_tolerance=48,
            y_tolerance=3
        )

    return result


# =========================================================
# EXCEL OUTPUT
# =========================================================

def create_excel(header, items, totals):
    wb = Workbook()
    ws = wb.active
    ws.title = "BOE DATA"

    blue = "5B9BD5"
    light_blue = "DDEBF7"
    white = "FFFFFF"
    thin_side = Side(style="thin", color="808080")

    # Header exactly in the requested layout.
    ws.append(["BOE No.", ":", header["BOE No."]])
    ws.append(["Date", ":", header["Date"]])
    ws.append(["EXPORTER", ":", header["EXPORTER"]])
    ws.append(["ADDRESS", ":", header["ADDRESS"]])
    ws.append([])

    ws.append([
        "DETAILS",
        "SI",
        "HSN",
        "GST @ %",
        "TAXABLE AMOUNT",
        "TAX AMOUNT"
    ])

    for item in items:
        ws.append([
            "",
            item["SI"] if item["SI"] is not None else "",
            item["HSN"],
            item["GST @ %"] if item["GST @ %"] is not None else "",
            item["TAXABLE AMOUNT"] if item["TAXABLE AMOUNT"] is not None else "",
            item["TAX AMOUNT"] if item["TAX AMOUNT"] is not None else ""
        ])

    # Total row is taken from PART-I summary, not calculated from item values.
    ws.append([
        "",
        "",
        "",
        "",
        totals["taxable_total"] if totals["taxable_total"] is not None else "",
        totals["igst_total"] if totals["igst_total"] is not None else ""
    ])

    # Formatting
    for cell in ws[6]:
        cell.fill = PatternFill("solid", fgColor=blue)
        cell.font = Font(bold=True, color=white)
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_no in range(1, 5):
        ws.cell(row_no, 1).font = Font(bold=True)
        ws.cell(row_no, 3).font = Font(bold=True)

    for row in ws.iter_rows():
        for cell in row:
            cell.border = Border(
                left=thin_side,
                right=thin_side,
                top=thin_side,
                bottom=thin_side
            )
            if cell.column in (5, 6) and cell.row >= 7:
                cell.number_format = '#,##0.00'

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

    # Audit sheet identifies incomplete values and source page.
    audit = wb.create_sheet("EXTRACTION CHECK")
    audit.append([
        "SI", "HSN", "GST RATE", "TAXABLE AMOUNT",
        "IGST AMOUNT", "INVOICE SR NO.", "PDF PAGE", "STATUS"
    ])

    for item in items:
        missing = []

        for key in ("SI", "HSN", "GST @ %", "TAXABLE AMOUNT", "TAX AMOUNT"):
            if item.get(key) is None or item.get(key) == "":
                missing.append(key)

        audit.append([
            item["SI"] if item["SI"] is not None else "",
            item["HSN"],
            item["GST @ %"] if item["GST @ %"] is not None else "",
            item["TAXABLE AMOUNT"] if item["TAXABLE AMOUNT"] is not None else "",
            item["TAX AMOUNT"] if item["TAX AMOUNT"] is not None else "",
            item["Invoice Sr. No."] if item["Invoice Sr. No."] is not None else "",
            item["PDF Page"],
            "CHECK: " + ", ".join(missing) if missing else "OK"
        ])

    for cell in audit[1]:
        cell.fill = PatternFill("solid", fgColor=blue)
        cell.font = Font(bold=True, color=white)

    for col in range(1, audit.max_column + 1):
        audit.column_dimensions[get_column_letter(col)].width = 22

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


# =========================================================
# STREAMLIT APP
# =========================================================

uploaded_file = st.file_uploader(
    "Upload Bill of Entry PDF",
    type=["pdf"]
)

if uploaded_file:
    if st.button("EXTRACT DATA", type="primary", use_container_width=True):
        try:
            with st.spinner("Reading PDF coordinates and extracting all items..."):
                pdf_bytes = uploaded_file.getvalue()
                pdf = fitz.open(stream=pdf_bytes, filetype="pdf")

                # Check whether this PDF has a selectable text layer.
                text_sample = "\n".join(
                    page.get_text("text") for page in pdf[:min(10, len(pdf))]
                )

                if len(text_sample.strip()) < 100:
                    st.error(
                        "This PDF appears to be image-only. "
                        "OCR is required; no values have been guessed."
                    )
                    st.stop()

                header = extract_boe_header(pdf)
                items = extract_all_items(pdf)
                totals = extract_summary_totals(pdf)

                if not items:
                    st.error(
                        "PART-III item records were not detected. "
                        "Please upload the original searchable BOE PDF."
                    )
                    st.stop()

                st.success(f"Extraction completed: {len(items)} item(s) detected.")

                st.markdown("### BOE DETAILS")
                st.table(pd.DataFrame([
                    ["BOE No.", header["BOE No."]],
                    ["Date", header["Date"]],
                    ["EXPORTER", header["EXPORTER"]],
                    ["ADDRESS", header["ADDRESS"]]
                ], columns=["FIELD", "VALUE"]))

                st.markdown("### EXTRACTED ITEM DETAILS")

                preview = pd.DataFrame([
                    {
                        "DETAILS": "",
                        "SI": item["SI"],
                        "HSN": item["HSN"],
                        "GST @ %": item["GST @ %"],
                        "TAXABLE AMOUNT": item["TAXABLE AMOUNT"],
                        "TAX AMOUNT": item["TAX AMOUNT"]
                    }
                    for item in items
                ])

                st.dataframe(
                    preview,
                    use_container_width=True,
                    hide_index=True
                )

                st.markdown("### TOTALS FROM PART-I")
                c1, c2 = st.columns(2)
                c1.metric(
                    "TOTAL TAXABLE AMOUNT",
                    f'{totals["taxable_total"]:,.2f}'
                    if totals["taxable_total"] is not None else "NOT FOUND"
                )
                c2.metric(
                    "TOTAL IGST AMOUNT",
                    f'{totals["igst_total"]:,.2f}'
                    if totals["igst_total"] is not None else "NOT FOUND"
                )

                missing_items = [
                    item for item in items
                    if any(
                        item.get(key) is None
                        for key in ("SI", "GST @ %", "TAXABLE AMOUNT", "TAX AMOUNT")
                    )
                ]

                if missing_items:
                    st.warning(
                        f"{len(missing_items)} item(s) need verification. "
                        "See the EXTRACTION CHECK sheet in Excel."
                    )
                else:
                    st.success("All item fields were detected. Please verify totals before filing.")

                excel_data = create_excel(header, items, totals)

                st.download_button(
                    "DOWNLOAD EXCEL",
                    data=excel_data,
                    file_name="BOE_Extracted_Data.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

                pdf.close()

        except Exception as error:
            st.error(f"Extraction failed: {error}")
            st.exception(error)
