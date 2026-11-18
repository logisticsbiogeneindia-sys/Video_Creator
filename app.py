# -*- coding: utf-8 -*-
"""
BOE GST Summary  -  Streamlit app
---------------------------------
Upload an Indian Customs Bill of Entry (ICEGATE BOE) PDF and get an HSN-wise
GST summary in the exact pasted-sheet format:

    BOE No.  :
    Date     :
    EXPORTER :
    ADDRESS  :

    SI | HSN | GST @ % | TAXABLE AMOUNT | TAX AMOUNT   (+ TOTAL row)

How to run:
    pip install streamlit pdfplumber openpyxl
    streamlit run boe_gst_summary_app.py

Everything is parsed from the PDF itself (no manual entry).
TAX AMOUNT = per-item IGST booked in the BOE (Part-III), HSN-wise sum, grand
total tied to the BOE's own Part-I IGST figure; TAXABLE AMOUNT = TAX / GST rate.
pandas is optional (only for the item-level preview table).
"""

import io
import re
from collections import OrderedDict

import pdfplumber

# ----------------------------------------------------------------------------- helpers

COUNTRY_MAP = {
    "UNITED STATES OF AMERICA": "US",
    "UNITED STATES": "US",
    "UNITED KINGDOM": "GB",
    "SOUTH KOREA": "KR",
    "HONG KONG": "HK",
    "GERMANY": "DE",
    "FRANCE": "FR",
    "JAPAN": "JP",
    "CHINA": "CN",
    "ITALY": "IT",
    "SPAIN": "ES",
    "CANADA": "CA",
    "AUSTRALIA": "AU",
    "INDIA": "IN",
}

NUM_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?$|^-?\d+(?:\.\d+)?$")
# kerning fragments: '28' + '2' + '.7' must re-join into '282.7',
# '9' + '.' + '6' into '9.6', and '101.' + '7' into '101.7'
FRAG_RE = re.compile(r"^\.\d+$|^\d+\.$|^\.$")

ITEM_RE = re.compile(
    r"(\d+)\s+(\d+)\s+(\d{6,10})\s+(?:NOEXCISE|EXCISE)?\s*"
    r"(.+?)\s+([YN])\s+([YN])\s+([YN])\s+([YN])\s+([YN])\s*$"
)
ITEM_RE_FALLBACK = re.compile(
    r"(\d+)\s+(\d+)\s+(\d{6,10})\s+(\S+?)\s+"
    r"(.+?)\s+([YN])\s+([YN])\s+([YN])\s+([YN])\s+([YN])\s*$"
)


def _is_num(tok):
    return bool(NUM_RE.match(tok) or FRAG_RE.match(tok))


def _f(tok):
    return float(tok.replace(",", ""))


def _cluster_lines(words, tol=2.5):
    """Group words into visual lines by their 'top' coordinate."""
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines, cur, base = [], [], None
    for w in words:
        if base is None or abs(w["top"] - base) <= tol:
            cur.append(w)
            if base is None:
                base = w["top"]
        else:
            lines.append(sorted(cur, key=lambda w: w["x0"]))
            cur, base = [w], w["top"]
    if cur:
        lines.append(sorted(cur, key=lambda w: w["x0"]))
    return lines


def _line_text(line):
    return " ".join(w["text"] for w in line)


def _joined(line):
    return "".join(w["text"] for w in line)


def _merge_num_words(words):
    """Merge kerning-split numbers ('2','8','2','.7' -> '282.7') and return
    [(value_str, x0, x1), ...] sorted by x0."""
    nums = [w for w in words if _is_num(w["text"])]
    nums.sort(key=lambda w: w["x0"])
    out = []
    for w in nums:
        t = w["text"]
        if t == ".":
            # a lone dot only makes sense glued to the value on its left
            if out and abs(out[-1][2] - w["x0"]) <= 1.0:
                v, x0, x1 = out[-1]
                out[-1] = (v + ".", x0, w["x1"])
            continue
        if out and abs(out[-1][2] - w["x0"]) <= 1.0:
            v, x0, x1 = out[-1]
            out[-1] = (v + t, x0, w["x1"])
        else:
            out.append((t, w["x0"], w["x1"]))
    return [x for x in out if x[0] not in (".", "")]


