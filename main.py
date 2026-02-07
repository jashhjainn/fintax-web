from fastapi import FastAPI, UploadFile, File, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from db import db
from models import Invoice, LedgerEntry, UserCreate, UserLogin, User
from pytes import process_ocr
from datetime import datetime
import bson
import logging
import hashlib
from pymongo.errors import DuplicateKeyError

# Configure logging
logging.basicConfig(level=logging.INFO, filename='app.log', filemode='a', 
                    format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI()

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
async def upload_invoice(file: UploadFile = File(...)):
    if db is None:
        logging.error("Database connection not available for upload.")
        raise HTTPException(status_code=500, detail="Database connection not available")

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
async def ocr_invoice(file: UploadFile = File(...)):
    if db is None:
        logging.error("Database connection not available for OCR.")
        raise HTTPException(status_code=500, detail="Database connection not available")

    try:
        file_content = await file.read()

        ocr_data = process_ocr(file_content, file.filename)
        ledger = LedgerEntry(
            filename=ocr_data["filename"],
            vendor=ocr_data.get("vendor"),
            invoice_date=ocr_data.get("invoice_date"),
            total_amount=ocr_data.get("total_amount"),
            raw_text=ocr_data["raw_text"],
            cleaned_text=ocr_data["cleaned_text"],
            lines=ocr_data["lines"],
            status=ocr_data.get("status", "processed"),
        )

        ledger_doc = ledger.model_dump()
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
def get_latest_ledger():
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = db.ledger_entries.find_one(sort=[("_id", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="No ledger entries found")
    doc["id"] = str(doc["_id"])
    doc.pop("_id", None)
    doc.pop("image_data", None)
    return doc


@app.get("/ledger/list")
def list_ledgers(limit: int = 20):
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    limit = max(1, min(limit, 100))
    cursor = db.ledger_entries.find(sort=[("_id", -1)]).limit(limit)
    items = []
    for doc in cursor:
        doc["id"] = str(doc["_id"])
        doc.pop("_id", None)
        doc.pop("image_data", None)
        items.append(doc)
    return {"items": items}


@app.get("/ledger/{ledger_id}")
def get_ledger(ledger_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = db.ledger_entries.find_one({"_id": bson.ObjectId(ledger_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    doc["id"] = str(doc["_id"])
    doc.pop("_id", None)
    doc.pop("image_data", None)
    return doc


@app.get("/ledger/{ledger_id}/image")
def get_ledger_image(ledger_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = db.ledger_entries.find_one({"_id": bson.ObjectId(ledger_id)})
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

    return {"message": "Login successful", "email": email}

if __name__ == "__main__":
    import uvicorn
    # This will run the app on http://127.0.0.1:8000 and reload on code changes.
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
