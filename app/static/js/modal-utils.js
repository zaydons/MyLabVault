/**
 * Global Modal Utilities
 * Handles PDF viewing, downloads, and other modal operations
 */

// Global variable to store current download URL
let currentPDFDownloadUrl = '';

/**
 * Show PDF in modal viewer
 * @param {string} filename - The PDF filename to display
 * @param {string} title - Optional title for the modal
 * @param {string} modalId - Optional modal ID (defaults to 'pdfModal')
 */
function showPDF(filename, title = null, modalId = 'pdfModal') {
    const modal = document.getElementById(modalId);
    const titleElement = document.getElementById(modalId + 'Title');
    const iframe = document.getElementById(modalId + 'Iframe');
    const loadingMessage = document.getElementById(modalId + 'LoadingMessage');
    const errorMessage = document.getElementById(modalId + 'ErrorMessage');
    const downloadBtn = document.getElementById(modalId + 'DownloadBtn');
    
    if (!modal || !titleElement || !iframe || !loadingMessage || !downloadBtn) {
        console.error('PDF modal elements not found. Make sure PDF modal is included.');
        return;
    }
    
    try {
        // Set title
        titleElement.textContent = title || filename || 'PDF Viewer';
        
        // Reset modal state
        iframe.style.display = 'none';
        loadingMessage.style.display = 'block';
        if (errorMessage) errorMessage.style.display = 'none';
        downloadBtn.style.display = 'none';
        iframe.src = '';
        
        // Show modal
        $('#' + modalId).modal('show');
        
        // Set iframe source after modal is shown
        $('#' + modalId).on('shown.bs.modal', function() {
            const pdfUrl = `/api/pdf/file/${encodeURIComponent(filename)}`;
            const downloadUrl = `/api/pdf/file/${encodeURIComponent(filename)}?download=true`;
            iframe.src = pdfUrl;
            currentPDFDownloadUrl = downloadUrl;
            downloadBtn.style.display = 'inline-block';
            
            // Handle iframe load
            iframe.onload = function() {
                loadingMessage.style.display = 'none';
                iframe.style.display = 'block';
            };
            
            iframe.onerror = function() {
                loadingMessage.style.display = 'none';
                if (errorMessage) errorMessage.style.display = 'block';
            };
            
            // Remove event handler after first use
            $('#' + modalId).off('shown.bs.modal');
        });
        
    } catch (error) {
        console.error('Error showing PDF modal:', error);
        if (typeof showNotification === 'function') {
            showNotification('Unable to display PDF: ' + error.message, 'error', 'PDF Viewer Error');
        } else {
            alert('Unable to display PDF: ' + error.message);
        }
    }
}

/**
 * Download current PDF using fetch + blob method to force download
 */
async function downloadCurrentPDF() {
    if (currentPDFDownloadUrl) {
        try {
            // Show loading state - try multiple possible modal IDs
            const downloadBtn = document.getElementById('pdfModalDownloadBtn') || 
                              document.getElementById('pdfDownloadBtn') ||
                              document.querySelector('[onclick*="downloadCurrentPDF"]');
                              
            if (downloadBtn) {
                const originalHTML = downloadBtn.innerHTML;
                downloadBtn.innerHTML = '<i class="mdi mdi-loading mdi-spin mr-1"></i>Downloading...';
                downloadBtn.disabled = true;
            }
            
            // Fetch the PDF as a blob to force download
            const response = await fetch(currentPDFDownloadUrl);
            if (!response.ok) {
                throw new Error('Download failed');
            }
            
            const blob = await response.blob();
            
            // Extract filename from URL or use default
            const urlParts = currentPDFDownloadUrl.split('/');
            const filenameWithParams = urlParts[urlParts.length - 1];
            const filename = filenameWithParams.split('?')[0] || 'document.pdf';
            
            // Create object URL and download
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = decodeURIComponent(filename);
            link.style.display = 'none';
            document.body.appendChild(link);
            link.click();
            
            // Cleanup
            document.body.removeChild(link);
            window.URL.revokeObjectURL(url);
            
            // Restore button state
            if (downloadBtn) {
                downloadBtn.innerHTML = '<i class="mdi mdi-download mr-1"></i>Download PDF';
                downloadBtn.disabled = false;
            }
            
        } catch (error) {
            console.error('Download failed:', error);
            
            // Show error and restore button state
            const downloadBtn = document.getElementById('pdfModalDownloadBtn') || 
                              document.getElementById('pdfDownloadBtn') ||
                              document.querySelector('[onclick*="downloadCurrentPDF"]');
                              
            if (downloadBtn) {
                downloadBtn.innerHTML = '<i class="mdi mdi-download mr-1"></i>Download PDF';
                downloadBtn.disabled = false;
            }
            
            // Show error notification if available
            if (typeof showNotification === 'function') {
                showNotification('Download failed. Please try again.', 'error', 'Download Error');
            } else {
                alert('Download failed. Please try again.');
            }
        }
    }
}

/**
 * Show a notification message
 * @param {string} message - The message to display
 * @param {string} type - Type: 'success', 'error', 'warning', 'info'
 * @param {string} title - Optional title for the notification
 */
