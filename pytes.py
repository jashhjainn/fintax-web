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
    # Prefer lines containing total keywords
    total_like = []
    fallback = []
    total_keywords = re.compile(r"\b(total|grand total|amount due|net total|balance due)\b", re.I)
    money_re = re.compile(r"(₹\s*)?([0-9OoslI,.\s]{2,})")

    for ln in lines:
        line = ln.strip()
        if not line:
            continue
        for m in money_re.finditer(line):
            raw = _clean_amount_token(m.group(2))
            if re.search(r"\d", raw) is None:
                continue
            try:
                val = float(raw)
            except ValueError:
                continue
            if val <= 0:
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

    return {
        "filename": filename,
        "raw_text": raw_text,
        "cleaned_text": cleaned_text,
        "lines": lines,
        "vendor": _extract_vendor(lines),
        "invoice_date": _extract_date(lines),
        "total_amount": _extract_total(lines),
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
