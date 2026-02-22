// Ledger sorting functionality
document.addEventListener('DOMContentLoaded', function() {
    // Get the dropdown elements
    const sortBySelect = document.getElementById('sortBy');
    const sortOrderSelect = document.getElementById('sortOrder');
    const ledgerBody = document.getElementById('ledgerBody');
    const financialYearSelect = document.getElementById('financialYear');
    const monthFilterSelect = document.getElementById('monthFilter');

    // Store original data for sorting
    let originalRows = [];
    let currentRows = [];

    // Initialize sorting when page loads
    function initSorting() {
        // Add event listeners for sorting
        if (sortBySelect) {
            sortBySelect.addEventListener('change', handleSort);
        }
        
        if (sortOrderSelect) {
            sortOrderSelect.addEventListener('change', handleSort);
        }
        
        // Listen for filter changes to re-sort filtered data
        if (financialYearSelect) {
            financialYearSelect.addEventListener('change', updateCurrentRows);
        }
        
        if (monthFilterSelect) {
            monthFilterSelect.addEventListener('change', updateCurrentRows);
        }
    }

    // Update current rows based on filters
    function updateCurrentRows() {
        // This will be called when filters change
        // We'll need to get the visible rows after filtering
        // Use a small delay to ensure the table has been re-rendered by script.js
        setTimeout(() => {
            const allRows = Array.from(ledgerBody.querySelectorAll('tr'));
            currentRows = allRows;
            
            // Re-apply current sort
            handleSort();
        }, 100);
    }

    // Handle sorting logic
    function handleSort() {
        const sortBy = sortBySelect ? sortBySelect.value : 'bill_no';
        const sortOrder = sortOrderSelect ? sortOrderSelect.value : 'asc';
        
        // Sort the current rows
        currentRows.sort((a, b) => {
            let aValue = getCellValue(a, sortBy);
            let bValue = getCellValue(b, sortBy);
            
            // Handle different data types
            if (sortBy === 'bill_no') {
                // String comparison
                aValue = String(aValue).toLowerCase();
                bValue = String(bValue).toLowerCase();
                return sortOrder === 'asc' ? aValue.localeCompare(bValue) : bValue.localeCompare(aValue);
            } else if (sortBy === 'gst_payable' || sortBy === 'total_amt') {
                // Numeric comparison
                aValue = parseFloat(aValue) || 0;
                bValue = parseFloat(bValue) || 0;
                return sortOrder === 'asc' ? aValue - bValue : bValue - aValue;
            } else if (sortBy === 'invoice_date') {
                // Date comparison
                aValue = new Date(aValue);
                bValue = new Date(bValue);
                return sortOrder === 'asc' ? aValue - bValue : bValue - aValue;
            }
            
            return 0;
        });
        
        // Re-render the table with sorted rows
        renderSortedTable();
    }

    // Get cell value based on sort criteria
    function getCellValue(row, sortBy) {
        const cells = row.querySelectorAll('td');
        
        switch(sortBy) {
            case 'bill_no':
                return cells[2] ? cells[2].textContent.trim() : '';
            case 'gst_payable':
                // Extract numeric value from GST Payable cell (handle currency formatting)
                const gstCell = cells[4];
                if (!gstCell) return 0;
                const gstText = gstCell.textContent.trim();
                // Remove currency symbols, commas, and extract numeric value
                const numericValue = gstText.replace(/[^\d.]/g, '');
                return numericValue ? parseFloat(numericValue) : 0;
            case 'total_amt':
                // Extract numeric value from Total Amt cell (handle currency formatting)
                const totalCell = cells[5];
                if (!totalCell) return 0;
                const totalText = totalCell.textContent.trim();
                // Remove currency symbols, commas, and extract numeric value
                const totalNumeric = totalText.replace(/[^\d.]/g, '');
                return totalNumeric ? parseFloat(totalNumeric) : 0;
            case 'invoice_date':
                // Try to find date in particulars or assume format
                const particulars = cells[3] ? cells[3].textContent.trim() : '';
                // Look for date pattern in particulars (DD/MM/YYYY or DD-MM-YYYY)
                const dateMatch = particulars.match(/(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})/);
                return dateMatch ? dateMatch[1] : '';
            default:
                return '';
        }
    }

    // Render the sorted table
    function renderSortedTable() {
        // Clear current table body
        ledgerBody.innerHTML = '';
        
        // Append sorted rows and update Sr No. to be sequential
        currentRows.forEach((row, index) => {
            // Find the Sr No. cell (column 1) and update it
            const srNoCell = row.querySelector('td:nth-child(2)');
            if (srNoCell) {
                srNoCell.textContent = index + 1;
            }
            ledgerBody.appendChild(row);
        });
    }

    // Update sorting when ledger data changes (called from script.js)
    function updateSorting() {
        // Get all rows from the table
        const rows = Array.from(ledgerBody.querySelectorAll('tr'));
        originalRows = rows;
        currentRows = [...rows];
        
        // Apply current sort if dropdowns have values
        if (sortBySelect && sortOrderSelect) {
            handleSort();
        }
    }

    // Expose function to global scope so script.js can call it
    window.updateLedgerSorting = updateSorting;

    // Initialize sorting
    initSorting();
});