def _inr(v):
    """Indian-style grouping: 39,58,570.00"""
    neg = v < 0
    s = f"{abs(v):.2f}"
    whole, dec = s.split(".")
    if len(whole) > 3:
        last3, rest = whole[-3:], whole[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        whole = ",".join(parts + [last3])
    return ("-" if neg else "") + whole + "." + dec


# ----------------------------------------------------------------------------- parser

def parse_boe(pdf_bytes):
    """Parse an ICEGATE Bill of Entry PDF.

    Returns dict with boe_no, boe_date, exporter, address and per-item rows
    (cth, description, assessable value, IGST rate, IGST amount).
    """
    out = {
        "boe_no": None,
        "boe_date": None,
        "exporter": None,
        "address": None,
        "igst_total": None,
        "items": [],
        "warnings": [],
    }

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        # ---- 1. header (page 1): BE No + BE Date -----------------------------
        t0 = pdf.pages[0].extract_text(layout=True) or ""
        m = re.search(
            r"BE No\s+BE Date\s+BE Type\s*\n\s*\S+\s+(\d+)\s+(\d{1,2}/\d{1,2}/\d{4})",
            t0,
        )
        if m:
            out["boe_no"] = m.group(1)
            out["boe_date"] = m.group(2).replace("/", "-")
        else:  # fallback: scan the top area of page 1 word by word
            for w in pdf.pages[0].extract_words():
                if re.fullmatch(r"\d{6,8}", w["text"]) and w["top"] < 120:
                    out["boe_no"] = w["text"]
                if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", w["text"]) and w["top"] < 120:
                    out["boe_date"] = w["text"].replace("/", "-")

        # ---- 1b. Part-I duty summary: total IGST (used to tie the summary) ---
        # C. DUTY SUMMARY row: 1.BCD 2.ACD 3.SWS 4.NCCD 5.ADD 6.CVD 7.IGST
        # 8.G.CESS 18.TOT.ASS.VAL — blanks are common, so values are mapped to
        # the nearest column anchor.
        words0 = pdf.pages[0].extract_words(keep_blank_chars=False,
                                            extra_attrs=["upright"])
        lines0 = _cluster_lines([w for w in words0 if w.get("upright", True)])
        for idx, line in enumerate(lines0):
            jt = _joined(line)
            if "7.IGST" in jt and "1.BCD" in jt:
                anchors = {}
                for w in line:
                    m2 = re.match(r"^(\d{1,2})\.", w["text"])
                    if m2 and m2.group(1) not in anchors:
                        anchors[m2.group(1)] = w["x0"]
                for j in range(idx + 1, min(idx + 4, len(lines0))):
                    vals = _map_to_cols(
                        _merge_num_words([w for w in lines0[j] if w["x0"] > 100]),
                        anchors,
                    )
                    if len(vals) >= 3 and "7" in vals:
                        out["igst_total"] = vals["7"]
                        break
                break

        # ---- 2. exporter / supplier block (PART-II, first page with header) --
        # Rotated section labels (B. TRANSACTING PARTIES etc.) are dropped via
        # the 'upright' filter; data words sit left of the 4.THIRD PARTY column.
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False,
                                       extra_attrs=["upright"])
            up = [w for w in words if w.get("upright", True)]
            hdr = next((w for w in up if w["text"].startswith("3.SUP")), None)
            if hdr is None:
                continue
            aeo = next((w for w in up if w["text"].startswith("5.AE")), None)
            if aeo is None:
                aeo = next((w for w in up if w["text"] == "AEO"), None)
            tp = next((w for w in up if w["text"].startswith("4.THIRD")), None)
            top0 = hdr["top"]
            top1 = (aeo["top"] if aeo else top0 + 60) - 0.5
            xcut = min(280.0, (tp["x0"] - 5) if tp else 280.0)
            sel = [w for w in up
                   if top0 + 1.5 < w["top"] < top1 and w["x0"] < xcut]
            if not sel:
                continue
            rows = [" ".join(w["text"] for w in ln)
                    for ln in _cluster_lines(sel)]
            rows = [r.strip() for r in rows if r.strip()]
            if rows:
                out["exporter"] = rows[0]
                addr = rows[1:]
                if addr and addr[-1].upper() in COUNTRY_MAP:
                    addr[-1] = COUNTRY_MAP[addr[-1].upper()]
                out["address"] = " ".join(addr)
            break

        # ---- 3. item + duty rows from PART - III ------------------------------
        # Only upright words are used: rotated labels (DETAILS, B., watermark
        # letters like M/T/I) would otherwise merge into the data lines.
        for page in pdf.pages:
            page_txt = page.extract_text() or ""
            if "1.INVSNO" not in page_txt or "PART - III" not in page_txt:
                continue
            words = page.extract_words(keep_blank_chars=False,
                                       extra_attrs=["upright"])
            lines = _cluster_lines([w for w in words if w.get("upright", True)])
            block = None
            for idx, line in enumerate(lines):
                jt = _joined(line)
                if "1.INVSNO" in jt and "2.ITEMSN" in jt:
                    if block:
                        out["items"].append(_finish_block(block, out["warnings"]))
                    block = {"anchor": None, "av_line_idx": None, "duty_idx": None}
                    continue
                if block is None:
                    continue
                lt = _line_text(line)
                first_lab = next(
                    (w for w in line if 55 < w["x0"] < 100), None
                )
                lab = first_lab["text"] if first_lab else ""

                if not block.get("cth"):
                    mm = ITEM_RE.search(lt) or ITEM_RE_FALLBACK.search(lt)
                    if mm:
                        block["invsno"], block["itemsn"], block["cth"] = (
                            mm.group(1),
                            mm.group(2),
                            mm.group(3),
                        )
                        block["ceth"] = "NOEXCISE" if "NOEXCISE" in jt else (
                            "EXCISE" if "EXCISE" in jt else ""
                        )
                        block["desc"] = re.sub(r"\s+", " ", mm.group(4)).strip()
                    continue

                if "29.ASSESS" in jt and "30.TOTAL" in jt:
                    block["av_line_idx"] = idx
                    block["av_done"] = False
                    continue
                if (block.get("av_line_idx") is not None
                        and not block.get("av_done")
                        and idx <= block["av_line_idx"] + 3):
                    # watermark junk lines can sit between header and values
                    nums = _merge_num_words([w for w in line if w["x0"] > 100])
                    if len(nums) >= 2:
                        block["av"] = _f(nums[-2][0])
                        block["total_duty"] = _f(nums[-1][0])
                        block["av_done"] = True
                    continue

                gst_word = next(
                    (w for w in line if "GST" in w["text"].upper()), None
                )
                if (block["duty_idx"] is None and "1.BCD" in jt
                        and gst_word is not None):
                    # kerning can mangle '5.IGST' -> '5.IOGST', so the column
                    # is found via the 'GST' substring, not the exact word
                    block["anchor"] = {}
                    for w in line:
                        m2 = re.match(r"^(\d{1,2})\.", w["text"])
                        if m2 and m2.group(1) not in block["anchor"]:
                            block["anchor"][m2.group(1)] = w["x0"]
                    mp = re.match(r"^(\d{1,2})\.", gst_word["text"])
                    block["igst_key"] = mp.group(1) if mp else "5"
                    block["duty_idx"] = idx
                    block["rate"] = None
                    block["amount"] = None
                    continue
                if block.get("duty_idx") is not None and block["duty_idx"] >= 0:
                    if block.get("rate") is None and lab == "Rate":
                        block["rate"] = _map_to_cols(_merge_num_words(
                            [w for w in line if w["x0"] > 100]), block["anchor"])
                        continue
                    if block.get("amount") is None and lab == "Amount":
                        block["amount"] = _map_to_cols(_merge_num_words(
                            [w for w in line if w["x0"] > 100]), block["anchor"])
                        continue
                    if lab == "Duty":  # 'Duty Fg' row -> this duty section ends
                        block["duty_idx"] = -1
            if block:
                out["items"].append(_finish_block(block, out["warnings"]))

    # drop unparseable blocks, sort by item no
    items = [it for it in out["items"] if it.get("cth") and it.get("av") is not None]
    items.sort(key=lambda x: (int(x["invsno"]), int(x["itemsn"])))
    out["items"] = items
    return out


