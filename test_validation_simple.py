#!/usr/bin/env python3
"""
Simple test for validation logic without requiring OCR dependencies.
This tests the _validate_invoice_fields function directly.
"""

import sys
import os

# Add the current directory to Python path to import pytes
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_validation_logic():
    """Test the validation logic directly"""
    
    # Import the validation function
    try:
        from pytes import _validate_invoice_fields
    except ImportError as e:
        print(f"❌ Failed to import validation function: {e}")
        return False
    
    print("🧪 Testing validation logic...")
    
    # Test 1: All fields present (valid)
    print("\n1. Testing valid invoice (all fields present):")
    result = _validate_invoice_fields(
        vendor="ABC Electronics",
        invoice_date="15/03/2024", 
        total_amount="₹ 1,250.00",
        hsn_codes=["8517", "8471"]
    )
    
    print(f"   Result: {result}")
    assert result["is_valid"] == True, "Valid invoice should pass validation"
    assert len(result["missing_fields"]) == 0, "Valid invoice should have no missing fields"
    print("   ✅ PASS")
    
    # Test 2: Missing bill number (invalid)
    print("\n2. Testing invalid invoice (missing bill number):")
    result = _validate_invoice_fields(
        vendor="",  # Empty vendor
        invoice_date="15/03/2024",
        total_amount="₹ 1,250.00", 
        hsn_codes=["8517", "8471"]
    )
    
    print(f"   Result: {result}")
    assert result["is_valid"] == False, "Invoice with missing bill number should fail validation"
    assert "bill number" in result["missing_fields"], "Should report missing bill number"
    print("   ✅ PASS")
    
    # Test 3: Missing invoice date (invalid)
    print("\n3. Testing invalid invoice (missing invoice date):")
    result = _validate_invoice_fields(
        vendor="ABC Electronics",
        invoice_date=None,  # Missing date
        total_amount="₹ 1,250.00",
        hsn_codes=["8517", "8471"]
    )
    
    print(f"   Result: {result}")
    assert result["is_valid"] == False, "Invoice with missing date should fail validation"
    assert "invoice date" in result["missing_fields"], "Should report missing invoice date"
    print("   ✅ PASS")
    
    # Test 4: Missing total amount (invalid)
    print("\n4. Testing invalid invoice (missing total amount):")
    result = _validate_invoice_fields(
        vendor="ABC Electronics",
        invoice_date="15/03/2024",
        total_amount=None,  # Missing amount
        hsn_codes=["8517", "8471"]
    )
    
    print(f"   Result: {result}")
    assert result["is_valid"] == False, "Invoice with missing amount should fail validation"
    assert "total amount" in result["missing_fields"], "Should report missing total amount"
    print("   ✅ PASS")
    
    # Test 5: Missing HSN code (invalid)
    print("\n5. Testing invalid invoice (missing HSN code):")
    result = _validate_invoice_fields(
        vendor="ABC Electronics",
        invoice_date="15/03/2024",
        total_amount="₹ 1,250.00",
        hsn_codes=[]  # Empty HSN codes
    )
    
    print(f"   Result: {result}")
    assert result["is_valid"] == False, "Invoice with missing HSN code should fail validation"
    assert "hsn code" in result["missing_fields"], "Should report missing HSN code"
    print("   ✅ PASS")
    
    # Test 6: Multiple missing fields (invalid)
    print("\n6. Testing invalid invoice (multiple missing fields):")
    result = _validate_invoice_fields(
        vendor="",  # Missing bill number
        invoice_date=None,  # Missing date
        total_amount=None,  # Missing amount
        hsn_codes=[]  # Missing HSN codes
    )
    
    print(f"   Result: {result}")
    assert result["is_valid"] == False, "Invoice with multiple missing fields should fail validation"
    assert len(result["missing_fields"]) == 4, "Should report all 4 missing fields"
    assert set(result["missing_fields"]) == {"bill number", "invoice date", "total amount", "hsn code"}
    print("   ✅ PASS")
    
    # Test 7: Short vendor name (invalid bill number)
    print("\n7. Testing invalid invoice (short vendor name):")
    result = _validate_invoice_fields(
        vendor="AB",  # Too short for bill number
        invoice_date="15/03/2024",
        total_amount="₹ 1,250.00",
        hsn_codes=["8517", "8471"]
    )
    
    print(f"   Result: {result}")
    assert result["is_valid"] == False, "Invoice with short vendor name should fail validation"
    assert "bill number" in result["missing_fields"], "Should report missing bill number"
    print("   ✅ PASS")
    
    # Test 8: Message format
    print("\n8. Testing validation message format:")
    # Valid case
    valid_result = _validate_invoice_fields("ABC Electronics", "15/03/2024", "₹ 1,250.00", ["8517"])
    assert valid_result["message"] == "Invoice validation successful", "Valid case should have success message"
    
    # Invalid case
    invalid_result = _validate_invoice_fields("", None, None, [])
    assert invalid_result["message"] == "kindly upload the image of invoice", "Invalid case should have error message"
    print("   ✅ PASS")
    
    print("\n🎉 All validation tests passed!")
    return True

if __name__ == "__main__":
    try:
        success = test_validation_logic()
        if success:
            print("\n✅ Validation system is working correctly!")
            print("📝 Summary:")
            print("   - All required fields are properly validated")
            print("   - Missing fields are correctly identified")
            print("   - Validation messages are appropriate")
            print("   - Database storage will be prevented for invalid invoices")
        else:
            print("\n❌ Validation tests failed!")
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)