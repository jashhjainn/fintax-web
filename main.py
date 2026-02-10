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


@app.get("/ledger/organized")
def get_organized_ledger(request: Request):
    """Get ledger entries organized by year and month based on invoice_date"""
    user = _get_auth_user(request)
    
    # Fetch all ledger entries for the user
    cursor = db.ledger_entries.find(
        {"owner_email": user.get("email")},
        sort=[("invoice_date", 1), ("_id", 1)]  # Sort by date ascending, then by ID
    )
    
    organized_data = {}
    total_entries = 0
    
    for doc in cursor:
        total_entries += 1
        doc["id"] = str(doc["_id"])
        doc.pop("_id", None)
        doc.pop("image_data", None)
        
        # Parse invoice date
        invoice_date = doc.get("invoice_date")
        if not invoice_date:
            # If no invoice date, skip this entry or put in "Unknown" category
            continue
            
        try:
            # Try to parse the date - handle various formats
            parsed_date = None
            date_str = str(invoice_date).strip()
            
            # Common date formats
            date_formats = [
                "%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y",
                "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
                "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"
            ]
            
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue
            
            if not parsed_date:
                # Try to extract year and month from string patterns
                year_month_match = re.search(r'(\d{4})[-/](\d{1,2})', date_str)
                if year_month_match:
                    year = int(year_month_match.group(1))
                    month = int(year_month_match.group(2))
                    parsed_date = datetime(year, month, 1)
                else:
                    continue
            
            year = parsed_date.year
            month = parsed_date.month
            month_name = parsed_date.strftime("%B")
            
            # Initialize year if not exists
            if year not in organized_data:
                organized_data[year] = {
                    "year": year,
                    "total_entries": 0,
                    "total_amount": 0.0,
                    "months": {}
                }
            
            # Initialize month if not exists
            if month not in organized_data[year]["months"]:
                organized_data[year]["months"][month] = {
                    "month": month,
                    "month_name": month_name,
                    "entries": [],
                    "month_total": 0.0
                }
            
            # Add entry to month
            organized_data[year]["months"][month]["entries"].append(doc)
            organized_data[year]["months"][month]["month_total"] += float(doc.get("total_amount") or 0)
            
            # Update totals
            organized_data[year]["total_entries"] += 1
            organized_data[year]["total_amount"] += float(doc.get("total_amount") or 0)
            
        except Exception as e:
            logging.warning(f"Failed to parse date '{invoice_date}' for entry {doc.get('id')}: {e}")
            continue
    
    # Convert to sorted list format
    result = {
        "total_entries": total_entries,
        "years": []
    }
    
    # Sort years in descending order (newest first)
    for year in sorted(organized_data.keys(), reverse=True):
        year_data = organized_data[year]
        
        # Sort months in descending order (newest first)
        sorted_months = []
        for month in sorted(year_data["months"].keys(), reverse=True):
            sorted_months.append(year_data["months"][month])
        
        year_data["months"] = sorted_months
        result["years"].append(year_data)
    
    return result


@app.get("/ledger/financial-year/{fy}")
def get_ledger_by_financial_year(request: Request, fy: str):
    """Get ledger entries filtered by financial year (April to March)"""
    user = _get_auth_user(request)
    
    # Handle "all" case
    if fy.lower() == "all":
        cursor = db.ledger_entries.find(
            {"owner_email": user.get("email")},
            sort=[("invoice_date", 1), ("_id", 1)]
        )
    else:
        # Parse financial year (e.g., "2026" means FY 2025-2026)
        try:
            end_year = int(fy)
            start_year = end_year - 1
            
            # Financial year: April of start_year to March of end_year
            # We need to filter dates between 01/04/start_year and 31/03/end_year
            
            # Build date range filters
            # Since dates are stored as strings in format "DD/MM/YYYY", we need to handle this carefully
            # We'll create a list of valid date patterns for the financial year
            
            valid_dates = []
            
            # Months from April to December of start_year
            for month in range(4, 13):  # April (4) to December (12)
                for day in range(1, 32):  # 1 to 31
                    try:
                        # Create date and check if it's valid
                        test_date = datetime(start_year, month, day)
                        date_str = test_date.strftime("%d/%m/%Y")
                        valid_dates.append(date_str)
                    except ValueError:
                        # Invalid date (e.g., Feb 30), skip
                        continue
            
            # Months from January to March of end_year
            for month in range(1, 4):  # January (1) to March (3)
                for day in range(1, 32):  # 1 to 31
                    try:
                        # Create date and check if it's valid
                        test_date = datetime(end_year, month, day)
                        date_str = test_date.strftime("%d/%m/%Y")
                        valid_dates.append(date_str)
                    except ValueError:
                        # Invalid date, skip
                        continue
            
            # Query for entries with invoice_date in our valid dates list
            cursor = db.ledger_entries.find({
                "owner_email": user.get("email"),
                "invoice_date": {"$in": valid_dates}
            }, sort=[("invoice_date", 1), ("_id", 1)])
            
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid financial year format. Use 'all' or a 4-digit year like '2026'")
    
    items = []
    for doc in cursor:
        doc["id"] = str(doc["_id"])
        doc.pop("_id", None)
        doc.pop("image_data", None)
        items.append(doc)
    
    return {"items": items, "financial_year": fy if fy.lower() != "all" else "All Years"}


@app.get("/ledger/available-financial-years")
def get_available_financial_years(request: Request):
    """Get list of financial years that have actual ledger entries"""
    user = _get_auth_user(request)
    
    # Get all ledger entries for the user
    cursor = db.ledger_entries.find(
        {"owner_email": user.get("email")},
        {"invoice_date": 1}
    )
    
    # Extract unique financial years from invoice dates
    financial_years = set()
    
    for doc in cursor:
        invoice_date = doc.get("invoice_date")
        if not invoice_date:
            continue
            
        try:
            # Try to parse the date - handle various formats
            parsed_date = None
            date_str = str(invoice_date).strip()
            
            # Common date formats
            date_formats = [
                "%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y",
                "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
                "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"
            ]
            
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue
            
            if not parsed_date:
                # Try to extract year and month from string patterns
                year_month_match = re.search(r'(\d{4})[-/](\d{1,2})', date_str)
                if year_month_match:
                    year = int(year_month_match.group(1))
                    month = int(year_month_match.group(2))
                    parsed_date = datetime(year, month, 1)
                else:
                    continue
            
            # Determine financial year
            # Financial year: April (4) to March (3) of next year
            if parsed_date.month >= 4:  # April to December
                financial_year_end = parsed_date.year
            else:  # January to March
                financial_year_end = parsed_date.year
            
            financial_years.add(financial_year_end)
            
        except Exception as e:
            logging.warning(f"Failed to parse date '{invoice_date}' for financial year calculation: {e}")
            continue
    
    # Convert to sorted list (newest first)
    available_years = sorted(list(financial_years), reverse=True)
    
    # Format for display
    formatted_years = []
    for year in available_years:
        start_year = year - 1
        formatted_years.append({
            "value": str(year),
            "label": f"{start_year}-{year}"
        })
    
    return {
        "available_years": formatted_years,
        "total_years": len(formatted_years)
    }


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
        print("Duplicate Email: ", email)
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