def _map_to_cols(nums, anchors):
    """Assign merged numeric values to the nearest duty-column anchor."""
    if not anchors:
        return {}
    vals = {}
    for v, x0, _x1 in nums:
        best = min(anchors.items(), key=lambda kv: abs(kv[1] - x0))
        vals[best[0]] = _f(v)
    return vals


def _finish_block(block, warnings):
    if not block.get("cth"):
        return {}
    rate = None
    amount = None
    rates = {}
    amounts = {}
    if block.get("anchor"):
        r = block.get("rate") or {}
        a = block.get("amount") or {}
        rates = dict(r)
        amounts = dict(a)
        key = block.get("igst_key", "5")   # column '5.IGST' (may be mangled)
        rate = r.get(key)
        amount = a.get(key)
    # IGST base per item: assessable value + all duties levied before IGST
    # (BCD, CVD_05, SWS, SAD, ADD, CVD). G.CESS / SG / T.VALUE / IGST itself
    # are excluded.
    av = block.get("av") or 0.0
    base = av + sum(amounts.get(c, 0.0) for c in ("1", "2", "3", "4", "7", "8"))
    return {
        "invsno": block.get("invsno"),
        "itemsn": block.get("itemsn"),
        "cth": block.get("cth"),
        "ceth": block.get("ceth", ""),
        "desc": block.get("desc", ""),
        "av": block.get("av"),
        "total_duty": block.get("total_duty"),
        "igst_rate": rate,
        "igst_amt": amount,
        "base": round(base, 2),
        "rates": rates,
        "amounts": amounts,
    }


