import pytesseract as pyt
import cv2
import numpy as np
import re
from datetime import datetime

TESSERACT_CMD = "C:\\Users\\Jash Jain\\AppData\\Local\\Programs\\Tesseract-OCR\\tesseract.exe"


def _normalize_lines(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]

def _clean_amount_token(token: str) -> str:
    # Fix common OCR errors in numbers
    token = token.replace(",", "").replace(" ", "")
    token = token.replace("O", "0").replace("o", "0")
    token = token.replace("S", "5").replace("s", "5")
    token = token.replace("I", "1").replace("l", "1")
    return token


def _extract_date(lines: list[str]) -> str | None:
    patterns = [
        r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b",
        r"\b(\d{4}[/-]\d{2}[/-]\d{2})\b",
        r"\b(\d{2}[/-]\d{2}[/-]\d{2})\b",
    ]
    for ln in lines:
        for pat in patterns:
            m = re.search(pat, ln)
            if m:
                return m.group(1)
    return None


def _extract_total(lines: list[str]) -> str | None:
    # Prefer lines containing total keywords, otherwise fallback to max amount
    total_like = []
    fallback = []
    total_keywords = re.compile(
        r"\b(total|grand\s*total|amount\s*due|net\s*total|balance\s*due|total\s*amount|amount\s*payable)\b",
        re.I,
    )
    money_re = re.compile(r"(?:₹|rs\.?|inr)?\s*([0-9OoslI,]+(?:\.\d{2})?)", re.I)

    for ln in lines:
        line = ln.strip()
        if not line:
            continue
        for m in money_re.finditer(line):
            val = _parse_amount_token(m.group(1))
            if val is None:
                continue
            if total_keywords.search(line):
                total_like.append(val)
            else:
                fallback.append(val)

    pool = total_like if total_like else fallback
    if not pool:
        return None
    total = max(pool)
    return f"₹ {total:,.2f}"


def _extract_vendor(lines: list[str]) -> str | None:
    if not lines:
        return None
    return lines[0][:80]


_HSN_RATE_MAP = {
    # Common GST slabs keyed by HSN. Extend as needed.
   
    "8517": 18.0,
    "9401": 18.0,
    "8471": 18.0,
    "1512": 5.0,
    "1806": 18.0
}


def _parse_amount_token(token: str) -> float | None:
    if not token:
        return None
    cleaned = _clean_amount_token(token)
    cleaned = re.sub(r"[^\d.]", "", cleaned)
    if not cleaned:
        return None
    try:
        val = float(cleaned)
    except ValueError:
        return None
    if val <= 0:
        return None
    return val


def _last_amount_in_line(line: str) -> float | None:
    money_re = re.compile(r"([0-9OoslI,]+(?:\.\d{2})?)")
    match = None
    for m in money_re.finditer(line):
        match = m
    return _parse_amount_token(match.group(1)) if match else None


def _extract_hsn_items(lines: list[str]) -> list[dict]:
    items: list[dict] = []
    hsn_label_re = re.compile(r"\b(hsn|hsn/sac|sac)\b", re.I)
    hsn_code_re = re.compile(r"\b(\d{4,8})\b")
    end_table_re = re.compile(
        r"\b(total|grand\s*total|amount\s*due|net\s*total|balance\s*due|gst|tax)\b",
        re.I,
    )

    in_table = False

    for ln in lines:
        if not ln:
            continue
        if hsn_label_re.search(ln):
            # Header or line explicitly mentioning HSN
            in_table = True
            continue
        if in_table and end_table_re.search(ln):
            in_table = False
            continue
        if not in_table:
            continue
        codes = [m.group(1) for m in hsn_code_re.finditer(ln)]
        if not codes:
            continue
        amount = _last_amount_in_line(ln)
        if amount is None:
            continue

        # Use only HSN rates from the configured map
        rate = _HSN_RATE_MAP.get(codes[0])
        if rate is None:
            continue

        # Item amount is GST-inclusive, so back out GST
        base = amount / (1 + rate / 100.0)
        gst_amount = amount - base

        items.append({
            "hsn": codes[0],
            "amount_gross": round(amount, 2),
            "gst_rate": rate,
            "gst_amount": round(gst_amount, 2),
            "amount_base": round(base, 2),
            "description": ln[:120],
        })

    return items


def process_ocr(image_bytes: bytes, filename: str) -> dict:
    pyt.pytesseract.tesseract_cmd = TESSERACT_CMD

    npbuf = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(npbuf, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Unable to decode image for OCR.")

    # Preprocess to improve OCR accuracy
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    config = "--oem 3 --psm 6"
    raw_text = pyt.image_to_string(thresh, config=config)
    lines = _normalize_lines(raw_text)
    cleaned_text = "\n".join(lines)
    items = _extract_hsn_items(lines)
    gst_payable = round(sum(i.get("gst_amount", 0.0) for i in items), 2) if items else None
    hsn_codes = sorted({i["hsn"] for i in items}) if items else []

    return {
        "filename": filename,
        "raw_text": raw_text,
        "cleaned_text": cleaned_text,
        "lines": lines,
        "vendor": _extract_vendor(lines),
        "invoice_date": _extract_date(lines),
        "total_amount": _extract_total(lines),
        "items": items,
        "hsn_codes": hsn_codes,
        "gst_payable": gst_payable,
        "status": "processed",
        "created_at": datetime.utcnow(),
    }


if __name__ == "__main__":
    # Quick local test: python pytes.py path/to/image
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pytes.py <image_path>")
        raise SystemExit(1)
    path = sys.argv[1]
    img = cv2.imread(path)
    if img is None:
        print("Could not read image:", path)
        raise SystemExit(1)
    pyt.pytesseract.tesseract_cmd = TESSERACT_CMD
    text = pyt.image_to_string(img)
    print(text)