function showNotification(message, type = 'success', title = null) {
    const modal = document.getElementById('notificationModal');
    if (!modal) {
        // Fallback to alert if notification modal not available
        alert((title ? title + ': ' : '') + message);
        return;
    }
    
    const header = document.getElementById('notificationModalHeader');
    const icon = document.getElementById('notificationModalIcon');
    const titleElement = document.getElementById('notificationModalTitle');
    const messageElement = document.getElementById('notificationModalMessage');
    const button = document.getElementById('notificationModalBtn');
    
    // Configure based on type
    const config = {
        success: {
            headerClass: 'bg-success text-white',
            icon: 'mdi-check-circle',
            title: title || 'Success',
            buttonClass: 'btn-success'
        },
        error: {
            headerClass: 'bg-danger text-white',
            icon: 'mdi-alert-circle',
            title: title || 'Error',
            buttonClass: 'btn-danger'
        },
        warning: {
            headerClass: 'bg-warning text-dark',
            icon: 'mdi-alert',
            title: title || 'Warning',
            buttonClass: 'btn-warning'
        },
        info: {
            headerClass: 'bg-info text-white',
            icon: 'mdi-information',
            title: title || 'Information',
            buttonClass: 'btn-info'
        }
    };
    
    const settings = config[type] || config.info;
    
    // Apply styling
    if (header) header.className = `modal-header ${settings.headerClass}`;
    if (icon) icon.className = `mdi ${settings.icon} mr-2`;
    if (titleElement) titleElement.textContent = settings.title;
    if (messageElement) messageElement.textContent = message;
    if (button) button.className = `btn ${settings.buttonClass}`;
    
    // Show modal
    $('#notificationModal').modal('show');
}

/**
 * Show a confirmation dialog
 * @param {string} message - The confirmation message
 * @param {string} title - Optional title for the dialog
 * @param {string} confirmText - Text for confirm button
 * @param {string} cancelText - Text for cancel button
 * @returns {Promise<boolean>} - Promise that resolves to true if confirmed
 */
function showConfirmation(message, title = 'Confirm Action', confirmText = 'OK', cancelText = 'Cancel') {
    return new Promise((resolve) => {
        const modal = document.getElementById('confirmationModal');
        if (!modal) {
            // Fallback to confirm if confirmation modal not available
            resolve(confirm((title ? title + ': ' : '') + message));
            return;
        }
        
        const header = modal.querySelector('.modal-header');
        const titleElement = document.getElementById('confirmationModalTitle');
        const messageElement = document.getElementById('confirmationModalMessage');
        const confirmBtn = document.getElementById('confirmationModalConfirmBtn');
        
        // Set content
        if (titleElement) titleElement.textContent = title;
        if (messageElement) messageElement.textContent = message;
        if (confirmBtn) confirmBtn.textContent = confirmText;
        
        // Style based on action type
        if (confirmBtn) {
            if (confirmText.toLowerCase().includes('delete')) {
                if (header) header.className = 'modal-header bg-danger text-white';
                confirmBtn.className = 'btn btn-danger';
            } else if (confirmText.toLowerCase().includes('process')) {
                if (header) header.className = 'modal-header bg-success text-white';
                confirmBtn.className = 'btn btn-success';
            } else {
                if (header) header.className = 'modal-header bg-primary text-white';
                confirmBtn.className = 'btn btn-primary';
            }
        }
        
        // Handle confirmation
        const handleConfirm = () => {
            $('#confirmationModal').modal('hide');
            if (confirmBtn) confirmBtn.removeEventListener('click', handleConfirm);
            resolve(true);
        };
        
        // Handle cancel (including backdrop click and ESC)
        const handleCancel = () => {
            if (confirmBtn) confirmBtn.removeEventListener('click', handleConfirm);
            $('#confirmationModal').off('hidden.bs.modal', handleCancel);
            resolve(false);
        };
        
        // Attach event listeners
        if (confirmBtn) confirmBtn.addEventListener('click', handleConfirm);
        $('#confirmationModal').on('hidden.bs.modal', handleCancel);
        
        // Show modal
        $('#confirmationModal').modal('show');
    });
}

// Initialize modal event handlers when document is ready
$(document).ready(function() {
    // Handle PDF view button clicks using event delegation
    $(document).on('click', '.view-pdf-btn', function() {
        const filename = $(this).data('filename');
        if (filename) {
            showPDF(filename);
        }
    });
    
    // Clear PDF iframe when any PDF modal closes to save memory
    $('[id*="Modal"]').on('hidden.bs.modal', function() {
        const iframe = $(this).find('iframe[id*="Iframe"]');
        if (iframe.length > 0) {
            iframe[0].src = '';
        }
    });
    
    // Improve accessibility by managing focus when modals are shown/hidden
    $('.modal').on('shown.bs.modal', function() {
        // Remove aria-hidden after modal is fully shown
        $(this).removeAttr('aria-hidden');
    });
    
    $('.modal').on('hide.bs.modal', function() {
        // Clear focus from any elements inside the modal before hiding
        $(this).find('*').blur();
    });
});

