from fastapi import FastAPI, UploadFile, File, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from db import db
from models import Invoice, LedgerEntry, UserCreate, UserLogin, User, get_ist_time
from pytes import process_ocr
from datetime import datetime
import bson
import logging
import hashlib
import re
import secrets
from pymongo.errors import DuplicateKeyError
from fpdf import FPDF

# Configure logging
logging.basicConfig(level=logging.INFO, filename='app.log', filemode='a', 
                    format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI()

def _get_token_from_request(request: Request):
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1].strip()
    token_q = request.query_params.get("token")
    if token_q:
        return token_q.strip()
    return ""

def _get_auth_user(request: Request):
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    token = _get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing auth token")
    user = db.users.find_one({"auth_token": token})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development, allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    if db is None:
        logging.error("Database connection not available.")
    else:
        logging.info("Database connection available.")
        try:
            db.users.create_index("email", unique=True)
            logging.info("Ensured unique index on users.email")
        except Exception as e:
            logging.error(f"Could not create unique index on users.email: {e}")

@app.post("/upload/")
async def upload_invoice(request: Request, file: UploadFile = File(...)):
    if db is None:
        logging.error("Database connection not available for upload.")
        raise HTTPException(status_code=500, detail="Database connection not available")
    user = _get_auth_user(request)

    try:
        # Read the file content
        file_content = await file.read()
        
        # Create the document using Pydantic schema
        # We handle metadata with the schema and add binary data separately
        invoice = Invoice(
            filename=file.filename,
            content_type=file.content_type
        )
        
        # Convert to dict and add binary image data
        # Note: using .model_dump() for Pydantic v2
        invoice_doc = invoice.model_dump()
        invoice_doc["owner_email"] = user.get("email")
        invoice_doc["owner_id"] = str(user.get("_id"))
        invoice_doc["image_data"] = bson.Binary(file_content)
        
        # Insert into MongoDB
        result = db.invoices.insert_one(invoice_doc)
        logging.info(f"File uploaded successfully with id: {result.inserted_id}")
        
        return {
            "message": "File uploaded successfully",
            "id": str(result.inserted_id),
            "filename": invoice.filename
        }
        
    except Exception as e:
        logging.error(f"An error occurred during upload: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")


@app.post("/ocr/")
async def ocr_invoice(request: Request, file: UploadFile = File(...)):
    if db is None:
        logging.error("Database connection not available for OCR.")
        raise HTTPException(status_code=500, detail="Database connection not available")
    user = _get_auth_user(request)

    try:
        file_content = await file.read()

        ocr_data = process_ocr(file_content, file.filename)
        ledger = LedgerEntry(
            filename=ocr_data["filename"],
            vendor=ocr_data.get("vendor"),
            invoice_date=ocr_data.get("invoice_date"),
            total_amount=ocr_data.get("total_amount"),
            items=ocr_data.get("items", []),
            hsn_codes=ocr_data.get("hsn_codes", []),
            gst_payable=ocr_data.get("gst_payable"),
            raw_text=ocr_data["raw_text"],
            cleaned_text=ocr_data["cleaned_text"],
            lines=ocr_data["lines"],
            status=ocr_data.get("status", "processed"),
        )

        ledger_doc = ledger.model_dump()
        ledger_doc["owner_email"] = user.get("email")
        ledger_doc["owner_id"] = str(user.get("_id"))
        ledger_doc["image_data"] = bson.Binary(file_content)

        result = db.ledger_entries.insert_one(ledger_doc)
        logging.info(f"OCR stored with id: {result.inserted_id}")

        return {
            "message": "OCR processed",
            "id": str(result.inserted_id),
            "ledger": {
                "id": str(result.inserted_id),
                **ledger.model_dump()
            }
        }
    except Exception as e:
        logging.error(f"An error occurred during OCR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")


