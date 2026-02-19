from fastapi import FastAPI, UploadFile, File, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
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

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, description="Current password")
    new_password: str = Field(..., min_length=6, description="New password (minimum 6 characters)")
    confirm_new_password: str = Field(..., description="Confirmation of new password")

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
            # Create indexes for better performance and data integrity
            db.users.create_index("email", unique=True)
            logging.info("Ensured unique index on users.email")
            
            # Create compound index for bill number duplication checks
            db.ledger_entries.create_index([("owner_email", 1), ("bill_number", 1)])
            logging.info("Ensured compound index on ledger_entries (owner_email, bill_number)")
            
            # Create index for faster queries by owner_email
            db.ledger_entries.create_index("owner_email")
            logging.info("Ensured index on ledger_entries.owner_email")
            
            # Create index for invoice_date for financial year queries
            db.ledger_entries.create_index("invoice_date")
            logging.info("Ensured index on ledger_entries.invoice_date")
            
            # Create index for invoices collection as well
            db.invoices.create_index([("owner_email", 1), ("bill_number", 1)])
            logging.info("Ensured compound index on invoices (owner_email, bill_number)")
            
            db.invoices.create_index("owner_email")
            logging.info("Ensured index on invoices.owner_email")
            
        except Exception as e:
            logging.error(f"Could not create indexes: {e}")

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
        
        # Perform validation using OCR (same as /ocr/ endpoint)
        try:
            ocr_data = process_ocr(file_content, file.filename)
            validation_result = ocr_data.get("validation", {})
            
            # Check if validation failed due to missing bill number
            if not validation_result.get("is_valid", True):
                missing_fields = validation_result.get("missing_fields", [])
                if "bill number" in missing_fields:
                    logging.warning(f"Missing bill number detected in upload: {file.filename} for user {user.get('email')}")
                    return {
                        "message": "Missing field bill number",
                        "status": "validation_failed",
                        "missing_fields": missing_fields
                    }
        except Exception as ocr_error:
            logging.warning(f"OCR failed during upload validation: {ocr_error}")
            # If OCR fails completely, we can't validate, so allow upload but log the issue
            # Alternatively, you could reject the upload if OCR is critical
            pass
        
        # Check for duplicate bill number in ledger entries (same as OCR endpoint)
        # Extract bill number from filename or do basic OCR check
        bill_number = None
        
        # Try to extract bill number from filename first
        filename_lower = file.filename.lower()
        bill_patterns = [
            r'(?:bill|invoice)[\s_-]*no?[\s_:]*([A-Z0-9-]+)',
            r'([A-Z0-9]{4,}-[A-Z0-9]{4,})',
            r'([A-Z0-9]{8,})'
        ]
        
        for pattern in bill_patterns:
            match = re.search(pattern, filename_lower)
            if match:
                bill_number = match.group(1).upper()
                break
        
        # If no bill number found in filename, try to extract from the image using OCR
        if not bill_number:
            try:
                # Use the same OCR processing as the /ocr/ endpoint
                ocr_data = process_ocr(file_content, file.filename)
                bill_number = ocr_data.get("bill_number")
            except Exception as ocr_error:
                logging.warning(f"OCR failed during upload duplicate check: {ocr_error}")
                # Continue without bill number if OCR fails
        
        # If bill number found (from filename or OCR), check for duplicates
        if bill_number:
            # Check both ledger_entries and invoices collections for duplicates
            existing_ledger = db.ledger_entries.find_one({
                "owner_email": user.get("email"),
                "bill_number": bill_number
            })
            
            existing_invoice = db.invoices.find_one({
                "owner_email": user.get("email"),
                "bill_number": bill_number
            })
            
            if existing_ledger or existing_invoice:
                logging.warning(f"Duplicate bill number detected in upload: {bill_number} for user {user.get('email')}")
                existing_entry = existing_ledger or existing_invoice
                return {
                    "message": "Duplicate bill number",
                    "bill_number": bill_number,
                    "status": "duplicate_detected",
                    "existing_entry_id": str(existing_entry["_id"])
                }
        
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
        
        # Check if validation failed - if so, don't store in database
        if not ocr_data.get("validation", {}).get("is_valid", True):
            logging.warning(f"Validation failed for file {file.filename}: {ocr_data.get('validation', {}).get('missing_fields', [])}")
            return {
                "message": "Validation failed",
                "validation": ocr_data.get("validation"),
                "status": "validation_failed"
            }

        # Enhanced duplicate bill number check
        bill_number = ocr_data.get("bill_number")
        if bill_number:
            # Check both ledger_entries and invoices collections for duplicates
            existing_ledger = db.ledger_entries.find_one({
                "owner_email": user.get("email"),
                "bill_number": bill_number
            })
            
            existing_invoice = db.invoices.find_one({
                "owner_email": user.get("email"),
                "bill_number": bill_number
            })
            
            if existing_ledger or existing_invoice:
                logging.warning(f"Duplicate bill number detected: {bill_number} for user {user.get('email')}")
                existing_entry = existing_ledger or existing_invoice
                return {
                    "message": "Duplicate bill number",
                    "bill_number": bill_number,
                    "status": "duplicate_detected",
                    "existing_entry_id": str(existing_entry["_id"]),
                    "existing_filename": existing_entry.get("filename", "Unknown"),
                    "existing_date": existing_entry.get("invoice_date", existing_entry.get("upload_date", "Unknown"))
                }

        # Only proceed with database storage if validation passed and no duplicate
        ledger = LedgerEntry(
            filename=ocr_data["filename"],
            vendor=ocr_data.get("vendor"),
            bill_number=ocr_data.get("bill_number"),  # Use actual bill number from OCR
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

@app.get("/ledger/total-sales")
def get_total_sales(request: Request):
    """Calculate total sales as sum of all invoice amounts for the authenticated user"""
    user = _get_auth_user(request)
    
    try:
        # Try aggregation with $toDouble first (for newer MongoDB versions)
        try:
            pipeline = [
                {"$match": {"owner_email": user.get("email")}},
                {"$group": {
                    "_id": None,
                    "total_sales": {"$sum": {"$toDouble": "$total_amount"}},
                    "count": {"$sum": 1}
                }}
            ]
            result = list(db.ledger_entries.aggregate(pipeline))
        except Exception:
            # Fallback for older MongoDB versions - calculate in Python
            cursor = db.ledger_entries.find({"owner_email": user.get("email")})
            total_sales = 0.0
            invoice_count = 0
            
            for doc in cursor:
                total_amount = doc.get("total_amount")
                if total_amount is not None:
                    try:
                        # Handle both string and numeric values
                        if isinstance(total_amount, str):
                            # Remove currency symbols and commas, then convert to float
                            clean_amount = total_amount.replace('₹', '').replace(',', '').strip()
                            numeric_value = float(clean_amount)
                        else:
                            numeric_value = float(total_amount)
                        total_sales += numeric_value
                        invoice_count += 1
                    except (ValueError, TypeError):
                        # Skip invalid values
                        continue
            
            result = [{"total_sales": total_sales, "count": invoice_count}]
        
        if not result or result[0]["count"] == 0:
            return {
                "total_sales": 0.0,
                "invoice_count": 0,
                "message": "No invoices found"
            }
        
        total_sales = result[0]["total_sales"]
        invoice_count = result[0]["count"]
        
        return {
            "total_sales": total_sales,
            "invoice_count": invoice_count,
            "message": f"Total sales from {invoice_count} invoices"
        }
    except Exception as e:
        print(f"Error in get_total_sales: {e}")
        raise HTTPException(status_code=500, detail=f"Error calculating total sales: {str(e)}")


@app.get("/ledger/total-gst-payable")
def get_total_gst_payable(request: Request):
    """Calculate total GST payable as sum of all gst_payable amounts for the authenticated user"""
    user = _get_auth_user(request)
    
    try:
        # Try aggregation with $toDouble first (for newer MongoDB versions)
        try:
            pipeline = [
                {"$match": {"owner_email": user.get("email")}},
                {"$group": {
                    "_id": None,
                    "total_gst": {"$sum": {"$toDouble": "$gst_payable"}},
                    "count": {"$sum": 1}
                }}
            ]
            result = list(db.ledger_entries.aggregate(pipeline))
        except Exception:
            # Fallback for older MongoDB versions - calculate in Python
            cursor = db.ledger_entries.find({"owner_email": user.get("email")})
            total_gst = 0.0
            invoice_count = 0
            
            for doc in cursor:
                gst_payable = doc.get("gst_payable")
                if gst_payable is not None:
                    try:
                        # Handle both string and numeric values
                        if isinstance(gst_payable, str):
                            # Remove currency symbols and commas, then convert to float
                            clean_amount = gst_payable.replace('₹', '').replace(',', '').strip()
                            numeric_value = float(clean_amount)
                        else:
                            numeric_value = float(gst_payable)
                        total_gst += numeric_value
                        invoice_count += 1
                    except (ValueError, TypeError):
                        # Skip invalid values
                        continue
            
            result = [{"total_gst": total_gst, "count": invoice_count}]
        
        if not result or result[0]["count"] == 0:
            return {
                "total_gst_payable": 0.0,
                "invoice_count": 0,
                "message": "No GST data found"
            }
        
        total_gst = result[0]["total_gst"]
        invoice_count = result[0]["count"]
        
        return {
            "total_gst_payable": total_gst,
            "invoice_count": invoice_count,
            "message": f"Total GST payable from {invoice_count} invoices"
        }
    except Exception as e:
        print(f"Error in get_total_gst_payable: {e}")
        raise HTTPException(status_code=500, detail=f"Error calculating total GST payable: {str(e)}")


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


@app.get("/ledger/month/{month}")
def get_ledger_by_month(request: Request, month: str):
    """Get ledger entries filtered by month based on invoice_date"""
    user = _get_auth_user(request)
    
    # Handle "all" case
    if month.lower() == "all":
        cursor = db.ledger_entries.find(
            {"owner_email": user.get("email")},
            sort=[("invoice_date", 1), ("_id", 1)]
        )
    else:
        # Validate month parameter (01-12)
        try:
            month_num = int(month)
            if month_num < 1 or month_num > 12:
                raise ValueError("Month must be between 01 and 12")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid month format. Use 'all' or a 2-digit month like '01' for January")
        
        # Build list of valid dates for the specified month
        valid_dates = []
        
        # Get all possible dates for the specified month across different years
        # We'll check a reasonable range of years (current year ± 5 years)
        current_year = datetime.now().year
        year_range = range(current_year - 5, current_year + 6)
        
        for year in year_range:
            for day in range(1, 32):  # 1 to 31
                try:
                    # Create date and check if it's valid for the specified month
                    test_date = datetime(year, month_num, day)
                    date_str = test_date.strftime("%d/%m/%Y")
                    valid_dates.append(date_str)
                except ValueError:
                    # Invalid date (e.g., Feb 30), skip
                    continue
        
        # Query for entries with invoice_date in our valid dates list
        cursor = db.ledger_entries.find({
            "owner_email": user.get("email"),
            "invoice_date": {"$in": valid_dates}
        }, sort=[("invoice_date", 1), ("_id", 1)])
    
    items = []
    for doc in cursor:
        doc["id"] = str(doc["_id"])
        doc.pop("_id", None)
        doc.pop("image_data", None)
        items.append(doc)
    
    return {"items": items, "month": month if month.lower() != "all" else "All Months"}


@app.get("/ledger/financial-year/{fy}/month/{month}")
def get_ledger_by_financial_year_and_month(request: Request, fy: str, month: str):
    """Get ledger entries filtered by both financial year and month based on invoice_date"""
    user = _get_auth_user(request)
    
    # Handle "all" cases
    if fy.lower() == "all" and month.lower() == "all":
        cursor = db.ledger_entries.find(
            {"owner_email": user.get("email")},
            sort=[("invoice_date", 1), ("_id", 1)]
        )
    elif fy.lower() == "all":
        # Filter by month only (reuse month filtering logic)
        try:
            month_num = int(month)
            if month_num < 1 or month_num > 12:
                raise ValueError("Month must be between 01 and 12")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid month format. Use 'all' or a 2-digit month like '01' for January")
        
        valid_dates = []
        current_year = datetime.now().year
        year_range = range(current_year - 5, current_year + 6)
        
        for year in year_range:
            for day in range(1, 32):
                try:
                    test_date = datetime(year, month_num, day)
                    date_str = test_date.strftime("%d/%m/%Y")
                    valid_dates.append(date_str)
                except ValueError:
                    continue
        
        cursor = db.ledger_entries.find({
            "owner_email": user.get("email"),
            "invoice_date": {"$in": valid_dates}
        }, sort=[("invoice_date", 1), ("_id", 1)])
        
    elif month.lower() == "all":
        # Filter by financial year only (reuse financial year filtering logic)
        try:
            end_year = int(fy)
            start_year = end_year - 1
            
            valid_dates = []
            
            # Months from April to December of start_year
            for month_val in range(4, 13):
                for day in range(1, 32):
                    try:
                        test_date = datetime(start_year, month_val, day)
                        date_str = test_date.strftime("%d/%m/%Y")
                        valid_dates.append(date_str)
                    except ValueError:
                        continue
            
            # Months from January to March of end_year
            for month_val in range(1, 4):
                for day in range(1, 32):
                    try:
                        test_date = datetime(end_year, month_val, day)
                        date_str = test_date.strftime("%d/%m/%Y")
                        valid_dates.append(date_str)
                    except ValueError:
                        continue
            
            cursor = db.ledger_entries.find({
                "owner_email": user.get("email"),
                "invoice_date": {"$in": valid_dates}
            }, sort=[("invoice_date", 1), ("_id", 1)])
            
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid financial year format. Use 'all' or a 4-digit year like '2026'")
    else:
        # Filter by both financial year and month
        try:
            end_year = int(fy)
            start_year = end_year - 1
            month_num = int(month)
            if month_num < 1 or month_num > 12:
                raise ValueError("Month must be between 01 and 12")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid format. Use 'all' or valid 4-digit year and 2-digit month")
        
        valid_dates = []
        
        # For financial year filtering, we need to consider:
        # - If month is April-December: use start_year
        # - If month is January-March: use end_year
        
        if month_num >= 4:  # April to December: use start_year
            target_year = start_year
        else:  # January to March: use end_year
            target_year = end_year
        
        # Generate valid dates for the specific month and year
        for day in range(1, 32):
            try:
                test_date = datetime(target_year, month_num, day)
                date_str = test_date.strftime("%d/%m/%Y")
                valid_dates.append(date_str)
            except ValueError:
                continue
        
        cursor = db.ledger_entries.find({
            "owner_email": user.get("email"),
            "invoice_date": {"$in": valid_dates}
        }, sort=[("invoice_date", 1), ("_id", 1)])
    
    items = []
    for doc in cursor:
        doc["id"] = str(doc["_id"])
        doc.pop("_id", None)
        doc.pop("image_data", None)
        items.append(doc)
    
    return {
        "items": items, 
        "financial_year": fy if fy.lower() != "all" else "All Years",
        "month": month if month.lower() != "all" else "All Months"
    }


@app.get("/ledger/available-months")
def get_available_months(request: Request):
    """Get list of months that have actual ledger entries"""
    user = _get_auth_user(request)
    
    # Get all ledger entries for the user
    cursor = db.ledger_entries.find(
        {"owner_email": user.get("email")},
        {"invoice_date": 1}
    )
    
    # Extract unique months from invoice dates
    months = set()
    
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
                # Try to extract month from string patterns
                month_match = re.search(r'(\d{1,2})[-/]', date_str)
                if month_match:
                    month = int(month_match.group(1))
                    if 1 <= month <= 12:
                        parsed_date = datetime(2000, month, 1)  # Use dummy year
                else:
                    continue
            
            months.add(parsed_date.month)
            
        except Exception as e:
            logging.warning(f"Failed to parse date '{invoice_date}' for month calculation: {e}")
            continue
    
    # Convert to sorted list (January first)
    available_months = sorted(list(months))
    
    # Format for display
    month_names = ['January', 'February', 'March', 'April', 'May', 'June', 
                   'July', 'August', 'September', 'October', 'November', 'December']
    
    formatted_months = []
    for month in available_months:
        formatted_months.append({
            "value": f"{month:02d}",
            "label": month_names[month - 1]
        })
    
    return {
        "available_months": formatted_months,
        "total_months": len(formatted_months)
    }


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
def ledger_list_pdf(request: Request, limit: int = 200, financial_year: str = "all", month: str = "all"):
    user = _get_auth_user(request)
    limit = max(1, min(limit, 500))
    
    # Build query based on filters
    query = {"owner_email": user.get("email")}
    
    # Apply financial year filter if specified
    if financial_year != "all":
        try:
            end_year = int(financial_year)
            start_year = end_year - 1
            
            # Build list of valid dates for the financial year
            valid_dates = []
            
            # Months from April to December of start_year
            for month_val in range(4, 13):  # April (4) to December (12)
                for day in range(1, 32):  # 1 to 31
                    try:
                        # Create date and check if it's valid
                        test_date = datetime(start_year, month_val, day)
                        date_str = test_date.strftime("%d/%m/%Y")
                        valid_dates.append(date_str)
                    except ValueError:
                        # Invalid date (e.g., Feb 30), skip
                        continue
            
            # Months from January to March of end_year
            for month_val in range(1, 4):  # January (1) to March (3)
                for day in range(1, 32):  # 1 to 31
                    try:
                        # Create date and check if it's valid
                        test_date = datetime(end_year, month_val, day)
                        date_str = test_date.strftime("%d/%m/%Y")
                        valid_dates.append(date_str)
                    except ValueError:
                        # Invalid date, skip
                        continue
            
            query["invoice_date"] = {"$in": valid_dates}
            
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid financial year format. Use 'all' or a 4-digit year like '2026'")
    
    # Apply month filter if specified
    if month != "all":
        try:
            month_num = int(month)
            if month_num < 1 or month_num > 12:
                raise ValueError("Month must be between 01 and 12")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid month format. Use 'all' or a 2-digit month like '01' for January")
        
        # Build list of valid dates for the specified month
        valid_dates = []
        
        # Get all possible dates for the specified month across different years
        # We'll check a reasonable range of years (current year ± 5 years)
        current_year = datetime.now().year
        year_range = range(current_year - 5, current_year + 6)
        
        for year in year_range:
            for day in range(1, 32):  # 1 to 31
                try:
                    # Create date and check if it's valid for the specified month
                    test_date = datetime(year, month_num, day)
                    date_str = test_date.strftime("%d/%m/%Y")
                    valid_dates.append(date_str)
                except ValueError:
                    # Invalid date (e.g., Feb 30), skip
                    continue
        
        # If financial year filter is also applied, intersect the date lists
        if "invoice_date" in query:
            existing_dates = query["invoice_date"]["$in"]
            query["invoice_date"]["$in"] = list(set(existing_dates) & set(valid_dates))
        else:
            query["invoice_date"] = {"$in": valid_dates}
    
    cursor = db.ledger_entries.find(query, sort=[("_id", -1)]).limit(limit)
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
    
    # Add filter information to the PDF
    pdf.set_font("Helvetica", "", 10)
    filter_text = "Filters: "
    if financial_year != "all" and month != "all":
        # Both filters applied
        end_year = int(financial_year)
        start_year = end_year - 1
        month_names = ['January', 'February', 'March', 'April', 'May', 'June', 
                       'July', 'August', 'September', 'October', 'November', 'December']
        month_name = month_names[int(month) - 1]
        filter_text += f"{month_name} {start_year}-{end_year}"
    elif financial_year != "all":
        # Only financial year filter
        end_year = int(financial_year)
        start_year = end_year - 1
        filter_text += f"Financial Year {start_year}-{end_year}"
    elif month != "all":
        # Only month filter
        month_names = ['January', 'February', 'March', 'April', 'May', 'June', 
                       'July', 'August', 'September', 'October', 'November', 'December']
        month_name = month_names[int(month) - 1]
        filter_text += f"Month: {month_name}"
    else:
        # No filters
        filter_text += "All entries"
    
    pdf.cell(0, 6, filter_text, ln=1)
    pdf.cell(0, 6, f"Generated: {get_ist_time().strftime('%d-%b-%Y %H:%M')}", ln=1)
    pdf.ln(2)

    col_widths = [12, 45, 32, 70, 35, 35]
    headers = ["Sr", "Bill No", "Invoice Date", "Vendor", "GST Payable", "Total Amount"]

    pdf.set_font("Helvetica", "B", 10)
    for i, title in enumerate(headers):
        pdf.cell(col_widths[i], 8, title, border=1)
    pdf.ln()

    def fmt_money(value):
        if value is None:
            return ""
        try:
            # Handle string values that might contain currency symbols
            if isinstance(value, str):
                # Remove currency symbols and commas, then convert to float
                clean_value = str(value).replace('₹', '').replace('Rs', '').replace(',', '').replace('$', '').strip()
                numeric_value = float(clean_value)
                return f"Rs {numeric_value:.2f}"
            else:
                # Handle numeric values
                return f"Rs {float(value):.2f}"
        except (ValueError, TypeError):
            # If conversion fails, return the original value as string
            return str(value)

    def safe_text(value):
        text = str(value or "")
        # Replace rupee symbol with 'Rs' for PDF compatibility
        text = text.replace('₹', 'Rs')
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
        pdf.cell(col_widths[2], 7, truncate(row["invoice_date"], 12), border=1)
        pdf.cell(col_widths[3], 7, truncate(row["vendor"], 40), border=1)
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


class LedgerPdfRequest(BaseModel):
    items: list
    selected_fy: str = "all"
    selected_month: str = "all"

@app.post("/ledger/pdf")
def ledger_pdf_from_items(request: Request, pdf_request: LedgerPdfRequest):
    """Generate PDF from specific ledger items (for current filtered view)"""
    user = _get_auth_user(request)
    
    # Get filter parameters from request body
    selected_fy = pdf_request.selected_fy
    selected_month = pdf_request.selected_month
    
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
    for doc in pdf_request.items:
        # Validate that the item belongs to the current user
        if doc.get("owner_email") != user.get("email"):
            continue
            
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
    
    # Add filter information to the PDF
    pdf.set_font("Helvetica", "", 10)
    filter_text = "Filters: "
    if selected_fy != "all" and selected_month != "all":
        # Both filters applied
        end_year = int(selected_fy)
        start_year = end_year - 1
        month_names = ['January', 'February', 'March', 'April', 'May', 'June', 
                       'July', 'August', 'September', 'October', 'November', 'December']
        month_name = month_names[int(selected_month) - 1]
        filter_text += f"{month_name} {start_year}-{end_year}"
    elif selected_fy != "all":
        # Only financial year filter
        end_year = int(selected_fy)
        start_year = end_year - 1
        filter_text += f"Financial Year {start_year}-{end_year}"
    elif selected_month != "all":
        # Only month filter
        month_names = ['January', 'February', 'March', 'April', 'May', 'June', 
                       'July', 'August', 'September', 'October', 'November', 'December']
        month_name = month_names[int(selected_month) - 1]
        filter_text += f"Month: {month_name}"
    else:
        # No filters
        filter_text += "All entries"
    
    pdf.cell(0, 6, filter_text, ln=1)
    pdf.cell(0, 6, f"Generated: {get_ist_time().strftime('%d-%b-%Y %H:%M')}", ln=1)
    pdf.ln(2)

    col_widths = [12, 45, 32, 70, 35, 35]
    headers = ["Sr", "Bill No", "Invoice Date", "Vendor", "GST Payable", "Total Amount"]

    pdf.set_font("Helvetica", "B", 10)
    for i, title in enumerate(headers):
        pdf.cell(col_widths[i], 8, title, border=1)
    pdf.ln()

    def fmt_money(value):
        if value is None:
            return ""
        try:
            # Handle string values that might contain currency symbols
            if isinstance(value, str):
                # Remove currency symbols and commas, then convert to float
                clean_value = str(value).replace('₹', '').replace('Rs', '').replace(',', '').replace('$', '').strip()
                numeric_value = float(clean_value)
                return f"Rs {numeric_value:.2f}"
            else:
                # Handle numeric values
                return f"Rs {float(value):.2f}"
        except (ValueError, TypeError):
            # If conversion fails, return the original value as string
            return str(value)

    def truncate(value, length):
        text = safe_text(value)
        if len(text) <= length:
            return text
        return text[: max(0, length - 3)] + "..."

    pdf.set_font("Helvetica", "", 9)
    for idx, row in enumerate(rows, start=1):
        pdf.cell(col_widths[0], 7, str(idx), border=1)
        pdf.cell(col_widths[1], 7, truncate(row["bill_no"], 20), border=1)
        pdf.cell(col_widths[2], 7, truncate(row["invoice_date"], 12), border=1)
        pdf.cell(col_widths[3], 7, truncate(row["vendor"], 40), border=1)
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

@app.delete("/ledger/{ledger_id}")
def delete_ledger_entry(request: Request, ledger_id: str):
    user = _get_auth_user(request)
    
    # First, check if the ledger entry exists and belongs to the user
    doc = db.ledger_entries.find_one({
        "_id": bson.ObjectId(ledger_id),
        "owner_email": user.get("email"),
    })
    
    if not doc:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    
    try:
        # Delete the ledger entry
        result = db.ledger_entries.delete_one({
            "_id": bson.ObjectId(ledger_id),
            "owner_email": user.get("email"),
        })
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        
        # Also delete from invoices collection if it exists there
        db.invoices.delete_one({
            "owner_email": user.get("email"),
            "bill_number": doc.get("bill_number")
        })
        
        logging.info(f"Ledger entry deleted successfully: {ledger_id} for user {user.get('email')}")
        return {"message": "Ledger entry deleted successfully", "id": ledger_id}
        
    except Exception as e:
        logging.error(f"Error deleting ledger entry {ledger_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting ledger entry: {str(e)}")

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
    return {"message": "Login successful", "email": email, "name": user.get("name"), "token": token}


@app.get("/profile/")
def get_user_profile(request: Request):
    """Get the current user's profile information"""
    user = _get_auth_user(request)
    
    return {
        "name": user.get("name"),
        "email": user.get("email"),
        "last_login": user.get("last_login")
    }


class UpdateProfileRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Full name")

@app.post("/change-password/")
def change_password(request: Request, payload: ChangePasswordRequest):
    """Change user password - requires current password verification"""
    if db is None:
        logging.error("Database connection not available for password change.")
        raise HTTPException(status_code=500, detail="Database connection not available")

    user = _get_auth_user(request)
    current_password = payload.current_password
    new_password = payload.new_password
    confirm_new_password = payload.confirm_new_password

    # Validate new password
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")
    
    if new_password != confirm_new_password:
        raise HTTPException(status_code=400, detail="New password and confirmation do not match")

    # Verify current password
    current_password_hash = hashlib.sha256(current_password.encode("utf-8")).hexdigest()
    if current_password_hash != user.get("password_hash"):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Check if new password is different from current password
    new_password_hash = hashlib.sha256(new_password.encode("utf-8")).hexdigest()
    if new_password_hash == user.get("password_hash"):
        raise HTTPException(status_code=400, detail="New password must be different from current password")

    # Update password
    db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "password_hash": new_password_hash,
            "updated_at": get_ist_time()
        }}
    )

    logging.info(f"Password changed successfully for user: {user.get('email')}")
    return {"message": "Password changed successfully"}

@app.post("/update-profile/")
def update_profile(request: Request, payload: UpdateProfileRequest):
    """Update user profile information (name only)"""
    if db is None:
        logging.error("Database connection not available for profile update.")
        raise HTTPException(status_code=500, detail="Database connection not available")

    user = _get_auth_user(request)
    new_name = payload.name.strip()

    # Validate name
    if not new_name:
        raise HTTPException(status_code=400, detail="Name is required")
    
    # Check if name is different from current name
    if new_name == user.get("name"):
        raise HTTPException(status_code=400, detail="New name must be different from current name")

    # Update profile
    db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "name": new_name,
            "updated_at": get_ist_time()
        }}
    )

    logging.info(f"Profile updated successfully for user: {user.get('email')}")
    return {"message": "Profile updated successfully", "name": new_name}

# Let me create a proper password change model and route

if __name__ == "__main__":
    import uvicorn
    # This will run the app on http://127.0.0.1:8000 and reload on code changes.
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)