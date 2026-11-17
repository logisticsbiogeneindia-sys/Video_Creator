
import re
import io
import pandas as pd
import streamlit as st
import pdfplumber

st.set_page_config(
    page_title="Bill of Entry Data Extractor",
    page_icon="📄",
    layout="wide"
)

st.title("Bill of Entry – GST Data Extractor")
st.caption("BOE Details | HSN-wise GST | Automatic Reconciliation")

# -----------------------------
# NUMBER CLEANING
# -----------------------------

def clean_amount(value):
    if not value:
        return 0.0

    value = str(value)
    value = value.replace(",", "").replace("₹", "")
    value = value.replace(" ", "").strip()

    match = re.search(r"-?\d+(?:\.\d+)?", value)

    return float(match.group()) if match else 0.0


def indian_format(value):
    return f"{value:,.2f}"


# -----------------------------
# PDF TEXT EXTRACTION
# -----------------------------

def extract_pdf_text(pdf_file):

    pages = []

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text(
                x_tolerance=2,
                y_tolerance=3
            )

            if text:
                pages.append(text)

    return "\n".join(pages)


# -----------------------------
# BOE HEADER EXTRACTION
# -----------------------------

def extract_header(text):

    data = {
        "BOE No": "",
        "Date": "",
        "Exporter": "",
        "Address": ""
    }

    patterns = {
        "BOE No": [
            r"(?:BE\s*No|BOE\s*No|Bill\s+of\s+Entry\s+No)"
            r"\s*[:\-]?\s*(\d{6,})"
        ],
        "Date": [
            r"(?:BE\s*Date|BOE\s*Date|Date)"
            r"\s*[:\-]?\s*(\d{2}[-/]\d{2}[-/]\d{4})"
        ],
        "Exporter": [
            r"EXPORTER\s*[:\-]?\s*(.+)"
        ],
        "Address": [
            r"ADDRESS\s*[:\-]?\s*(.+)"
        ]
    }

    for field, regex_list in patterns.items():
        for pattern in regex_list:
            match = re.search(
                pattern, text,
                re.IGNORECASE
            )

            if match:
                data[field] = match.group(1).strip()
                break

    return data


# -----------------------------
# GST SUMMARY EXTRACTION
# -----------------------------

def extract_gst_summary(text):

    records = []

    lines = text.splitlines()

    # HSN + GST rate + taxable amount + tax amount
    amount_pattern = re.compile(
        r"(?P<hsn>\d{8})\s+"
        r"(?P<rate>\d{1,2}(?:\.\d+)?)\s+"
        r"(?P<taxable>[\d,]+\.\d{2})"
        r"(?:\s+(?P<tax>[\d,]+\.\d{2}))?",
        re.IGNORECASE
    )

    for line in lines:

        line = line.replace("\xa0", " ")
        line = re.sub(r"\s+", " ", line).strip()

        match = amount_pattern.search(line)

        if not match:
            continue

        hsn = match.group("hsn")
        rate = float(match.group("rate"))

        taxable = clean_amount(
            match.group("taxable")
        )

        tax_text = match.group("tax")

        if tax_text:
            tax = clean_amount(tax_text)
            tax_source = "PDF"
        else:
            tax = round(
                taxable * rate / 100,
                2
            )
            tax_source = "Calculated"

        records.append({
            "HSN Code": hsn,
            "GST Rate (%)": rate,
            "Taxable Amount": taxable,
            "Tax Amount": tax,
            "Tax Source": tax_source
        })

    # Remove duplicate rows
    unique = {}

    for row in records:
        key = (
            row["HSN Code"],
            row["GST Rate (%)"],
            row["Taxable Amount"]
        )
        unique[key] = row

    return list(unique.values())


# -----------------------------
# TOTAL VALIDATION
# -----------------------------

def validate_data(records):

    taxable_total = round(
        sum(r["Taxable Amount"] for r in records),
        2
    )

    tax_total = round(
        sum(r["Tax Amount"] for r in records),
        2
    )

    expected_tax = round(
        sum(
            r["Taxable Amount"] *
            r["GST Rate (%)"] / 100
            for r in records
        ),
        2
    )

    checks = [
        {
            "Check": "Taxable Amount Total",
            "Calculated": taxable_total
        },
        {
            "Check": "Tax Amount Total",
            "Calculated": tax_total
        },
        {
            "Check": "Expected GST",
            "Calculated": expected_tax
        },
        {
            "Check": "GST Calculation Difference",
            "Calculated": round(
                tax_total - expected_tax, 2
            )
        }
    ]

    return checks


# -----------------------------
# EXCEL GENERATION
# -----------------------------

def create_excel(header, records, checks):

    output = io.BytesIO()

    df_header = pd.DataFrame(
        list(header.items()),
        columns=["Field", "Value"]
    )

    df_gst = pd.DataFrame(records)

    if not df_gst.empty:

        total_row = pd.DataFrame([{
            "HSN Code": "TOTAL",
            "GST Rate (%)": None,
            "Taxable Amount": df_gst[
                "Taxable Amount"
            ].sum(),
            "Tax Amount": df_gst[
                "Tax Amount"
            ].sum(),
            "Tax Source": ""
        }])

        df_gst = pd.concat(
            [df_gst, total_row],
            ignore_index=True
        )

    df_checks = pd.DataFrame(checks)

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        df_header.to_excel(
            writer,
            sheet_name="BOE Details",
            index=False
        )

        df_gst.to_excel(
            writer,
            sheet_name="GST Summary",
            index=False
        )

        df_checks.to_excel(
            writer,
            sheet_name="Checks",
            index=False
        )

        for ws in writer.book.worksheets:

            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions

            for column in ws.columns:

                max_length = max(
                    len(str(cell.value or ""))
                    for cell in column
                )

                ws.column_dimensions[
                    column[0].column_letter
                ].width = min(max_length + 4, 45)

    output.seek(0)

    return output


# -----------------------------
# STREAMLIT UI
# -----------------------------

uploaded_files = st.file_uploader(
    "Upload Bill of Entry PDF",
    type=["pdf"],
    accept_multiple_files=True
)

if uploaded_files:

    for pdf_file in uploaded_files:

        st.divider()

        st.subheader(pdf_file.name)

        try:

            text = extract_pdf_text(pdf_file)

            header = extract_header(text)

            records = extract_gst_summary(text)

            if not records:
                st.error(
                    "GST Summary extract nahi hui. "
                    "PDF table layout manually verify karein."
                )
                continue

            checks = validate_data(records)

            st.markdown("### BOE Details")
            st.json(header)

            st.markdown("### HSN-wise GST Summary")

            df = pd.DataFrame(records)

            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True
            )

            taxable_total = sum(
                r["Taxable Amount"]
                for r in records
            )

            tax_total = sum(
                r["Tax Amount"]
                for r in records
            )

            col1, col2 = st.columns(2)

            col1.metric(
                "Total Taxable Amount",
                f"₹{indian_format(taxable_total)}"
            )

            col2.metric(
                "Total Tax Amount",
                f"₹{indian_format(tax_total)}"
            )

            excel = create_excel(
                header,
                records,
                checks
            )

            st.download_button(
                "Download Excel Report",
                data=excel,
                file_name=(
                    pdf_file.name.rsplit(".", 1)[0]
                    + "_BOE_Report.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                )
            )

        except Exception as e:

            st.error(f"Processing Error: {e}")
