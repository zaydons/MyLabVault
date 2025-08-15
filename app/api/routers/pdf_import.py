"""PDF import and processing routes."""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Form, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload
import pypdf
from werkzeug.utils import secure_filename
from ..database import get_db
from ..models import PDFImportLog, LabResult, Lab, Provider, Patient, Panel, Unit, ImportTemplate
from ..schemas import APIResponse, PDFImportPreview, PDFImportConfirm
from ..services.pdf_parser import PDFParser
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

# Ensure uploads directory exists
# Use absolute path to handle Docker working directory differences
UPLOADS_DIR = Path("/app/data/uploads/pdfs")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

@router.post("/upload", response_model=PDFImportPreview)
async def upload_pdf(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Upload and analyze PDF lab report file.

    Processes PDF file to extract lab results, dates, and provider information.
    Returns preview of extractable data for user confirmation.

    Example:
        POST /api/pdf/upload
        Content-Type: multipart/form-data
        file: labcorp_report.pdf

    Returns:
        PDFImportPreview with parsed tests, provider info, and import_id
    """
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    try:
        # Read file content
        content = await file.read()

        # Generate file hash for duplicate detection
        file_hash = hashlib.sha256(content).hexdigest()

        # Check for duplicate imports
        existing_import = db.query(PDFImportLog).filter_by(file_hash=file_hash).first()
        if existing_import:
            return PDFImportPreview(
                filename=file.filename,
                date_collected=existing_import.date_collected,
                total_tests_found=existing_import.total_tests_found,
                importable_tests=[],
                problematic_tests=[],
                matched_provider=None,
                import_id=str(existing_import.id),
                duplicate_warning={
                    "message": "This PDF file has already been imported",
                    "previous_import_date": str(existing_import.created_at),
                    "previous_tests_imported": existing_import.tests_imported
                }
            )

        # Save file
        file_path = UPLOADS_DIR / (file.filename or "unknown.pdf")
        with open(file_path, "wb") as f:
            f.write(content)

        # Parse PDF
        parser = PDFParser()
        parsed_data = await parser.parse_pdf_content(content)

        # Create import log with cached parsed data
        import json
        import_log = PDFImportLog(
            filename=file.filename,
            file_hash=file_hash,
            file_path=str(file_path),
            total_tests_found=len(parsed_data.get('tests', [])),
            date_collected=parsed_data.get('date_collected'),
            status="pending",
            parsed_data=json.dumps(parsed_data)  # Cache parsed data as JSON
        )
        db.add(import_log)
        db.commit()
        db.refresh(import_log)

        # Find matched provider if physician name is available
        matched_provider = None
        if parsed_data.get('physician'):
            # Simple fuzzy matching - in production, use more sophisticated matching
            providers = db.query(Provider).all()
            for provider in providers:
                if parsed_data['physician'].lower() in provider.name.lower():
                    matched_provider = provider
                    break

        # Convert tests to importable format and identify problematic ones
        importable_tests = []
        problematic_tests = []
        
        for test in parsed_data.get('tests', []):
            # Find matching lab test with improved matching logic
            lab_test = None
            if test.get('name'):
                test_name = test['name'].strip()
                
                # 1. Try exact match first (case insensitive)
                lab_test = db.query(Lab).filter(
                    Lab.name.ilike(test_name)
                ).first()
                
                # 2. If no exact match, try smart matching to avoid incorrect partial matches
                # This prevents "Hemoglobin" from matching "Hemoglobin A1C"
                if not lab_test:
                    all_labs = db.query(Lab).all()
                    for lab in all_labs:
                        # Check if the test name matches the beginning of the lab name followed by space or end
                        # This allows "TSH" to match "TSH (details)" but prevents "Hemoglobin" from matching "Hemoglobin A1C"
                        lab_name_lower = lab.name.lower()
                        test_name_lower = test_name.lower()
                        
                        # Match if test name is at start and followed by specific delimiters or end of string
                        # Allow: parentheses, commas, dashes, but NOT spaces followed by letters
                        if lab_name_lower.startswith(test_name_lower):
                            if len(lab_name_lower) == len(test_name_lower):
                                # Exact match
                                lab_test = lab
                                break
                            else:
                                next_char = lab_name_lower[len(test_name_lower)]
                                # Allow punctuation or space followed by punctuation
                                if next_char in '(),-':
                                    lab_test = lab
                                    break
                                elif (next_char == ' ' and 
                                      len(lab_name_lower) > len(test_name_lower) + 1 and
                                      lab_name_lower[len(test_name_lower) + 1] in '(),-'):
                                    lab_test = lab
                                    break
                
                # 3. NO fallback partial matching - if exact and smart matching fail,
                # it's better to create a new lab test than to incorrectly match
                # This prevents "Hemoglobin" from matching "Hemoglobin A1c"

            test_data = {
                'name': test.get('name', 'Unknown Test'),
                'result': test.get('result'),
                'result_text': test.get('result_text'),
                'unit': test.get('unit'),
                'reference_range': test.get('reference_range'),
                'is_numeric': test.get('is_numeric', False),
                'is_qualitative': test.get('is_qualitative', False),
                'numeric_value': test.get('numeric_value'),
                'matched_lab_id': lab_test.id if lab_test else None,
                'matched_lab_name': lab_test.name if lab_test else None,
                'confidence': 1.0 if lab_test else 0.0
            }
            
            # Identify problematic tests - be more lenient to allow manual review
            issues = []
            is_critical_issue = False
            
            if not test.get('name'):
                issues.append("Test name could not be extracted from PDF")
                is_critical_issue = True
            if not test.get('result') and not test.get('numeric_value') and not test.get('result_text'):
                issues.append("No result value could be extracted")
                is_critical_issue = True
            
            # Non-critical issues that shouldn't prevent import
            if not lab_test:
                issues.append("No matching lab test found in database - will create new lab test")
            if test.get('unit') and lab_test and lab_test.unit and test['unit'].lower() != lab_test.unit.name.lower():
                issues.append(f"Unit mismatch: PDF shows '{test['unit']}', database expects '{lab_test.unit.name}'")
            
            # Only mark as problematic if there are critical issues
            if is_critical_issue:
                test_data['issues'] = issues
                problematic_tests.append(test_data)
            else:
                # Mark as importable but include non-critical issues as warnings
                if issues:
                    test_data['warnings'] = issues
                importable_tests.append(test_data)

        return PDFImportPreview(
            filename=file.filename,
            date_collected=parsed_data.get('date_collected'),
            total_tests_found=len(parsed_data.get('tests', [])),
            importable_tests=importable_tests,
            problematic_tests=problematic_tests,
            matched_provider=matched_provider,
            import_id=str(import_log.id)
        )

    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="PDF file not found. Please try uploading again.")
    except PermissionError:
        raise HTTPException(status_code=500, detail="Unable to access PDF file. Please check file permissions.")
    except pypdf.errors.PdfReadError:
        raise HTTPException(status_code=400, detail="Invalid or corrupted PDF file. Please ensure the file is a valid PDF.")
    except ValueError as e:
        if "date" in str(e).lower():
            raise HTTPException(status_code=400, detail="Unable to extract date from PDF. Please ensure this is a valid lab report.")
        else:
            raise HTTPException(status_code=400, detail=f"Invalid data in PDF: {str(e)}")
    except MemoryError:
        raise HTTPException(status_code=413, detail="PDF file is too large to process. Please try a smaller file.")
    except Exception as e:
        # Log the actual error for debugging
        logger.error(f"Unexpected PDF processing error: {str(e)}")
        raise HTTPException(status_code=500, detail="Unable to process PDF. This may not be a compatible lab report format.")

@router.post("/bulk-upload")
async def bulk_upload_pdfs(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    """
    Upload and analyze multiple PDF lab report files.
    
    Processes each file individually using existing upload logic,
    groups them under a shared batch_id for batch operations.
    
    Returns:
        dict: Bulk import preview with batch_id and individual file results
    """
    import uuid
    batch_id = str(uuid.uuid4())
    
    successful_uploads = []
    failed_uploads = []
    duplicate_count = 0
    
    for file in files:
        try:
            if not file.filename or not file.filename.lower().endswith('.pdf'):
                failed_uploads.append({
                    "filename": file.filename or "unknown",
                    "error": "Only PDF files are allowed"
                })
                continue
            
            # Use existing upload logic
            preview = await upload_pdf(file, db)
            
            # Add batch_id to the created import log
            import_log = db.query(PDFImportLog).filter_by(id=int(preview.import_id)).first()
            if import_log:
                import_log.batch_id = batch_id
                db.commit()
            
            if hasattr(preview, 'duplicate_warning') and preview.duplicate_warning:
                duplicate_count += 1
                
            successful_uploads.append({
                "import_id": preview.import_id,
                "filename": preview.filename,
                "status": "duplicate" if hasattr(preview, 'duplicate_warning') and preview.duplicate_warning else "ready",
                "tests_found": preview.total_tests_found,
                "date_collected": preview.date_collected,
                "importable_tests": preview.importable_tests,  # Include parsed test details
                "duplicate_warning": getattr(preview, 'duplicate_warning', None)
            })
            
        except Exception as e:
            failed_uploads.append({
                "filename": file.filename or "unknown",
                "error": str(e)
            })
    
    return {
        "success": True,
        "batch_id": batch_id,
        "total_files": len(files),
        "successful_uploads": len(successful_uploads),
        "failed_uploads": len(failed_uploads),
        "duplicates": duplicate_count,
        "files": successful_uploads,
        "errors": failed_uploads,
        "estimated_import_time": f"{len(successful_uploads) * 10} seconds"
    }

@router.get("/batch-status/{batch_id}")
async def get_batch_status(batch_id: str, db: Session = Depends(get_db)):
    """
    Get status of batch PDF import operation.
    
    Args:
        batch_id: UUID of the batch to check
        db: Database session dependency
        
    Returns:
        dict: Batch status with individual import progress
    """
    imports = db.query(PDFImportLog).filter_by(batch_id=batch_id).all()
    
    if not imports:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    pending_count = len([i for i in imports if i.status == "pending"])
    completed_count = len([i for i in imports if i.status == "completed"])
    failed_count = len([i for i in imports if i.status == "failed"])
    
    return {
        "success": True,
        "batch_id": batch_id,
        "total": len(imports),
        "pending": pending_count,
        "completed": completed_count,
        "failed": failed_count,
        "progress_percent": int((completed_count + failed_count) / len(imports) * 100) if imports else 0,
        "imports": [import_log.to_dict() for import_log in imports]
    }

@router.post("/batch-confirm")
async def confirm_batch_import(
    request: Request,
    batch_confirmation: dict,
    db: Session = Depends(get_db)
):
    """
    Confirm and execute batch PDF import.
    
    Processes each import in the batch using existing confirmation logic
    with global settings applied as defaults.
    
    Args:
        batch_confirmation: Batch confirmation data with global settings
        db: Database session dependency
        
    Returns:
        APIResponse: Success status with import statistics
    """
    batch_id = batch_confirmation.get("batch_id")
    global_settings = batch_confirmation.get("global_settings", {})
    individual_confirmations = batch_confirmation.get("individual_confirmations", [])
    
    if not batch_id:
        raise HTTPException(status_code=400, detail="Batch ID is required")
    
    total_imported = 0
    total_skipped = 0
    failed_imports = []
    
    for confirmation in individual_confirmations:
        try:
            # Update import log with selected provider before processing
            import_log = db.query(PDFImportLog).filter_by(id=confirmation.get("import_id")).first()
            if import_log:
                import_log.provider_id = confirmation.get("provider_id") or global_settings.get("provider_id")
                db.commit()
            
            # Merge global settings with individual confirmation
            try:
                cookie_patient_id = request.cookies.get("selectedPatientId", "1")
                final_patient_id = confirmation.get("patient_id") or global_settings.get("patient_id") or int(cookie_patient_id)
            except (ValueError, TypeError):
                final_patient_id = 1
            
            merged_confirmation = PDFImportConfirm(
                import_id=confirmation.get("import_id"),
                selected_tests=confirmation.get("selected_tests", []),
                provider_id=confirmation.get("provider_id") or global_settings.get("provider_id"),
                patient_id=final_patient_id,
                manual_date=confirmation.get("manual_date") or global_settings.get("manual_date")
            )
            
            # Use existing confirmation logic
            result = await confirm_pdf_import(request, merged_confirmation, db)
            if result.success:
                total_imported += result.data.get("imported_count", 0)
                total_skipped += result.data.get("skipped_count", 0)
            
        except Exception as e:
            import_log = db.query(PDFImportLog).filter_by(id=confirmation.get("import_id")).first()
            filename = import_log.filename if import_log else "Unknown"
            error_msg = f"{filename}: {str(e)}"
            failed_imports.append(error_msg)
            logger.error(f"PDF Batch Import Failed for {filename}: {str(e)}")
    
    return APIResponse(
        success=len(failed_imports) == 0,
        message=f"Batch import completed: {total_imported} tests imported, {total_skipped} skipped" + 
               (f", {len(failed_imports)} files failed" if failed_imports else ""),
        data={
            "batch_id": batch_id,
            "total_imported": total_imported,
            "total_skipped": total_skipped,
            "failed_count": len(failed_imports),
            "failed_files": failed_imports
        }
    )

@router.delete("/cancel/{import_id}")
async def cancel_pdf_import(
    import_id: str,
    db: Session = Depends(get_db)
):
    """Cancel PDF import and cleanup resources."""
    try:
        # Get import log
        import_log = db.query(PDFImportLog).filter_by(id=int(import_id)).first()
        if not import_log:
            raise HTTPException(status_code=404, detail="Import not found")

        # Only allow cancellation of pending imports
        if import_log.status != "pending":
            raise HTTPException(status_code=400, detail="Cannot cancel completed imports")

        # Delete the uploaded file
        file_path = Path(import_log.file_path)
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception as e:
                logger.warning(f"Could not delete file {file_path}: {e}")

        # Delete the import log
        db.delete(import_log)
        db.commit()

        return APIResponse(
            success=True,
            message="Import cancelled and resources cleaned up"
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error cancelling import: {str(e)}")

@router.post("/confirm", response_model=APIResponse)
async def confirm_pdf_import(
    request: Request,
    confirmation: PDFImportConfirm,
    db: Session = Depends(get_db)
):
    """
    Confirm and execute PDF import with selected tests and provider.

    Takes the import_id from upload step and saves selected tests to database.
    Creates LabResult records and associates with provider and patient.

    Example:
        POST /api/pdf/confirm
        {
            "import_id": "123",
            "selected_test_indices": [0, 1, 2],
            "provider_id": 5,
            "manual_date": "2024-01-15"
        }

    Returns:
        APIResponse with success status and imported test count
    """
    try:
        # Get import log
        import_log = db.query(PDFImportLog).filter_by(id=int(confirmation.import_id)).first()
        if not import_log:
            raise HTTPException(status_code=404, detail="Import not found")

        # Use cached parsed data instead of re-parsing PDF
        import json
        if import_log.parsed_data:
            # Use cached parsed data
            parsed_data = json.loads(import_log.parsed_data)
        else:
            # Fallback: re-parse if cached data not available (backward compatibility)
            file_path = Path(import_log.file_path)
            if not file_path.exists():
                raise HTTPException(status_code=404, detail="PDF file not found")

            with open(file_path, "rb") as f:
                content = f.read()

            parser = PDFParser()
            parsed_data = await parser.parse_pdf_content(content)

        imported_count = 0
        skipped_count = 0

        # Import selected tests
        for test_index in confirmation.selected_tests:
            if test_index >= len(parsed_data.get('tests', [])):
                continue

            test = parsed_data['tests'][test_index]

            # Find or create lab test with improved matching logic
            test_name = test.get('name', '').strip()
            
            # 1. Try exact match first (case insensitive)
            lab_test = db.query(Lab).filter(
                Lab.name.ilike(test_name)
            ).first()
            
            # 2. If no exact match, try smart matching to avoid incorrect partial matches
            # This prevents "Hemoglobin" from matching "Hemoglobin A1C"
            if not lab_test:
                all_labs = db.query(Lab).all()
                for lab in all_labs:
                    # Check if the test name matches the beginning of the lab name followed by space or end
                    # This allows "TSH" to match "TSH (details)" but prevents "Hemoglobin" from matching "Hemoglobin A1C"
                    lab_name_lower = lab.name.lower()
                    test_name_lower = test_name.lower()
                    
                    # Match if test name is at start and followed by specific delimiters or end of string
                    # Allow: parentheses, commas, dashes, but NOT spaces followed by letters
                    if lab_name_lower.startswith(test_name_lower):
                        if len(lab_name_lower) == len(test_name_lower):
                            # Exact match
                            lab_test = lab
                            break
                        else:
                            next_char = lab_name_lower[len(test_name_lower)]
                            # Allow punctuation or space followed by punctuation
                            if next_char in '(),-':
                                lab_test = lab
                                break
                            elif (next_char == ' ' and 
                                  len(lab_name_lower) > len(test_name_lower) + 1 and
                                  lab_name_lower[len(test_name_lower) + 1] in '(),-'):
                                lab_test = lab
                                break
            
            # 3. NO fallback partial matching - if exact and smart matching fail,
            # it's better to create a new lab test than to incorrectly match
            # This prevents "Hemoglobin" from matching "Hemoglobin A1c"

            if not lab_test:
                # Create a basic lab test if not found - use panel from PDF or default
                panel_name = test.get('panel_name')

                if panel_name:
                    # Try to find existing panel or create it
                    panel = db.query(Panel).filter_by(name=panel_name).first()
                    if not panel:
                        panel = Panel(name=panel_name)
                        db.add(panel)
                        db.commit()
                        db.refresh(panel)
                else:
                    # Fall back to default panel for imported tests
                    panel = db.query(Panel).filter_by(name="Imported Tests").first()
                    if not panel:
                        panel = Panel(name="Imported Tests")
                        db.add(panel)
                        db.commit()
                        db.refresh(panel)

                # Extract reference range from parsed data and determine type
                ref_range = test.get('reference_range', {})
                ref_low = None
                ref_high = None
                ref_type = "range"  # Default type
                ref_value = None

                if isinstance(ref_range, dict):
                    ref_low = ref_range.get('low')
                    ref_high = ref_range.get('high')

                    # Check the original text to determine the correct type
                    ref_text = ref_range.get('text', '').strip()

                    # Determine reference range type based on the original text format
                    if ref_text.startswith('>'):
                        # Greater than format: >10.0 (parsed as {low: 10.0, high: None})
                        ref_type = "greater"
                        ref_value = ref_low  # The value after >
                        ref_low = None
                        ref_high = None
                    elif ref_text.startswith('<'):
                        # Less than format: <5.0 (parsed as {low: None, high: 5.0})
                        ref_type = "less"
                        ref_value = ref_high  # The value after <
                        ref_low = None
                        ref_high = None
                    elif ref_low is not None and ref_high is not None:
                        # Range format: 5.0-10.0
                        ref_type = "range"
                        ref_value = None
                        # Keep ref_low and ref_high as they are
                    else:
                        # Handle cases where we can't determine the type
                        ref_type = "range"
                        ref_value = None

                # Find or create unit based on extracted unit name
                unit_id = 1  # Default unit
                unit_name = test.get('unit', '').strip()
                if unit_name:
                    unit = db.query(Unit).filter_by(name=unit_name).first()
                    if not unit:
                        # Create new unit
                        unit = Unit(name=unit_name)
                        db.add(unit)
                        db.commit()
                        db.refresh(unit)
                    unit_id = unit.id

                lab_test = Lab(
                    name=test.get('name', 'Unknown Test'),
                    panel_id=panel.id,
                    unit_id=unit_id,
                    ref_low=ref_low,
                    ref_high=ref_high,
                    ref_type=ref_type,
                    ref_value=ref_value
                )
                db.add(lab_test)
                db.commit()
                db.refresh(lab_test)

            # Create lab result - handle both numeric and qualitative results
            result_value = None
            result_text = None

            # Check if this is a qualitative result
            if test.get('is_qualitative', False) or test.get('result_text'):
                result_text = test.get('result_text') or test.get('result', '')
                result_value = None  # No numeric value for qualitative results
            else:
                # Try to parse as numeric result
                try:
                    result_value = float(test.get('numeric_value') or test.get('value', 0))
                except (ValueError, TypeError):
                    # If parsing fails, treat as qualitative
                    result_text = str(test.get('result', ''))
                    result_value = None

            # Get patient ID from confirmation, cookie, or default to 1
            selected_patient_id = confirmation.patient_id
            
            if not selected_patient_id:
                try:
                    cookie_patient_id = request.cookies.get("selectedPatientId", "1")
                    selected_patient_id = int(cookie_patient_id)
                except (ValueError, TypeError):
                    selected_patient_id = 1
            
            lab_result = LabResult(
                lab_id=lab_test.id,
                patient_id=selected_patient_id,
                provider_id=confirmation.provider_id or 1,  # Default provider
                result=result_value,
                result_text=result_text,
                date_collected=(
                    datetime.fromisoformat(confirmation.manual_date) if confirmation.manual_date 
                    else datetime.fromisoformat(import_log.date_collected) if import_log.date_collected 
                    else datetime.now()
                ),
                notes=f"Imported from PDF: {import_log.filename} (test index: {test_index})",
                pdf_import_id=str(import_log.id)
            )
            db.add(lab_result)
            imported_count += 1

        # Update import log
        import_log.tests_imported = imported_count
        import_log.tests_skipped = skipped_count
        import_log.provider_id = confirmation.provider_id  # Save the selected provider
        import_log.status = "completed"
        import_log.updated_at = datetime.now()

        db.commit()

        # Invalidate results cache after importing lab results
        from ..utils.cache import api_cache
        api_cache.invalidate_pattern('results')
        api_cache.invalidate_pattern('dashboard')

        return APIResponse(
            success=True,
            message=f"Successfully imported {imported_count} test results",
            data={
                "imported_count": imported_count,
                "skipped_count": skipped_count
            }
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error importing results: {str(e)}")

def validate_filename(filename: str) -> str:
    """
    Validate and sanitize filename to prevent path injection attacks.
    
    Args:
        filename: The filename to validate
        
    Returns:
        str: Sanitized filename
        
    Raises:
        HTTPException: If filename is invalid or contains dangerous characters
    """
    if not filename:
        raise HTTPException(status_code=400, detail="Filename cannot be empty")

    # Use werkzeug's secure_filename to sanitize the filename
    safe_filename = secure_filename(filename)

    # Ensure filename ends with .pdf
    if not safe_filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    # Check for reasonable filename length
    if len(safe_filename) > 255:
        raise HTTPException(status_code=400, detail="Filename too long")
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename after sanitization")


    return safe_filename


def validate_file_path(file_path: Path, allowed_dir: Path) -> Path:
    """
    Validate that a file path is within the allowed directory.
    
    Args:
        file_path: The file path to validate
        allowed_dir: The allowed base directory
        
    Returns:
        Path: Resolved file path if valid
        
    Raises:
        HTTPException: If path is outside allowed directory
    """
    try:
        # Resolve both paths to handle symlinks and relative paths
        resolved_file_path = file_path.resolve()
        resolved_allowed_dir = allowed_dir.resolve()

        # Check if the file path is within the allowed directory using Path.relative_to()
        try:
            resolved_file_path.relative_to(resolved_allowed_dir)
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied: file outside allowed directory")

        return resolved_file_path
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid file path: {str(e)}")


@router.get("/file/{filename}")
async def get_pdf_file(filename: str, download: bool = False, db: Session = Depends(get_db)):
    """
    Serve PDF file for viewing in frontend or force download.

    Args:
        filename: Name of the PDF file to serve
        download: If True, force download; if False, display inline
        db: Database session dependency

    Returns:
        FileResponse: PDF file with appropriate headers

    Raises:
        HTTPException: 404 if file not found, 400/403 for security violations

    Features:
        - Validates filename to prevent path injection attacks
        - Ensures file access is restricted to allowed directories
        - Handles both direct file access and database lookup
        - Works with Docker container path differences
        - Sets proper MIME type and disposition for PDF viewing or downloading
        - Supports both inline PDF viewing and forced downloads
    """
    # Validate and sanitize the filename
    safe_filename = validate_filename(filename)

    # First try to find the file directly
    file_path = UPLOADS_DIR / safe_filename

    # Validate the file path is within allowed directory
    file_path = validate_file_path(file_path, UPLOADS_DIR)

    if not file_path.exists():
        # If not found, look up the actual file path from the database
        import_log = db.query(PDFImportLog).filter(PDFImportLog.filename == safe_filename).first()
        if import_log and import_log.file_path:
            # Convert relative path to absolute path (handles Docker working directory differences)
            actual_file_path = Path("/app") / import_log.file_path

            # Validate the database file path is also within allowed directories
            app_data_dir = Path("/app/data")
            actual_file_path = validate_file_path(actual_file_path, app_data_dir)

            if actual_file_path.exists():
                disposition = "attachment" if download else "inline"
                return FileResponse(
                    path=str(actual_file_path),
                    media_type='application/pdf',
                    filename=safe_filename,
                    headers={"Content-Disposition": f'{disposition}; filename="{safe_filename}"'}
                )

        raise HTTPException(status_code=404, detail="PDF file not found")

    disposition = "attachment" if download else "inline"
    return FileResponse(
        path=str(file_path),
        media_type='application/pdf',
        filename=safe_filename,
        headers={"Content-Disposition": f'{disposition}; filename="{safe_filename}"'}
    )


@router.get("/import-details/{import_id}")
async def get_import_details(import_id: int, db: Session = Depends(get_db)):
    """Get detailed information about a specific import."""
    import_log = db.query(PDFImportLog).filter(PDFImportLog.id == import_id).first()
    
    if not import_log:
        raise HTTPException(status_code=404, detail="Import not found")
    
    # Get the basic import data
    import_data = import_log.to_dict()
    
    # For completed imports, get information about which tests were imported
    if import_log.status == "completed":
        # Get all lab results for this import
        imported_results = db.query(LabResult).filter(
            LabResult.pdf_import_id == str(import_id)
        ).all()
        
        # Extract test indices from notes
        imported_test_indices = []
        for result in imported_results:
            if result.notes and "test index:" in result.notes:
                try:
                    # Extract test index from notes like "Imported from PDF: filename.pdf (test index: 5)"
                    index_part = result.notes.split("test index:")[-1].strip().rstrip(")")
                    test_index = int(index_part)
                    imported_test_indices.append(test_index)
                except (ValueError, IndexError):
                    continue
        
        # Always try to match by test name for results without test index
        # This handles mixed scenarios (some with indices, some without)
        if len(imported_results) > 0:
            try:
                # Parse the original PDF data to get test names
                parsed_data = json.loads(import_log.parsed_data) if import_log.parsed_data else {}
                if 'tests' in parsed_data:
                    # Get the names of imported lab tests that don't have test index in notes
                    imported_lab_names = []
                    for result in imported_results:
                        # Only include results that don't have test index (old imports)
                        if result.lab and result.lab.name and not ("test index:" in (result.notes or "")):
                            imported_lab_names.append(result.lab.name.lower().strip())
                    
                    # Find matching test indices by name for tests not already tracked
                    for test_index, test in enumerate(parsed_data['tests']):
                        # Skip if this test index is already tracked
                        if test_index in imported_test_indices:
                            continue
                            
                        test_name = test.get('name', '').lower().strip()
                        if test_name and test_name in imported_lab_names:
                            imported_test_indices.append(test_index)
            except Exception:
                pass
        
        # Update the database tests_imported count if it doesn't match the actual count
        actual_imported_count = len(imported_test_indices)
        if import_log.tests_imported != actual_imported_count:
            import_log.tests_imported = actual_imported_count
            db.commit()
            db.refresh(import_log)
            # Update the return data as well
            import_data['tests_imported'] = actual_imported_count
        
        import_data['imported_test_indices'] = imported_test_indices
    
    return import_data

@router.get("/history")
async def get_import_history(db: Session = Depends(get_db)):
    """Get complete PDF import history ordered by date."""
    imports = db.query(PDFImportLog).options(
        joinedload(PDFImportLog.provider)
    ).order_by(PDFImportLog.created_at.desc()).all()
    
    return [import_log.to_dict() for import_log in imports]

@router.delete("/{import_id}")
async def delete_pdf_import(
    import_id: int,
    db: Session = Depends(get_db)
):
    """Delete PDF import and all associated lab results."""
    # Find the import log
    import_log = db.query(PDFImportLog).filter(PDFImportLog.id == import_id).first()
    if not import_log:
        raise HTTPException(status_code=404, detail="PDF import not found")

    # Delete all associated lab results
    deleted_results = db.query(LabResult).filter(
        LabResult.pdf_import_id == str(import_id)
    ).delete()

    # Delete the PDF file from storage if it exists
    if import_log.filename:
        file_path = UPLOADS_DIR / import_log.filename
        try:
            if file_path.exists():
                file_path.unlink()
        except Exception as e:
            logger.warning(f"Could not delete PDF file {file_path}: {str(e)}")

    # Delete the import log
    db.delete(import_log)
    db.commit()

    return APIResponse(
        success=True,
        message=f"Successfully deleted PDF import '{import_log.filename}' and {deleted_results} associated lab results"
    )
