from datetime import datetime
from typing import Any

import pytz
from pydantic import BaseModel, ConfigDict, Field


def get_ist_time():
    return datetime.now(pytz.timezone("Asia/Kolkata"))


class Invoice(BaseModel):
    filename: str
    content_type: str
    upload_date: datetime = Field(default_factory=get_ist_time)
    status: str = "uploaded"

    # We don't include image_data here for basic validation as it's handled separately
    # or we can type it as Any/bytes if we want to include it in the model.
    # For MongoDB storage, we convert this model to a dict and add the binary data.

    model_config = ConfigDict(arbitrary_types_allowed=True)


class LedgerEntry(BaseModel):
    filename: str
    vendor: str | None = None
    bill_number: str | None = None
    invoice_date: str | None = None
    total_amount: str | None = None
    items: list[dict] = Field(default_factory=list)
    hsn_codes: list[str] = Field(default_factory=list)
    gst_payable: float | None = None
    gstin: str | None = None
    raw_text: str
    cleaned_text: str
    lines: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=get_ist_time)
    status: str = "processed"

    # Srno, bill no, Particulars, gst amt, total amt

    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    def extract_gstin_from_text(self) -> str | None:
        """
        Extract GSTIN from the cleaned_text field using regex patterns.
        Returns the first valid GSTIN found or None if not found.
        """
        import re
        
        if not self.cleaned_text:
            return None
        
        # GSTIN patterns
        gstin_patterns = [
            # Standard GSTIN format: 2 digits + state code + PAN + 'Z' + check digit
            r'\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z0-9][A-Z\d])\b',
            
            # Alternative patterns with spaces, dashes, or other separators
            r'\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[-\s]*[Z0-9][A-Z\d])\b',
            r'\b(\d{2}[-\s]*[A-Z]{5}[-\s]*\d{4}[-\s]*[A-Z][-\s]*\d[-\s]*[Z0-9][-\s]*[A-Z\d])\b',
            
            # Common labels for GSTIN
            r'(?:gstin|gst\s*id|tax\s*id|registration\s*no)\s*[:\-]\s*(\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z0-9][A-Z\d])',
            r'(?:gstin|gst\s*id|tax\s*id|registration\s*no)\s*[:\-]\s*(\d{2}[-\s]*[A-Z]{5}[-\s]*\d{4}[-\s]*[A-Z][-\s]*\d[-\s]*[Z0-9][-\s]*[A-Z\d])',
            
            # GSTIN with "GSTIN No:" or similar labels
            r'(?:gstin\s*(?:no\.?|number)?|gst\s*registration)\s*[:\-]\s*(\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z0-9][A-Z\d])',
            
            # Look for GSTIN in various contexts
            r'\b(27[A-Z]{14}[Z0-9][A-Z\d])\b',  # More specific pattern for 15-character GSTIN
            r'\b(2[0-9][A-Z]{5}\d{4}[A-Z]\d[Z0-9][A-Z\d])\b',  # Any state code (20-29)
        ]
        
        # Search for GSTIN patterns in cleaned text
        for pattern in gstin_patterns:
            match = re.search(pattern, self.cleaned_text, re.IGNORECASE)
            if match:
                gstin = match.group(1).strip()
                # Clean up the GSTIN (remove spaces, dashes)
                gstin = re.sub(r'[-\s]', '', gstin)
                # Validate GSTIN format (15 characters)
                if len(gstin) == 15 and gstin[:2].isdigit() and gstin[2:7].isalpha() and gstin[7:11].isdigit() and gstin[11].isalpha() and gstin[12].isdigit() and gstin[13] in 'Z0123456789' and gstin[14].isalnum():
                    return gstin
        
        return None


class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    confirm_password: str

    model_config = ConfigDict(arbitrary_types_allowed=True)


class UserLogin(BaseModel):
    email: str
    password: str

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_new_password: str

    model_config = ConfigDict(arbitrary_types_allowed=True)


class User(BaseModel):
    name: str
    email: str
    password_hash: str
    created_at: datetime = Field(default_factory=get_ist_time)
    status: str = "active"

    model_config = ConfigDict(arbitrary_types_allowed=True)
