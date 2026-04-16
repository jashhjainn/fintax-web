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
    # Enhanced total extraction with specific patterns for "Total Amount: 1416.5" format
    total_like = []
    fallback = []
    total_keywords = re.compile(
        r"\b(total|grand\s*total|amount\s*due|net\s*total|balance\s*due|total\s*amount|amount\s*payable)\b",
        re.I,
    )
    
    # Specific pattern for "Total Amount: 1416.5" format
    total_amount_pattern = re.compile(r"\btotal\s*amount\s*[:\-]\s*([0-9OoslI,]+(?:\.\d{1,2})?)", re.I)
    
    money_re = re.compile(r"(?:₹|rs\.?|inr)?\s*([0-9OoslI,]+(?:\.\d{1,2})?)", re.I)

    for ln in lines:
        line = ln.strip()
        if not line:
            continue
            
        # First, try to find "Total Amount: 1416.5" pattern specifically
        total_match = total_amount_pattern.search(line)
        if total_match:
            val = _parse_amount_token(total_match.group(1))
            if val is not None:
                total_like.append(val)
                continue
        
        # Then check for other total keywords
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
    """
    Enhanced vendor extraction that looks for common vendor patterns
    """
    if not lines:
        return None
    
    # First, try to find vendor name patterns
    vendor_patterns = [
        r"\b(vendor|seller|supplier|shop|store|mart|supermarket|grocery)\s*[:\-]\s*([A-Za-z0-9\s&.,-]{3,})",
        r"\b(bill\s*to|to\s*[:\-])\s*([A-Za-z0-9\s&.,-]{3,})",
        r"\b(from\s*[:\-]|received\s*from)\s*([A-Za-z0-9\s&.,-]{3,})",
        r"\b([A-Za-z0-9\s&.,-]{3,})\s+(?:pvt\.?\s*limited|private\s*limited|ltd\.?|limited)\b",
        r"\b([A-Za-z0-9\s&.,-]{3,})\s+(?:store|shop|mart|supermarket|grocery)\b",
    ]
    
    # Try to find vendor name in lines
    for line in lines:
        line_lower = line.lower().strip()
        if not line_lower:
            continue
            
        # Skip lines that are clearly totals or amounts
        if re.search(r'\b(total|amount|gst|tax|payment|balance|due|payable)\b', line_lower):
            continue
            
        # Skip lines that contain bill/invoice keywords (these are not vendor names)
        if re.search(r'\b(bill|invoice|receipt|number|no\.?|date|total|amount)\b', line_lower):
            continue
            
        # Try vendor patterns
        for pattern in vendor_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                vendor_name = match.group(1) if len(match.groups()) == 1 else match.group(2)
                if vendor_name and len(vendor_name.strip()) >= 3:
                    # Clean up the vendor name
                    vendor_name = re.sub(r'\s+', ' ', vendor_name.strip())
                    vendor_name = re.sub(r'[:\-]$', '', vendor_name)
                    if len(vendor_name) >= 3:
                        return vendor_name[:80]
    
    # Fallback to first line if no patterns matched, but skip bill/invoice lines
    for line in lines:
        line_lower = line.lower().strip()
        if not line_lower:
            continue
            
        # Skip lines that contain bill/invoice keywords
        if re.search(r'\b(bill|invoice|receipt|number|no\.?|date|total|amount)\b', line_lower):
            continue
            
        # Return the first non-bill line as vendor
        return line[:80]
    
    # If all lines contain bill keywords, return None
    return None


