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
    ws.cell(total_row, 5, float(summary["Taxable_Amount"].sum()))
    ws.cell(total_row, 6, float(summary["Tax_Amount"].sum()))
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
        "Taxable Amount = Assessable Value + BCD + SWS. Tax Amount = IGST amount read from Part III of the BOE. "
        "Please verify the totals against the original Bill of Entry."
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
        ("Total taxable amount", float(summary["Taxable_Amount"].sum())),
        ("Total IGST amount", float(summary["Tax_Amount"].sum())),
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
