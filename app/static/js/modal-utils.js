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