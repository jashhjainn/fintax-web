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


def _extract_bill_number(lines: list[str], text_blob: str) -> str | None:
    """
    Extract bill number from invoice text using multiple patterns.
    Returns the first found bill number or None if not found.
    """
    if not lines and not text_blob:
        return None
    
    # Enhanced bill number patterns with specific support for "bill no. - 100" format
    bill_patterns = [
        # Specific pattern for "bill no. - 100" format (your example)
        r"(?:bill|invoice)\s*(?:no\.?|number)?\s*[-:]\s*([A-Z0-9-]+)",
        
        # General patterns for various formats
        r"(?:bill|invoice)\s*(?:no\.?|number)?\s*[:#-]?\s*([A-Z0-9-]+)",
        r"\b(?:bill|invoice)\s*[:#]\s*([A-Z0-9-]{4,})\b",
        r"\b([A-Z0-9-]{6,})\b",  # Standalone alphanumeric codes
        r"\b\d{2}[A-Z]{5}\d{7}\b",  # GST-style invoice numbers
        r"\b[A-Z]{2,4}-?\d{3,8}\b",  # Common format: ABC-12345 or ABC12345
        
        # Additional patterns for better coverage
        r"(?:bill|invoice)\s*(?:no\.?|number)?\s*[:#]\s*([A-Z0-9-]+)",
        r"(?:bill|invoice)\s*(?:no\.?|number)?\s*[-]\s*([A-Z0-9-]+)",
        r"(?:bill|invoice)\s*(?:no\.?|number)?\s*[:]\s*([A-Z0-9-]+)",
        r"(?:bill|invoice)\s*(?:no\.?|number)?\s*#\s*([A-Z0-9-]+)",
    ]
    
    # Search in lines first (more targeted)
    for line in lines:
        for pattern in bill_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                bill_no = match.group(1).strip()
                # Validate that it looks like a bill number
                if len(bill_no) >= 2 and re.search(r'[A-Z0-9]', bill_no):
                    return bill_no
    
    # If not found in lines, search in full text blob
    if text_blob:
        for pattern in bill_patterns:
            match = re.search(pattern, text_blob, re.IGNORECASE)
            if match:
                bill_no = match.group(1).strip()
                if len(bill_no) >= 2 and re.search(r'[A-Z0-9]', bill_no):
                    return bill_no
    
    return None


_HSN_RATE_MAP = {
    # Common GST slabs keyed by HSN. Extend as needed.
    "0405": 5.0,
    "1905": 5.0,
    "3401": 5.0,
    "8516": 18.0,
    "0401": 0.0,
    "0902": 5.0,
    "3305": 18.0,
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


def _validate_invoice_fields(vendor, invoice_date, total_amount, hsn_codes) -> dict:
    """
    Validate that all required invoice fields are present.
    Returns a validation result with missing fields.
    """
    missing_fields = []
    
    # Check bill number (extracted from vendor field or lines)
    if not vendor or len(vendor.strip()) < 3:
        missing_fields.append("bill number")
    
    # Check invoice date
    if not invoice_date:
        missing_fields.append("invoice date")
    
    # Check total amount
    if not total_amount:
        missing_fields.append("total amount")
    
    # Check HSN code
    if not hsn_codes or len(hsn_codes) == 0:
        missing_fields.append("hsn code")
    
    is_valid = len(missing_fields) == 0
    
    return {
        "is_valid": is_valid,
        "missing_fields": missing_fields,
        "message": "kindly upload the image of invoice" if not is_valid else "Invoice validation successful"
    }


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
    
    # Extract fields for validation
    vendor = _extract_vendor(lines)
    bill_number = _extract_bill_number(lines, cleaned_text)
    invoice_date = _extract_date(lines)
    total_amount = _extract_total(lines)
    
    # Perform validation
    validation_result = _validate_invoice_fields(vendor, invoice_date, total_amount, hsn_codes)

    return {
        "filename": filename,
        "raw_text": raw_text,
        "cleaned_text": cleaned_text,
        "lines": lines,
        "vendor": vendor,
        "bill_number": bill_number,
        "invoice_date": invoice_date,
        "total_amount": total_amount,
        "items": items,
        "hsn_codes": hsn_codes,
        "gst_payable": gst_payable,
        "validation": validation_result,
        "status": "processed" if validation_result["is_valid"] else "validation_failed",
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
