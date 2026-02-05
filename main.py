from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from db import db
from models import Invoice
from datetime import datetime
import bson
import logging

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

@app.get("/")
def read_root():
    return {"message": "FINTAX API is running"}

if __name__ == "__main__":
    import uvicorn
    # This will run the app on http://127.0.0.1:8000 and reload on code changes.
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
