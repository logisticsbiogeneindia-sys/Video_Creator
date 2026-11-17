# Biogene India - Bill of Entry Extractor (Streamlit Cloud compatible)

## Streamlit Cloud deployment
1. Upload `app.py` and `requirements.txt` into the SAME folder / repository root used by your Streamlit app.
2. `requirements.txt` must contain PyMuPDF (package name) because the code imports `fitz`.
3. In Streamlit Cloud, open Manage app > Reboot app, or push a new Git commit to trigger redeploy.
4. Set the app's Main file path to `app.py` if the file is at repository root.

## Local Windows run
```bash
py -m pip install -r requirements.txt
py -m streamlit run app.py
```

No Poppler / pdftotext installation is needed. PDF text extraction is done with PyMuPDF.

## Notes
This parser targets text-based ICEGATE Bill of Entry PDFs with the same Part II / Part III item-duty layout as the sample. Scanned/image-only PDFs need OCR. Always verify totals against the original BOE.