# ----------------------------------------------------------------------------- summary

def build_summary(parsed):
    """HSN-wise GST summary matching the CHA sheet format.

    TAX AMOUNT  = sum of the per-item IGST booked in the BOE (Part-III),
                  with the grand total tied to the BOE's own IGST figure
                  (Part-I duty summary) — any small display-rounding
                  residual is adjusted in the first HSN row.
    TAXABLE     = TAX AMOUNT ÷ GST rate (the IGST-implied base), which is
                  how the sheet derives it (e.g. 1,32,793.40 ÷ 5% = 26,55,868).
    """
    groups = OrderedDict()
    for it in parsed["items"]:
        g = groups.setdefault(
            it["cth"],
            {"si": len(groups) + 1, "hsn": it["cth"], "tax": 0.0,
             "rates": set(), "boe_igst": 0.0, "duty_base": 0.0, "items": 0},
        )
        g["tax"] += it["igst_amt"] or 0.0
        g["boe_igst"] += it["igst_amt"] or 0.0
        g["duty_base"] += it.get("base") or 0.0
        g["items"] += 1
        if it["igst_rate"] is not None:
            g["rates"].add(it["igst_rate"])

    rows = []
    for g in groups.values():
        rate = max(g["rates"]) if g["rates"] else 0.0
        rows.append(
            {
                "SI": g["si"],
                "HSN": g["hsn"],
                "GST @ %": int(rate) if rate == int(rate) else rate,
                "TAX AMOUNT": round(g["tax"], 2),
                "TAXABLE AMOUNT": 0.0,  # filled below (needs final tax)
                "DUTY BASE (AV+BCD+SWS)": round(g["duty_base"], 2),
                "IGST AS PER ITEMS": round(g["boe_igst"], 2),
                "ITEMS": g["items"],
                "_mixed": len(g["rates"]) > 1,
            }
        )

    # tie the tax total to the BOE's own IGST figure
    total_items = sum(x["IGST AS PER ITEMS"] for x in rows)
    boe_total = parsed.get("igst_total")
    residual = None
    if boe_total is not None and rows:
        tol = max(5.0, 0.05 * sum(x["ITEMS"] for x in rows))
        residual = round(boe_total - total_items, 2)
        if abs(residual) <= tol:
            rows[0]["TAX AMOUNT"] = round(rows[0]["TAX AMOUNT"] + residual, 2)
        else:
            rows[0]["_residual_skipped"] = residual
            residual = None

    for x in rows:
        gst = float(x["GST @ %"]) or 0.0
        if gst:
            x["TAXABLE AMOUNT"] = round(x["TAX AMOUNT"] / gst * 100.0, 2)
        else:
            x["TAXABLE AMOUNT"] = 0.0
        x["_residual"] = residual
    return rows


