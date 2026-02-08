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
    invoice_date: str | None = None
    total_amount: str | None = None
    items: list[dict] = Field(default_factory=list)
    hsn_codes: list[str] = Field(default_factory=list)
    gst_payable: float | None = None
    raw_text: str
    cleaned_text: str
    lines: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=get_ist_time)
    status: str = "processed"

    # Srno, bill no, Particulars, gst amt, total amt

    model_config = ConfigDict(arbitrary_types_allowed=True)


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


class User(BaseModel):
    name: str
    email: str
    password_hash: str
    created_at: datetime = Field(default_factory=get_ist_time)
    status: str = "active"

    model_config = ConfigDict(arbitrary_types_allowed=True)