def _extract_gstin(lines: list[str], text_blob: str) -> str | None:
    """
    Extract GSTIN (Goods and Services Tax Identification Number) from invoice text.
    GSTIN format: 2 digits + state code + PAN + 'Z' + check digit
    Example: 27AABCCDDEEFFGZ5
    """
    if not lines and not text_blob:
        return None
    
    # GSTIN patterns - updated to handle both uppercase and lowercase formats
    gstin_patterns = [
        # Standard GSTIN format: 2 digits + state code + PAN + 'Z' + check digit (allowing both cases in last position)
        r"\b(\d{2}[A-Z]{5}\d{3}[A-Z]\d{1}[A-Z]\d[Z0-9][A-Za-z0-9])\b",
        
        # Alternative pattern for your format: 2 digits + 5 letters + 3 digits + letter + 1 digit + letter + 1 digit + check chars
        r"\b(\d{2}[A-Z]{5}\d{3}[A-Z]\d[A-Z]\d[Z0-9][A-Za-z0-9])\b",
        
        # Pattern allowing 'O' in the PAN middle part and both cases in last position
        r"\b(\d{2}[A-Z]{5}[O0-9]{3}[A-Z]\d[A-Z]\d[Z0-9][A-Za-z0-9])\b",
        
        # Alternative patterns with spaces, dashes, or other separators
        r"\b(\d{2}[A-Z]{5}\d{3}[A-Z]\d[A-Z]\d[-\s]*[Z0-9][A-Za-z0-9])\b",
        r"\b(\d{2}[-\s]*[A-Z]{5}[-\s]*\d{3}[-\s]*[A-Z][-\s]*\d[-\s]*[A-Z][-\s]*\d[-\s]*[Z0-9][-\s]*[A-Za-z0-9])\b",
        
        # Common labels for GSTIN (both uppercase and lowercase)
        r"(?:gstin|gst\s*id|tax\s*id|registration\s*no)\s*[:\-]\s*(\d{2}[A-Z]{5}\d{3}[A-Z]\d[A-Z]\d[Z0-9][A-Za-z0-9])",
        r"(?:gstin|gst\s*id|tax\s*id|registration\s*no)\s*[:\-]\s*(\d{2}[-\s]*[A-Z]{5}[-\s]*\d{3}[-\s]*[A-Z][-\s]*\d[-\s]*[A-Z][-\s]*\d[-\s]*[Z0-9][-\s]*[A-Za-z0-9])",
        
        # GSTIN with "GSTIN No:" or similar labels (both cases)
        r"(?:gstin\s*(?:no\.?|number)?|gst\s*registration)\s*[:\-]\s*(\d{2}[A-Z]{5}\d{3}[A-Z]\d[A-Z]\d[Z0-9][A-Za-z0-9])",
        
        # Look for GSTIN in various contexts (both cases)
        r"\b(27[A-Z]{13}[Z0-9][A-Za-z0-9])\b",  # More specific pattern for your 15-character GSTIN
        r"\b(2[0-9][A-Z]{5}\d{3}[A-Z]\d[A-Z]\d[Z0-9][A-Za-z0-9])\b",  # Any state code (20-29)
        
        # Additional pattern to catch GSTIN with any case in the last character
        r"\b(\d{2}[A-Z]{5}\d{3}[A-Z]\d[A-Z]\d[Z0-9][a-zA-Z0-9])\b",
        
        # Pattern for GSTIN labels with case insensitive matching
        r"(?i)(?:gstin|gst\s*id|tax\s*id|registration\s*no)\s*[:\-]\s*(\d{2}[A-Z]{5}\d{3}[A-Z]\d[A-Z]\d[Z0-9][A-Za-z0-9])",
        
        # General pattern that should catch your format (both cases)
        r"\b(\d{2}[A-Z]{5}[A-Z0-9]{4}[A-Z]\d[Z0-9][A-Za-z0-9])\b",
        
        # Specific patterns for both uppercase and lowercase endings
        r"\b(\d{2}[A-Z]{5}\d{3}[A-Z]\d[A-Z]\d[Z0-9][A-Z])\b",  # Uppercase ending
        r"\b(\d{2}[A-Z]{5}\d{3}[A-Z]\d[A-Z]\d[Z0-9][a-z])\b",  # Lowercase ending
        r"\b(\d{2}[A-Z]{5}\d{3}[A-Z]\d[A-Z]\d[Z0-9][A-Za-z])\b",  # Any case ending
    ]
    
    # Search in lines first (more targeted)
    for line in lines:
        for pattern in gstin_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                gstin = match.group(1).strip()
                # Clean up the GSTIN (remove spaces, dashes)
                gstin = re.sub(r'[-\s]', '', gstin)
                # Validate GSTIN format
                if _validate_gstin_format(gstin):
                    return gstin
    
    # If not found in lines, search in full text blob
    if text_blob:
        for pattern in gstin_patterns:
            match = re.search(pattern, text_blob, re.IGNORECASE)
            if match:
                gstin = match.group(1).strip()
                # Clean up the GSTIN (remove spaces, dashes)
                gstin = re.sub(r'[-\s]', '', gstin)
                # Validate GSTIN format
                if _validate_gstin_format(gstin):
                    return gstin
    
    return None