/**
 * Date Formatting Utilities
 * Provides user-configurable date formatting based on user preferences
 */

// Global variable to cache user's date format preference
let userDateFormat = null;

/**
 * Get user's preferred date format from API or use default
 * @returns {Promise<string>} The user's preferred date format
 */
async function getUserDateFormat() {
    if (userDateFormat) {
        return userDateFormat;
    }
    
    try {
        const response = await fetch('/api/settings/user');
        if (response.ok) {
            const settings = await response.json();
            userDateFormat = settings.date_format || 'MM/DD/YYYY';
            return userDateFormat;
        }
    } catch (error) {
        console.warn('Could not fetch user date format:', error);
    }
    
    // Fallback to default US format
    userDateFormat = 'MM/DD/YYYY';
    return userDateFormat;
}

/**
 * Format a date according to user's preferred format
 * @param {string|Date} dateInput - The date to format (ISO string, Date object, or other parseable format)
 * @param {string} format - Optional format override (if not provided, uses user preference)
 * @returns {Promise<string>} The formatted date string
 */
async function formatUserDate(dateInput, format = null) {
    if (!dateInput) {
        return 'Not available';
    }
    
    try {
        // Parse the input date
        let date;
        if (dateInput instanceof Date) {
            date = dateInput;
        } else {
            // Handle ISO strings and other formats
            date = new Date(dateInput);
        }
        
        if (isNaN(date.getTime())) {
            return 'Invalid date';
        }
        
        // Get format preference if not provided
        const dateFormat = format || await getUserDateFormat();
        
        // Extract date components
        const year = date.getFullYear();
        const month = (date.getMonth() + 1).toString().padStart(2, '0');
        const day = date.getDate().toString().padStart(2, '0');
        
        // Month abbreviations array
        const monthAbbreviations = [
            'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
        ];
        const monthAbbr = monthAbbreviations[date.getMonth()];
        
        // Format according to preference
        switch (dateFormat) {
            case 'MM/DD/YYYY':
                return `${month}/${day}/${year}`;
            case 'DD/MM/YYYY':
                return `${day}/${month}/${year}`;
            case 'YYYY-MM-DD':
                return `${year}-${month}-${day}`;
            case 'MM-DD-YYYY':
                return `${month}-${day}-${year}`;
            case 'DD-MM-YYYY':
                return `${day}-${month}-${year}`;
            case 'DD-MMM-YYYY':
                return `${day}-${monthAbbr}-${year}`;
            case 'YYYY-MMM-DD':
                return `${year}-${monthAbbr}-${day}`;
            default:
                return `${month}/${day}/${year}`; // Fallback to US format
        }
    } catch (error) {
        console.error('Error formatting date:', error);
        return 'Invalid date';
    }
}

/**
 * Format a date synchronously using a cached format preference
 * Use this when you already know the format or for better performance
 * @param {string|Date} dateInput - The date to format
 * @param {string} format - The format to use (defaults to MM/DD/YYYY)
 * @returns {string} The formatted date string
 */
function formatDateSync(dateInput, format = 'MM/DD/YYYY') {
    if (!dateInput) {
        return 'Not available';
    }
    
    try {
        // Parse the input date
        let date;
        if (dateInput instanceof Date) {
            date = dateInput;
        } else {
            date = new Date(dateInput);
        }
        
        if (isNaN(date.getTime())) {
            return 'Invalid date';
        }
        
        // Extract date components
        const year = date.getFullYear();
        const month = (date.getMonth() + 1).toString().padStart(2, '0');
        const day = date.getDate().toString().padStart(2, '0');
        
        // Month abbreviations array
        const monthAbbreviations = [
            'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
        ];
        const monthAbbr = monthAbbreviations[date.getMonth()];
        
        // Format according to preference
        switch (format) {
            case 'MM/DD/YYYY':
                return `${month}/${day}/${year}`;
            case 'DD/MM/YYYY':
                return `${day}/${month}/${year}`;
            case 'YYYY-MM-DD':
                return `${year}-${month}-${day}`;
            case 'MM-DD-YYYY':
                return `${month}-${day}-${year}`;
            case 'DD-MM-YYYY':
                return `${day}-${month}-${year}`;
            case 'DD-MMM-YYYY':
                return `${day}-${monthAbbr}-${year}`;
            case 'YYYY-MMM-DD':
                return `${year}-${monthAbbr}-${day}`;
            default:
                return `${month}/${day}/${year}`;
        }
    } catch (error) {
        console.error('Error formatting date:', error);
        return 'Invalid date';
    }
}

/**
 * Clear cached date format to force refresh from server
 * Call this after updating user date format preferences
 */
function clearDateFormatCache() {
    userDateFormat = null;
}

/**
 * Initialize date formatting by loading user preferences
 * Call this on page load to cache the user's format preference
 */
async function initializeDateFormatting() {
    try {
        await getUserDateFormat();
    } catch (error) {
        console.warn('Could not initialize date formatting:', error);
    }
}