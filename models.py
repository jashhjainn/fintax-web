from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Any
import pytz

def get_ist_time():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

class Invoice(BaseModel):
    filename: str
    content_type: str
    upload_date: datetime = Field(default_factory=get_ist_time)
    status: str = "uploaded"
    
    # We don't include image_data here for basic validation as it's handled separately 
    # or we can type it as Any/bytes if we want to include it in the model.
    # For MongoDB storage, we convert this model to a dict and add the binary data.

    model_config = ConfigDict(arbitrary_types_allowed=True)