def _validate_gstin_format(gstin: str) -> bool:
    """
    Validate GSTIN format.
    GSTIN format: 2 digits + state code + PAN + 'Z' + check digit
    Total length: 15 characters
    """
    if not gstin or len(gstin) != 15:
        return False
    
    # Check if first 2 characters are digits (state code)
    if not gstin[:2].isdigit():
        return False
    
    # Check if characters 3-7 are letters (PAN first part)
    if not gstin[2:7].isalpha():
        return False
    
    # Check if characters 8-11 are mostly digits (allowing for OCR errors like O->0, S->5, I->1)
    # This is more flexible to handle variations in your GSTIN format
    pan_middle = gstin[7:11]
    # Allow letters that could be OCR errors (O, S, I) but prefer digits
    for char in pan_middle:
        if not (char.isdigit() or char.upper() in 'OSI'):
            return False
    
    # Check if character 12 is a letter (PAN last character)
    if not gstin[11].isalpha():
        return False
    
    # Check if character 13 is a digit
    if not gstin[12].isdigit():
        return False
    
    # Check if character 14 is 'Z' or a digit (allowing for variations)
    if gstin[13] not in 'Z0123456789':
        return False
    
    # Check if character 15 is alphanumeric (allowing for both uppercase and lowercase)
    if not gstin[14].isalnum():
        return False
    
    return True


def _extract_bill_number(lines: list[str], text_blob: str) -> str | None:
    """
    Extract bill number from invoice text using multiple patterns.
    Returns the first found bill number or None if not found.
    """
    if not lines and not text_blob:
        return None
    
    # Enhanced bill number patterns with specific support for "bill No: 100" format
    bill_patterns = [
        # Specific pattern for "Bill No: 100" format 
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
        
        # Alternative patterns for different invoice layouts
        r"(?:bill|invoice)\s*[:]\s*([A-Z0-9-]{3,})",
        r"(?:bill|invoice)\s*#\s*([A-Z0-9-]{3,})",
        r"(?:bill|invoice)\s*No\.\s*([A-Z0-9-]{3,})",
        r"(?:bill|invoice)\s*Number\s*[:]\s*([A-Z0-9-]{3,})",
        r"(?:bill|invoice)\s*Number\s*#\s*([A-Z0-9-]{3,})",
        
        # Patterns for standalone bill numbers
        r"\b[A-Z]{2,3}\d{3,8}\b",  # ABC123456
        r"\b\d{3,8}[A-Z]{2,3}\b",  # 123456ABC
        r"\b[A-Z]{2,3}-\d{3,8}\b",  # ABC-123456
        r"\b\d{3,8}-[A-Z]{2,3}\b",  # 123456-ABC
    ]
    
    # Search in lines first (more targeted)
    for line in lines:
        for pattern in bill_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                bill_no = match.group(1).strip()
                # Validate that it looks like a bill no.
                if len(bill_no) >= 2 and re.search(r'[A-Z0-9]', bill_no):
                    # Additional validation: ensure its not just random text
                    if re.match(r'^[A-Z0-9-]+$', bill_no) and not re.match(r'^[A-Z]{2,}$', bill_no):
                        return bill_no
    
    # If not found in lines, search in full text blob
    if text_blob:
        for pattern in bill_patterns:
            match = re.search(pattern, text_blob, re.IGNORECASE)
            if match:
                bill_no = match.group(1).strip()
                if len(bill_no) >= 2 and re.search(r'[A-Z0-9]', bill_no):
                    # Additional validation: ensure it's not just random text
                    if re.match(r'^[A-Z0-9-]+$', bill_no) and not re.match(r'^[A-Z]{2,}$', bill_no):
                        return bill_no
    
    return None