def build_excel(parsed, rows, path_or_buf):
    """Create the Excel workbook that mirrors the pasted format."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "GST Summary"
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    bold = Font(bold=True)
    ctr = Alignment(horizontal="center", vertical="center", wrap_text=True)
    right = Alignment(horizontal="right", vertical="center")
    indian_fmt = "[>=10000000]##\\,##\\,##\\,##0.00;[>=100000]##\\,##\\,##0.00;#,##0.00"

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 10
    for col in ("D", "E"):
        ws.column_dimensions[col].width = 18

    info = [
        ("BOE No.", parsed["boe_no"]),
        ("Date", parsed["boe_date"]),
        ("EXPORTER", parsed["exporter"]),
        ("ADDRESS", parsed["address"]),
    ]
    r = 1
    for label, val in info:
        ws.cell(row=r, column=1, value=label).font = bold
        ws.cell(row=r, column=2, value=":").font = bold
        c = ws.cell(row=r, column=3, value=val)
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5)
        ws.row_dimensions[r].height = 30 if label == "ADDRESS" else 16
        for col in range(1, 6):
            ws.cell(row=r, column=col).border = border
        r += 1

    hdr_row = r
    headers = ["SI", "HSN", "GST @ %", "TAXABLE AMOUNT", "TAX AMOUNT"]
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=hdr_row, column=j, value=h)
        c.font = bold
        c.alignment = ctr
    r += 1
    for row in rows:
        ws.cell(row=r, column=1, value=row["SI"]).alignment = ctr
        ws.cell(row=r, column=2, value=row["HSN"]).alignment = ctr
        ws.cell(row=r, column=3, value=row["GST @ %"]).alignment = ctr
        d = ws.cell(row=r, column=4, value=row["TAXABLE AMOUNT"])
        e = ws.cell(row=r, column=5, value=row["TAX AMOUNT"])
        d.number_format = indian_fmt
        e.number_format = indian_fmt
        d.alignment = right
        e.alignment = right
        r += 1

    tot_taxable = round(sum(x["TAXABLE AMOUNT"] for x in rows), 2)
    tot_tax = round(sum(x["TAX AMOUNT"] for x in rows), 2)
    d = ws.cell(row=r, column=4, value=tot_taxable)
    e = ws.cell(row=r, column=5, value=tot_tax)
    for c in (d, e):
        c.font = bold
        c.number_format = indian_fmt
        c.alignment = right
    total_row = r

    # borders for the summary grid (SI | HSN | GST @ % | TAXABLE | TAX)
    for rr in range(hdr_row, total_row + 1):
        for cc in range(1, 6):
            ws.cell(row=rr, column=cc).border = border

    # ---- item details sheet -------------------------------------------------
    ws2 = wb.create_sheet("Item Details")
    cols = ["SI", "Item SNo", "CTH (HSN)", "Description", "Assessable Value (INR)",
            "Duty Base (AV+BCD+SWS)", "IGST Rate %", "IGST as per BOE (INR)"]
    ws2.append(cols)
    for c in ws2[1]:
        c.font = bold
        c.border = border
        c.alignment = ctr
    si_by_cth = {x["HSN"]: x["SI"] for x in rows}
    for it in parsed["items"]:
        ws2.append([
            si_by_cth.get(it["cth"]),
            it["itemsn"],
            it["cth"],
            it["desc"],
            it["av"],
            it.get("base"),
            it["igst_rate"],
            it["igst_amt"],
        ])
    for rr in ws2.iter_rows(min_row=2):
        for c in rr:
            c.border = border
    for col, w in zip("ABCDEFGH", (8, 10, 12, 40, 20, 20, 12, 20)):
        ws2.column_dimensions[col].width = w
    for rr in range(2, ws2.max_row + 1):
        ws2.cell(row=rr, column=5).number_format = indian_fmt
        ws2.cell(row=rr, column=6).number_format = indian_fmt
        ws2.cell(row=rr, column=8).number_format = indian_fmt

    wb.save(path_or_buf)
    return path_or_buf


# ----------------------------------------------------------------------------- UI

def _summary_html(parsed, rows):
    tot_taxable = _inr(sum(x["TAXABLE AMOUNT"] for x in rows))
    tot_tax = _inr(sum(x["TAX AMOUNT"] for x in rows))

    body = ""
    for x in rows:
        body += (
            f"<tr><td class='c'>{x['SI']}</td><td class='c'>{x['HSN']}</td>"
            f"<td class='c'>{x['GST @ %']}</td>"
            f"<td class='r'>{_inr(x['TAXABLE AMOUNT'])}</td>"
            f"<td class='r'>{_inr(x['TAX AMOUNT'])}</td></tr>"
        )
    body += (
        f"<tr class='tot'><td class='c'></td><td class='c'></td><td class='c'></td>"
        f"<td class='r'>{tot_taxable}</td><td class='r'>{tot_tax}</td></tr>"
    )

    info = ""
    for lab, val in (("BOE No.", parsed["boe_no"]), ("Date", parsed["boe_date"]),
                     ("EXPORTER", parsed["exporter"]), ("ADDRESS", parsed["address"])):
        info += (f"<tr><td class='lab'>{lab}</td><td class='sep'>:</td>"
                 f"<td>{val or ''}</td></tr>")

    return f"""