@app.get("/ledger/latest")
def get_latest_ledger(request: Request):
    user = _get_auth_user(request)
    doc = db.ledger_entries.find_one({"owner_email": user.get("email")}, sort=[("_id", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="No ledger entries found")
    doc["id"] = str(doc["_id"])
    doc.pop("_id", None)
    doc.pop("image_data", None)
    return doc


@app.get("/ledger/latest/total")
def get_latest_ledger_total(request: Request):
    user = _get_auth_user(request)
    doc = db.ledger_entries.find_one({"owner_email": user.get("email")}, sort=[("_id", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="No ledger entries found")
    return {
        "id": str(doc["_id"]),
        "total_amount": doc.get("total_amount")
    }


@app.get("/ledger/list")
def list_ledgers(request: Request, limit: int = 20):
    user = _get_auth_user(request)
    limit = max(1, min(limit, 100))
    cursor = db.ledger_entries.find(
        {"owner_email": user.get("email")},
        sort=[("_id", -1)],
    ).limit(limit)
    items = []
    for doc in cursor:
        doc["id"] = str(doc["_id"])
        doc.pop("_id", None)
        doc.pop("image_data", None)
        items.append(doc)
    return {"items": items}


@app.get("/ledger/list/pdf")
def ledger_list_pdf(request: Request, limit: int = 200):
    user = _get_auth_user(request)
    limit = max(1, min(limit, 500))
    cursor = db.ledger_entries.find(
        {"owner_email": user.get("email")},
        sort=[("_id", -1)],
    ).limit(limit)
    def extract_bill_no(lines, text_blob):
        lines = lines or []
        for ln in lines:
            m = re.search(r"(?:bill|invoice)\s*(?:no\.?|number)?\s*[:#-]?\s*([A-Z0-9-]+)", ln, re.I)
            if m and m.group(1):
                return m.group(1).strip()
        for ln in lines:
            if re.search(r"\bno\.?\b|\bnumber\b", ln, re.I):
                t = re.search(r"\b([A-Z0-9-]{4,})\b", ln)
                if t and t.group(1):
                    return t.group(1).strip()
        m = re.search(r"(?:bill|invoice)\s*(?:no\.?|number|#|:)\s*([A-Z0-9-]+)", text_blob or "", re.I)
        return m.group(1).strip() if m and m.group(1) else ""

    def extract_client_name(lines, text_blob):
        lines = lines or []
        for ln in lines:
            m = re.search(r"\bclient\b\s*[:\-]\s*(.+)$", ln, re.I)
            if m and m.group(1):
                return m.group(1).strip()
        m = re.search(r"\bclient\b\s*[:\-]\s*(.+)$", text_blob or "", re.I)
        return m.group(1).strip() if m and m.group(1) else ""

    rows = []
    for doc in cursor:
        vendor = doc.get("vendor") or ""
        client_name = extract_client_name(doc.get("lines"), doc.get("cleaned_text"))
        vendor_display = vendor
        if client_name:
            if vendor_display:
                if client_name.lower() not in vendor_display.lower():
                    vendor_display = f"{vendor_display} ({client_name})"
            else:
                vendor_display = client_name
        rows.append({
            "bill_no": extract_bill_no(doc.get("lines"), doc.get("cleaned_text")),
            "vendor": vendor_display,
            "invoice_date": doc.get("invoice_date") or "",
            "gst_payable": doc.get("gst_payable"),
            "total_amount": doc.get("total_amount") or "",
        })

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "FINTAX Ledger Report", ln=1)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Generated: {get_ist_time().strftime('%d-%b-%Y %H:%M')}", ln=1)
    pdf.ln(2)

    col_widths = [12, 45, 70, 32, 35, 35]
    headers = ["Sr", "Bill No", "Vendor", "Invoice Date", "GST Payable", "Total Amount"]

    pdf.set_font("Helvetica", "B", 10)
    for i, title in enumerate(headers):
        pdf.cell(col_widths[i], 8, title, border=1)
    pdf.ln()

    def fmt_money(value):
        if value is None:
            return ""
        try:
            return f"{float(value):.2f}"
        except Exception:
            return str(value)

    def safe_text(value):
        text = str(value or "")
        # FPDF core fonts expect latin-1; replace unsupported chars to avoid crashes.
        return text.encode("latin-1", "replace").decode("latin-1")

    def truncate(value, length):
        text = safe_text(value)
        if len(text) <= length:
            return text
        return text[: max(0, length - 3)] + "..."

    pdf.set_font("Helvetica", "", 9)
    for idx, row in enumerate(rows, start=1):
        pdf.cell(col_widths[0], 7, str(idx), border=1)
        pdf.cell(col_widths[1], 7, truncate(row["bill_no"], 20), border=1)
        pdf.cell(col_widths[2], 7, truncate(row["vendor"], 40), border=1)
        pdf.cell(col_widths[3], 7, truncate(row["invoice_date"], 12), border=1)
        pdf.cell(col_widths[4], 7, fmt_money(row["gst_payable"]), border=1)
        pdf.cell(col_widths[5], 7, truncate(row["total_amount"], 14), border=1)
        pdf.ln()

    output = pdf.output(dest="S")
    if isinstance(output, (bytes, bytearray)):
        pdf_bytes = bytes(output)
    else:
        pdf_bytes = output.encode("latin1")

    filename = f"ledger-report-{get_ist_time().strftime('%Y%m%d')}.pdf"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@app.get("/ledger/{ledger_id}")
def get_ledger(request: Request, ledger_id: str):
    user = _get_auth_user(request)
    doc = db.ledger_entries.find_one({
        "_id": bson.ObjectId(ledger_id),
        "owner_email": user.get("email"),
    })
    if not doc:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    doc["id"] = str(doc["_id"])
    doc.pop("_id", None)
    doc.pop("image_data", None)
    return doc


@app.get("/ledger/{ledger_id}/image")
def get_ledger_image(request: Request, ledger_id: str):
    user = _get_auth_user(request)
    doc = db.ledger_entries.find_one({
        "_id": bson.ObjectId(ledger_id),
        "owner_email": user.get("email"),
    })
    if not doc or "image_data" not in doc:
        raise HTTPException(status_code=404, detail="Ledger image not found")
    return Response(content=doc["image_data"], media_type="image/png")

@app.get("/")
def read_root():
    return {"message": "FINTAX API is running"}


@app.post("/signup/")
def signup_user(payload: UserCreate):
    if db is None:
        logging.error("Database connection not available for signup.")
        raise HTTPException(status_code=500, detail="Database connection not available")

    name = payload.name.strip()
    email = payload.email.strip().lower()
    password = payload.password
    confirm = payload.confirm_password

    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if not email.endswith("@gmail.com"):
        raise HTTPException(status_code=400, detail="Email must be @gmail.com")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if password != confirm:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    existing = db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    user = User(name=name, email=email, password_hash=password_hash)
    try:
        result = db.users.insert_one(user.model_dump())
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Email already registered")

    return {"message": "Signup successful", "id": str(result.inserted_id)}


@app.post("/login/")
def login_user(payload: UserLogin):
    if db is None:
        logging.error("Database connection not available for login.")
        raise HTTPException(status_code=500, detail="Database connection not available")

    email = payload.email.strip().lower()
    password = payload.password

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    if not email.endswith("@gmail.com"):
        raise HTTPException(status_code=400, detail="Email must be @gmail.com")

    password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    user = db.users.find_one({"email": email, "password_hash": password_hash})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = secrets.token_urlsafe(32)
    db.users.update_one({"_id": user["_id"]}, {"$set": {"auth_token": token, "last_login": get_ist_time()}})
    return {"message": "Login successful", "email": email, "token": token}

if __name__ == "__main__":
    import uvicorn
    # This will run the app on http://127.0.0.1:8000 and reload on code changes.
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
