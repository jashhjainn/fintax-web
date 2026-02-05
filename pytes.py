import pytesseract as pyt
import cv2
from db import db
from datetime import datetime

img = cv2.imread("ocr_test2.jpeg")

pyt.pytesseract.tesseract_cmd ="C:\\Users\\Jash Jain\\AppData\\Local\\Programs\\Tesseract-OCR\\tesseract.exe"

text = pyt.image_to_string(img)

print(text)

if db is not None:
    try:
        # Save OCR result to MongoDB
        document = {
            "filename": "ocr_test2.jpeg",
            "text": text,
            "created_at": datetime.utcnow(),
            "status": "processed"
        }
        result = db.ocr_results.insert_one(document)
        print(f"Saved to MongoDB with ID: {result.inserted_id}")
    except Exception as e:
        print(f"Error saving to MongoDB: {e}")
else:
    print("Database connection not available, skipping save.")