<div class="boe-wrap"><table class="boe-info">
  {info}
</table>
<table class="boe-table">
  <tr><td class="c h">SI</td><td class="c h">HSN</td><td class="c h">GST @ %</td>
      <td class="c h">TAXABLE AMOUNT</td><td class="c h">TAX AMOUNT</td></tr>
  {body}
</table></div>
<style>
.boe-wrap {{ max-width: 780px; overflow-x: auto; font-family: Arial, sans-serif; }}
.boe-info {{ border-collapse: collapse; width: 100%; min-width: 620px; margin-bottom: 14px; }}
.boe-info td {{ border: 1px solid #000; padding: 5px 10px; font-size: 14px; }}
.boe-info td.lab {{ font-weight: bold; width: 110px; }}
.boe-info td.sep {{ width: 30px; text-align: center; font-weight: bold; }}
.boe-table {{ border-collapse: collapse; width: 100%; min-width: 620px; }}
.boe-table td {{ border: 1px solid #000; padding: 6px 10px; font-size: 14px; }}
.boe-table td.c {{ text-align: center; }}
.boe-table td.r {{ text-align: right; }}
.boe-table td.h {{ font-weight: bold; }}
.boe-table tr.tot td {{ font-weight: bold; }}
</style>
"""


def run_app():
    import streamlit as st

    st.set_page_config(page_title="BOE GST Summary", page_icon="🧾", layout="centered")
    st.title("🧾 BOE GST Summary")

    with st.sidebar:
        st.header("Bill of Entry → GST Summary")
        up = st.file_uploader("Upload BOE PDF (ICEGATE)", type=["pdf"])
        st.caption(
            "Parses Part-III (Duties) of the ICEGATE Bill of Entry PDF, groups "
            "items by HSN and builds the GST summary sheet.\n\n"
            "Run locally:\n\n"
            "pip install streamlit pdfplumber openpyxl\n\n"
            "streamlit run boe_gst_summary_app.py"
        )

    if up is None:
        st.info("Left side panel me apni BOE PDF upload karein — summary yahin ban jayegi.")
        st.stop()

    buf = io.BytesIO(up.getvalue())
    with st.spinner("Parsing BOE PDF…"):
        try:
            parsed = parse_boe(buf.getvalue())
        except Exception as e:  # noqa: BLE001 - show a friendly message instead of a traceback
            st.error(
                "PDF parse nahi ho paya. ICEGATE se download kiya hua original "
                f"BOE PDF hi upload karein.\n\nError: `{e}`"
            )
            st.stop()

    if not parsed["items"]:
        st.error(
            "Is PDF me PART - III (Duties) section nahi mila. Kripya ICEGATE se "
            "download kiya gaya Bill of Entry PDF upload karein."
        )
        st.stop()

    rows = build_summary(parsed)
    n_items = len(parsed["items"])
    mixed = [x for x in rows if x["_mixed"]]

    st.subheader("GST Summary (as per sheet format)")
    st.markdown(_summary_html(parsed, rows), unsafe_allow_html=True)

    tot_igst_items = round(sum(x["IGST AS PER ITEMS"] for x in rows), 2)
    tot_tax = round(sum(x["TAX AMOUNT"] for x in rows), 2)
    boe_total = parsed.get("igst_total")
    cap = (f"Parsed {n_items} items into {len(rows)} HSN groups. "
           f"TAX AMOUNT = IGST as booked per item in the BOE (Part-III); "
           f"TAXABLE AMOUNT = TAX ÷ GST @ %. "
           f"Items ka IGST total: ₹{_inr(tot_igst_items)}")
    if boe_total is not None:
        cap += f" | BOE duty summary ka IGST: ₹{_inr(boe_total)} (total isi se tied hai)"
    st.caption(cap)
    if mixed:
        st.warning(
            "In HSN groups me alag-alag IGST rates mile the: "
            + ", ".join(f"{x['HSN']} ({sorted(x['rates'])})" for x in mixed)
            + " — highest rate use kiya gaya hai."
        )

    # ---- downloads ----------------------------------------------------------
    excel_buf = io.BytesIO()
    build_excel(parsed, rows, excel_buf)
    st.download_button(
        "⬇️ Download Excel (GST Summary + Item Details)",
        data=excel_buf.getvalue(),
        file_name=f"BOE_{parsed['boe_no'] or 'summary'}_GST.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # ---- verification / detail ---------------------------------------------
    with st.expander("🔍 Item-level details & verification"):
        try:
            import pandas as pd
        except ImportError:
            pd = None
        if pd is None:
            st.caption(
                "Item-level table ke liye pandas chahiye: `pip install pandas`. "
                "(Summary aur Excel download bina pandas ke bhi theek kaam karte hain.)"
            )
        else:
            df = pd.DataFrame(parsed["items"])
            df = df.rename(columns={
                "invsno": "Inv SNo", "itemsn": "Item SNo", "cth": "HSN",
                "ceth": "CETH", "desc": "Description",
                "av": "Assessable Value (INR)", "base": "IGST Base (AV+BCD+SWS)",
                "igst_rate": "IGST Rate %",
                "igst_amt": "IGST as per BOE (INR)", "total_duty": "Total Duty (INR)",
            })
            si_map = {x["HSN"]: x["SI"] for x in rows}
            df.insert(0, "SI", df["HSN"].map(si_map))
            df = df[["SI", "Inv SNo", "Item SNo", "HSN", "Description",
                     "Assessable Value (INR)", "IGST Base (AV+BCD+SWS)",
                     "IGST Rate %", "IGST as per BOE (INR)", "Total Duty (INR)"]]
            st.dataframe(df, use_container_width=True, height=400)

            chk = pd.DataFrame(rows)[["SI", "HSN", "GST @ %", "TAXABLE AMOUNT",
                                      "TAX AMOUNT", "IGST AS PER ITEMS",
                                      "DUTY BASE (AV+BCD+SWS)", "ITEMS"]]
            st.markdown("**HSN-wise check:**")
            st.dataframe(chk, use_container_width=True)
            st.caption(
                "TAX AMOUNT = per-item IGST (Part-III) ka HSN-wise sum; grand total "
                "BOE ke Part-I duty summary (IGST) se tie hota hai — chhota sa "
                "display-rounding residual pehle HSN row me adjust hota hai. "
                "TAXABLE AMOUNT = TAX ÷ GST rate (IGST-implied base). "
                "'DUTY BASE (AV+BCD+SWS)' sirf reference ke liye hai — customs ka "
                "actual IGST base (Assessable Value + BCD + SWS)."
            )


if __name__ == "__main__":
    run_app()
