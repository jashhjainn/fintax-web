from fastapi.testclient import TestClient
from main import app
from unittest.mock import MagicMock, patch
import bson

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "FINTAX API is running"}

@patch("main.db")
def test_upload_invoice(mock_db):
    # Mock the database insert operation
    mock_insert_result = MagicMock()
    mock_insert_result.inserted_id = "test_id_123"
    mock_db.invoices.insert_one.return_value = mock_insert_result

    # Create a dummy file
    file_content = b"fake image content"
    files = {"file": ("test_image.png", file_content, "image/png")}

    response = client.post("/upload/", files=files)

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "File uploaded successfully"
    assert data["id"] == "test_id_123"
    assert data["filename"] == "test_image.png"

    # Verify that insert_one was called
    mock_db.invoices.insert_one.assert_called_once()
    
    # Verify the arguments passed to insert_one
    call_args = mock_db.invoices.insert_one.call_args[0][0]
    assert call_args["filename"] == "test_image.png"
    assert call_args["content_type"] == "image/png"
    assert "upload_date" in call_args
    assert "image_data" in call_args
    assert isinstance(call_args["image_data"], bson.Binary)