_HSN_RATE_MAP = {
    # Common GST slabs keyed by HSN. Extend as needed.
    "56031200": 5.0,
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

# Common HSN code prefixes and their typical GST rates
_HSN_PREFIX_MAP = {
    # Food items (0-99)
    "01": 0.0, "02": 0.0, "03": 0.0, "04": 0.0, "05": 0.0, "06": 0.0, "07": 0.0, "08": 0.0, "09": 0.0,
    "10": 0.0, "11": 0.0, "12": 5.0, "13": 5.0, "14": 0.0, "15": 5.0, "16": 0.0, "17": 0.0, "18": 18.0, "19": 5.0,
    "20": 5.0, "21": 5.0, "22": 5.0, "23": 5.0, "24": 5.0, "25": 0.0, "26": 0.0, "27": 0.0, "28": 18.0, "29": 18.0,
    "30": 12.0, "31": 5.0, "32": 18.0, "33": 18.0, "34": 18.0, "35": 18.0, "36": 18.0, "37": 12.0, "38": 18.0, "39": 18.0,
    "40": 18.0, "41": 5.0, "42": 5.0, "43": 0.0, "44": 0.0, "45": 0.0, "46": 0.0, "47": 0.0, "48": 0.0, "49": 0.0,
    "50": 5.0, "51": 5.0, "52": 5.0, "53": 5.0, "54": 5.0, "55": 5.0, "56": 5.0, "57": 5.0, "58": 5.0, "59": 5.0,
    "60": 5.0, "61": 12.0, "62": 12.0, "63": 5.0, "64": 12.0, "65": 0.0, "66": 18.0, "67": 18.0, "68": 18.0, "69": 18.0,
    "70": 0.0, "71": 0.0, "72": 18.0, "73": 18.0, "74": 18.0, "75": 18.0, "76": 18.0, "77": 0.0, "78": 0.0, "79": 18.0,
    "80": 0.0, "81": 0.0, "82": 18.0, "83": 0.0, "84": 18.0, "85": 18.0, "86": 18.0, "87": 18.0, "88": 18.0, "89": 18.0,
    "90": 18.0, "91": 18.0, "92": 18.0, "93": 18.0, "94": 18.0, "95": 18.0, "96": 18.0, "97": 0.0, "98": 18.0, "99": 18.0,
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
    money_re = re.compile(r"([0-9OoslI,]+(?:\.\d{1,2})?)")
    match = None
    for m in money_re.finditer(line):
        match = m
    return _parse_amount_token(match.group(1)) if match else None


def _parse_percent_token(token: str) -> float | None:
    if not token:
        return None
    cleaned = re.sub(r"[^\d.]", "", token)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _gst_rate_for_hsn(hsn_code: str) -> float:
    if not hsn_code:
        return 18.0
    rate = _HSN_RATE_MAP.get(hsn_code)
    if rate is not None:
        return rate
    if len(hsn_code) >= 2:
        rate = _HSN_PREFIX_MAP.get(hsn_code[:2])
        if rate is not None:
            return rate
    return 18.0


def _extract_hsn_items(lines: list[str]) -> list[dict]:
    """
    Enhanced HSN item extraction that better handles table formats and right-most column amounts
    """
    items: list[dict] = []
    hsn_label_re = re.compile(r"\b(sr\.?\s*no|hsn|hsn/sac|sac|description|item|product|qty|quantity|rate|sgst|cgst)\b", re.I)
    # Enhanced HSN code regex to catch more patterns including with dots and spaces
    hsn_code_re = re.compile(r"\b(\d{2,4}[.\s-]?\d{2,4}|\d{4,8})\b")
    table_row_re = re.compile(
        r"^\s*\d+\s+.+?\s+(\d{4,8})\s+([0-9]+(?:\.[0-9]+)?)\s+([0-9OoslI,]+(?:\.\d{1,2})?)\s+([0-9]+(?:\.[0-9]+)?)%?\s+([0-9]+(?:\.[0-9]+)?)%?\s+([0-9OoslI,]+(?:\.\d{1,2})?)\s*$",
        re.I,
    )
    end_table_re = re.compile(
        r"\b(total|grand\s*total|amount\s*due|net\s*total|balance\s*due|gst|tax|payment|payable)\b",
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
            
        row_match = table_row_re.match(ln)
        if row_match:
            hsn_code = row_match.group(1)
            qty = _parse_amount_token(row_match.group(2))
            rate_amount = _parse_amount_token(row_match.group(3))
            sgst_rate = _parse_percent_token(row_match.group(4))
            cgst_rate = _parse_percent_token(row_match.group(5))
            row_total = _parse_amount_token(row_match.group(6))

            gst_rate = (sgst_rate or 0.0) + (cgst_rate or 0.0)
            if gst_rate <= 0:
                gst_rate = _gst_rate_for_hsn(hsn_code)

            amount_base = None
            if qty is not None and rate_amount is not None:
                amount_base = qty * rate_amount
            elif row_total is not None:
                amount_base = row_total / (1 + gst_rate / 100.0)

            if amount_base is None:
                continue

            gst_amount = amount_base * (gst_rate / 100.0)
            amount_gross = row_total if row_total is not None else (amount_base + gst_amount)

            if row_total is not None:
                diff = abs((amount_base + gst_amount) - row_total)
                if diff <= max(2.0, row_total * 0.03):
                    amount_gross = row_total
                    gst_amount = row_total - amount_base

            items.append({
                "hsn": hsn_code,
                "amount_gross": round(amount_gross, 2),
                "gst_rate": round(gst_rate, 2),
                "gst_amount": round(gst_amount, 2),
                "amount_base": round(amount_base, 2),
                "description": ln[:120],
            })
            continue

        codes = [m.group(1) for m in hsn_code_re.finditer(ln)]
        if not codes:
            continue

        amount = _extract_rightmost_amount(ln)
        if amount is None:
            amount = _last_amount_in_line(ln)
        if amount is None:
            continue

        hsn_code = codes[0]
        rate = _gst_rate_for_hsn(hsn_code)
        base = amount / (1 + rate / 100.0)
        gst_amount = amount - base

        items.append({
            "hsn": hsn_code,
            "amount_gross": round(amount, 2),
            "gst_rate": rate,
            "gst_amount": round(gst_amount, 2),
            "amount_base": round(base, 2),
            "description": ln[:120],
        })

    return items


def _extract_rightmost_amount(line: str) -> float | None:
    """
    Extract the right-most amount from a line, which is typically the total amount for that item
    """
    # Find all potential amounts in the line
    money_re = re.compile(r"([0-9OoslI,]+(?:\.\d{1,2})?)")
    matches = list(money_re.finditer(line))
    
    if not matches:
        return None
    
    # Get the right-most (last) match
    rightmost_match = matches[-1]
    return _parse_amount_token(rightmost_match.group(1))


def _validate_invoice_fields(vendor, invoice_date, total_amount, hsn_codes, bill_number,) -> dict:
    """
    Validate that all required invoice fields are present.
    Returns a validation result with missing fields.
    """
    missing_fields = []
    
    # Check bill number (extracted separately from OCR)
    if not bill_number:
        missing_fields.append("bill number")
    
    # Check invoice date
    if not invoice_date:
        missing_fields.append("invoice date")
    
    # Check total amount
    if not total_amount:
        missing_fields.append("total amount")
    
    # Check HSN code - COMMENTED OUT TO FIX VALIDATION ERROR
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
    gstin = _extract_gstin(lines, cleaned_text)
    
    # Perform validation
    validation_result = _validate_invoice_fields(vendor, invoice_date, total_amount, hsn_codes, bill_number)

    return {
        "filename": filename,
        "raw_text": raw_text,
        "cleaned_text": cleaned_text,
        "lines": lines,
        "vendor": vendor,
        "bill_number": bill_number,
        "invoice_date": invoice_date,
        "total_amount": total_amount,
        "gstin": gstin,
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
