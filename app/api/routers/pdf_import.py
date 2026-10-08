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
from ..services import ai_parser
from ..services.ai_parser import AIParseError
from ..logging_setup import audit
from ..services.import_review import apply_edit, compare_parses, find_lab, find_lab_in_unit, normalize_unit, review_rows, row_date, row_status
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

# Ensure uploads directory exists
# Use absolute path to handle Docker working directory differences
UPLOADS_DIR = Path("/app/data/uploads/pdfs")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Titles and credentials ignored when comparing provider names.
_NAME_NOISE = {
    'dr', 'doctor', 'md', 'do', 'np', 'pa', 'pac', 'fnp', 'aprn', 'dnp', 'rn', 'crnp', 'phd',
    'mph', 'mbbs', 'facp', 'faafp', 'dc', 'od', 'jr', 'sr', 'ii', 'iii', 'npi',
}


def _name_tokens(name: str) -> List[str]:
    """Normalize a person's name to lowercase tokens without titles, credentials or initials."""
    name = name.strip()
    # "Smith, Jane MD" -> "Jane MD Smith"
    if name.count(',') == 1:
        last, rest = name.split(',')
        if last.strip() and rest.strip():
            name = f"{rest} {last}"
    tokens = re.findall(r"[a-z][a-z'\-]*", name.lower())
    return [t for t in tokens if t not in _NAME_NOISE and len(t) > 1]


def _match_provider(physician: Optional[str], db: Session) -> Optional[Provider]:
    """Find the saved provider whose name matches the provider printed on the report."""
    if not physician:
        return None
    wanted = _name_tokens(physician)
    if not wanted:
        return None
    providers = [(p, _name_tokens(p.name)) for p in db.query(Provider).all()]

    # Same name once titles, credentials and middle initials are ignored
    for provider, tokens in providers:
        if tokens and set(tokens) == set(wanted):
            return provider
    # Same first and last name (ignores middle names)
    if len(wanted) >= 2:
        for provider, tokens in providers:
            if len(tokens) >= 2 and {tokens[0], tokens[-1]} == {wanted[0], wanted[-1]}:
                return provider
    # Last name only on the report: accept it only when exactly one provider has that last name
    if len(wanted) == 1:
        candidates = [p for p, tokens in providers if tokens and tokens[-1] == wanted[0]]
        if len(candidates) == 1:
            return candidates[0]
    return None


def _imported_indices(import_log: PDFImportLog, db: Session) -> set:
    """Positions of the parsed rows already saved as results for this import."""
    indices = set()
    for (notes,) in db.query(LabResult.notes).filter(LabResult.pdf_import_id == str(import_log.id)).all():
        match = re.search(r"test index:\s*(\d+)", notes or "")
        if match:
            indices.add(int(match.group(1)))
    return indices


def _build_preview(parsed_data: dict, import_log: PDFImportLog, filename: str, db: Session) -> PDFImportPreview:
    """Match parsed tests and provider against the database and build the review data."""
    rows = review_rows(parsed_data.get('tests', []), db, _imported_indices(import_log, db), parsed_data.get('date_collected'))
    return PDFImportPreview(
        parser=parsed_data.get('parser', 'standard'),
        filename=filename,
        date_collected=parsed_data.get('date_collected'),
        total_tests_found=len(rows),
        tests=rows,
        importable_tests=[r for r in rows if r['readable']],
        problematic_tests=[r for r in rows if not r['readable']],
        matched_provider=_match_provider(parsed_data.get('physician'), db),
        physician=parsed_data.get('physician'),
        import_id=str(import_log.id),
        pdf_url=f"/api/pdf/{import_log.id}/file",
        import_status=import_log.status,
        fasting=parsed_data.get('fasting') if isinstance(parsed_data.get('fasting'), bool) else None,
        comparison=compare_parses(parsed_data, parsed_data['other_parse'], db) if parsed_data.get('other_parse') else None,
    )


def _report_details(test: dict, parsed_data: dict) -> dict:
    """Per-result details printed on the report: reference range, flag, comment, fasting."""
    ref_range = test.get('reference_range') if isinstance(test.get('reference_range'), dict) else {}
    ref_text = (ref_range.get('text') or '').strip() or None
    flag = (test.get('flag') or '').strip() or None
    comment = (test.get('lab_comment') or '').strip() or None
    fasting = parsed_data.get('fasting')
    return {
        'ref_low': ref_range.get('low'),
        'ref_high': ref_range.get('high'),
        'ref_text': ref_text[:100] if ref_text else None,
        'flag': flag[:20] if flag else None,
        'lab_comment': comment,
        'fasting': fasting if isinstance(fasting, bool) else None,
    }


async def _parse_with_ai(content: bytes, db: Session) -> dict:
    """Parse with the AI parser, passing existing test names so results map onto them."""
    known_lab_names = [name for (name,) in db.query(Lab.name).all()]
    return await ai_parser.parse_pdf_with_ai(content, known_lab_names)


async def _parse_content(content: bytes, db: Session, use_ai: bool = False) -> dict:
    """Parse a PDF, falling back to the AI parser (when enabled) if the standard parser finds nothing."""
    if use_ai:
        try:
            return await _parse_with_ai(content, db)
        except AIParseError as e:
            raise HTTPException(status_code=400, detail=f"AI parsing failed: {e}")

    try:
        parsed_data = await PDFParser().parse_pdf_content(content)
    except (ValueError, pypdf.errors.PdfReadError) as parse_error:
        if not ai_parser.is_enabled():
            raise
        logger.info("Standard parser failed, retrying with AI parser")
        try:
            return await _parse_with_ai(content, db)
        except AIParseError as e:
            logger.warning(f"AI parser fallback failed: {e}")
        # Report the standard parser's error, not the fallback's
        raise parse_error

    if not parsed_data.get('tests') and ai_parser.is_enabled():
        logger.info("Standard parser found no tests, retrying with AI parser")
        try:
            return await _parse_with_ai(content, db)
        except AIParseError as e:
            logger.warning(f"AI parser fallback failed: {e}")
    return parsed_data


@router.get("/ai-status")
def get_ai_status():
    """Report whether AI parsing is configured."""
    enabled = ai_parser.is_enabled()
    return {"enabled": enabled, "model": ai_parser.get_model() if enabled else None}


@router.post("/upload", response_model=PDFImportPreview)
async def upload_pdf(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    ai: bool = False
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
        if existing_import and not existing_import.tests_imported:
            # Nothing saved from it yet: read it again, so improvements to the readers since the
            # first upload apply. An earlier AI reading is kept to compare with and switch back to.
            previous = json.loads(existing_import.parsed_data) if existing_import.parsed_data else {}
            parsed_data = await _parse_content(content, db, use_ai=ai)
            if previous.get('parser') == 'ai' and parsed_data.get('parser') != 'ai':
                previous.pop('other_parse', None)
                parsed_data['other_parse'] = previous
            stored = Path(existing_import.file_path) if existing_import.file_path else None
            if not stored or not stored.exists():
                stored = UPLOADS_DIR / f"{file_hash[:12]}_{secure_filename(file.filename) or 'report.pdf'}"
                stored.write_bytes(content)
                existing_import.file_path = str(stored)
            existing_import.status = "pending"
            existing_import.error_message = None
            _save_parse(existing_import, parsed_data, db)
            audit("import.reread", import_id=existing_import.id, parser=parsed_data.get('parser', 'standard'),
                  tests=len(parsed_data.get('tests', [])))
            return _build_preview(parsed_data, existing_import, existing_import.filename, db)
        if existing_import:
            return PDFImportPreview(
                filename=file.filename,
                date_collected=existing_import.date_collected,
                total_tests_found=existing_import.total_tests_found,
                importable_tests=[],
                problematic_tests=[],
                matched_provider=None,
                import_id=str(existing_import.id),
                import_status=existing_import.status,
                duplicate_warning={
                    "message": "This PDF file has already been imported",
                    "previous_import_date": str(existing_import.created_at),
                    "previous_tests_imported": existing_import.tests_imported
                }
            )

        # Save under a sanitized name prefixed with the content hash, so different reports
        # that share a file name don't overwrite each other and the name can't escape the folder
        safe_name = secure_filename(file.filename) or "report.pdf"
        file_path = UPLOADS_DIR / f"{file_hash[:12]}_{safe_name}"
        with open(file_path, "wb") as f:
            f.write(content)

        # Parse PDF (AI parser when requested, or as a fallback when enabled)
        parsed_data = await _parse_content(content, db, use_ai=ai)

        # Create import log with cached parsed data
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
        audit("import.uploaded", import_id=import_log.id, parser=parsed_data.get('parser', 'standard'),
              tests=len(parsed_data.get('tests', [])), size_kb=len(content) // 1024)

        return _build_preview(parsed_data, import_log, file.filename, db)

    except HTTPException:
        raise
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

@router.get("/review/{import_id}", response_model=PDFImportPreview)
def get_import_review(import_id: int, db: Session = Depends(get_db)):
    """Review data for an earlier upload, to finish importing it (rows already saved are marked)."""
    import_log = db.query(PDFImportLog).filter_by(id=import_id).first()
    if not import_log:
        raise HTTPException(status_code=404, detail="Import not found")
    if not import_log.parsed_data:
        raise HTTPException(status_code=400, detail="This import has no parsed results to review")
    return _build_preview(json.loads(import_log.parsed_data), import_log, import_log.filename, db)


@router.get("/{import_id}/file")
def get_import_file(import_id: int, download: bool = False, db: Session = Depends(get_db)):
    """The uploaded PDF for an import, shown inline beside the review (or downloaded)."""
    import_log = db.query(PDFImportLog).filter_by(id=import_id).first()
    if not import_log or not import_log.file_path:
        raise HTTPException(status_code=404, detail="PDF file not found")
    path = Path(import_log.file_path)
    if not path.is_absolute():
        path = Path("/app") / path
    path = validate_file_path(path, UPLOADS_DIR)  # stored uploads only
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found")
    name = secure_filename(import_log.filename) or "report.pdf"
    disposition = "attachment" if download else "inline"
    return FileResponse(path=str(path), media_type="application/pdf", filename=name,
                        headers={"Content-Disposition": f'{disposition}; filename="{name}"'})


@router.post("/rescan-ai/{import_id}", response_model=PDFImportPreview)
async def rescan_with_ai(import_id: int, db: Session = Depends(get_db)):
    """Re-parse a pending import's PDF with the AI parser and replace its cached results."""
    if not ai_parser.is_enabled():
        raise HTTPException(status_code=400, detail="AI parsing is not enabled")

    import_log = db.query(PDFImportLog).filter_by(id=import_id).first()
    if not import_log:
        raise HTTPException(status_code=404, detail="Import not found")
    if import_log.tests_imported:
        raise HTTPException(status_code=400, detail="Tests from this import have already been saved; it can no longer be re-scanned")

    file_path = Path(import_log.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found")
    content = file_path.read_bytes()

    current = json.loads(import_log.parsed_data) if import_log.parsed_data else {}
    parsed_data = await _parse_content(content, db, use_ai=True)
    # Keep the built-in parser's reading so the two can be compared and the user can switch back
    parsed_data['other_parse'] = await _standard_reading(current, content)
    _save_parse(import_log, parsed_data, db)
    audit("import.rescanned", import_id=import_log.id, tests=len(parsed_data.get('tests', [])),
          built_in_tests=len(parsed_data['other_parse'].get('tests', [])))

    return _build_preview(parsed_data, import_log, import_log.filename, db)


async def _standard_reading(current: dict, content: bytes) -> dict:
    """The built-in parser's reading of a report, without any nested comparison data."""
    if current.get('parser', 'standard') != 'ai':
        reading = dict(current)
    elif current.get('other_parse'):
        reading = dict(current['other_parse'])
    else:
        # The AI read this report on upload (the built-in parser found nothing); read it again to compare
        try:
            reading = await PDFParser().parse_pdf_content(content)
        except (ValueError, pypdf.errors.PdfReadError) as e:
            reading = {'tests': [], 'error': str(e)}
    reading.pop('other_parse', None)
    reading['parser'] = 'standard'
    return reading


def _save_parse(import_log: PDFImportLog, parsed_data: dict, db: Session) -> None:
    import_log.parsed_data = json.dumps(parsed_data)
    import_log.total_tests_found = len(parsed_data.get('tests', []))
    import_log.date_collected = parsed_data.get('date_collected')
    import_log.updated_at = datetime.now()
    db.commit()
    db.refresh(import_log)


@router.post("/{import_id}/switch-reading", response_model=PDFImportPreview)
async def switch_reading(import_id: int, db: Session = Depends(get_db)):
    """Swap between the built-in parser's and the AI's reading of a pending import."""
    import_log = db.query(PDFImportLog).filter_by(id=import_id).first()
    if not import_log:
        raise HTTPException(status_code=404, detail="Import not found")
    parsed_data = json.loads(import_log.parsed_data) if import_log.parsed_data else {}
    other = parsed_data.pop('other_parse', None)
    if not other:
        raise HTTPException(status_code=400, detail="This report has only been read one way")
    if import_log.tests_imported:
        # Saved results are tied to row positions in the reading they came from
        raise HTTPException(status_code=400, detail="Tests from this import have already been saved; the reading can no longer be switched")
    other['other_parse'] = parsed_data
    _save_parse(import_log, other, db)
    audit("import.reading_switched", import_id=import_log.id, now_using=other.get('parser', 'standard'))
    return _build_preview(other, import_log, import_log.filename, db)


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
                "parser": preview.parser,
                "tests": preview.tests,
                "pdf_url": preview.pdf_url,
                "physician": preview.physician,
                "matched_provider": {"id": preview.matched_provider.id, "name": preview.matched_provider.name} if preview.matched_provider else None,
                "duplicate_warning": getattr(preview, 'duplicate_warning', None)
            })
            
        except HTTPException as e:
            failed_uploads.append({
                "filename": file.filename or "unknown",
                "error": e.detail
            })
        except Exception:
            logger.exception(f"Bulk upload failed for {file.filename}")
            failed_uploads.append({
                "filename": file.filename or "unknown",
                "error": "Unable to process PDF."
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
    global_settings = batch_confirmation.get("global_settings", {})
    individual_confirmations = batch_confirmation.get("individual_confirmations", [])
    if not individual_confirmations:
        raise HTTPException(status_code=400, detail="Nothing selected to import")

    try:
        cookie_patient_id = int(request.cookies.get("selectedPatientId", "1"))
    except (ValueError, TypeError):
        cookie_patient_id = 1

    files, failed = [], []
    for confirmation in individual_confirmations:
        import_log = db.query(PDFImportLog).filter_by(id=confirmation.get("import_id")).first()
        filename = import_log.filename if import_log else "Unknown file"
        try:
            merged = PDFImportConfirm(
                import_id=str(confirmation.get("import_id")),
                selected_tests=confirmation.get("selected_tests", []),
                provider_id=confirmation.get("provider_id") or global_settings.get("provider_id"),
                patient_id=confirmation.get("patient_id") or global_settings.get("patient_id") or cookie_patient_id,
                manual_date=confirmation.get("manual_date") or global_settings.get("manual_date"),
                edits=confirmation.get("edits") or {},
            )
            result = await confirm_pdf_import(request, merged, db)
            files.append(result.data)
        except HTTPException as e:
            failed.append({"import_id": confirmation.get("import_id"), "filename": filename, "error": e.detail})
        except Exception:
            logger.exception(f"Batch import failed for {filename}")
            failed.append({"import_id": confirmation.get("import_id"), "filename": filename, "error": "Import failed."})

    total = sum(f["imported_count"] for f in files)
    return APIResponse(
        success=not failed,
        message=f"Imported {total} results from {len(files)} file{'s' if len(files) != 1 else ''}"
                + (f"; {len(failed)} file{'s' if len(failed) != 1 else ''} failed" if failed else ""),
        data={
            "total_imported": total,
            "files": files,
            "failed_count": len(failed),
            "failed_files": failed,
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

def _create_lab(test: dict, db: Session) -> Lab:
    """New lab test for a report row, in the report's panel (or "Imported Tests")."""
    panel_name = (test.get('panel_name') or '').strip() or "Imported Tests"
    panel = db.query(Panel).filter_by(name=panel_name).first()
    if not panel:
        panel = Panel(name=panel_name)
        db.add(panel)
        db.flush()

    unit = None
    unit_name = (test.get('unit') or '').strip()
    if unit_name:
        unit = db.query(Unit).filter_by(name=unit_name).first()
        if not unit:
            unit = Unit(name=unit_name)
            db.add(unit)
            db.flush()

    ref = test.get('reference_range') if isinstance(test.get('reference_range'), dict) else {}
    ref_text = (ref.get('text') or '').strip()
    ref_low, ref_high, ref_type, ref_value = ref.get('low'), ref.get('high'), "range", None
    if ref_text.startswith('>') and ref_low is not None:
        ref_type, ref_value, ref_low, ref_high = "greater", ref_low, None, None
    elif ref_text.startswith('<') and ref_high is not None:
        ref_type, ref_value, ref_low, ref_high = "less", ref_high, None, None

    # A saved test with the same name (in any panel) means the user chose to keep this one
    # separate, typically because of a different unit, so give it a distinguishable name
    base = (test.get('name') or 'Unknown Test').strip()
    name, n = base, 1
    while db.query(Lab).filter(Lab.name.ilike(name)).first():
        n += 1
        name = f"{base} ({unit_name})" if n == 2 and unit_name else f"{base} ({n})"

    lab = Lab(name=name, panel_id=panel.id, unit_id=unit.id if unit else None,
              ref_low=ref_low, ref_high=ref_high, ref_type=ref_type, ref_value=ref_value)
    db.add(lab)
    db.flush()
    return lab


def _parse_collection_date(raw, where: str) -> datetime:
    """A collection date as a datetime; refuses a missing, invalid or future date."""
    if not raw:
        raise HTTPException(status_code=400, detail=f"Enter the collection date for {where}.")
    try:
        collected = datetime.fromisoformat(str(raw)[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"The collection date for {where} isn't a valid date.")
    if collected.date() > datetime.now().date():
        raise HTTPException(status_code=400, detail=f"The collection date for {where} is in the future.")
    return collected


def _collection_date(confirmation: PDFImportConfirm, import_log: PDFImportLog) -> datetime:
    """The collection date chosen on the review screen, else the one read from the report."""
    return _parse_collection_date(confirmation.manual_date or import_log.date_collected, import_log.filename)


@router.post("/confirm", response_model=APIResponse)
async def confirm_pdf_import(
    request: Request,
    confirmation: PDFImportConfirm,
    db: Session = Depends(get_db)
):
    """
    Save the selected rows of an uploaded report as lab results.

    selected_tests are row positions in the parsed report. edits holds corrections made on the
    review screen (name, result, unit, range) and which saved test each row goes to.

    Example:
        POST /api/pdf/confirm
        {"import_id": "12", "selected_tests": [0, 1, 3], "provider_id": 2, "manual_date": "2026-01-15",
         "edits": {"3": {"result": "5.6", "lab_id": 7}}}
    """
    try:
        import_id = int(confirmation.import_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid import id")
    import_log = db.query(PDFImportLog).filter_by(id=import_id).first()
    if not import_log:
        raise HTTPException(status_code=404, detail="Import not found")
    if not import_log.parsed_data:
        raise HTTPException(status_code=400, detail="This import has no parsed results")
    if not confirmation.provider_id or not db.query(Provider).filter_by(id=confirmation.provider_id).first():
        raise HTTPException(status_code=400, detail=f"Choose the provider for {import_log.filename}.")

    parsed_data = json.loads(import_log.parsed_data)
    parsed_tests = parsed_data.get('tests', [])
    report_date = None  # only needed when a selected row has no date of its own
    patient_id = confirmation.patient_id or 1
    already = _imported_indices(import_log, db)

    # Apply edits and check every selected row before saving anything
    rows, problems = [], []
    for index in dict.fromkeys(confirmation.selected_tests):
        if index < 0 or index >= len(parsed_tests) or index in already:
            continue
        edit = confirmation.edits.get(str(index))
        test = apply_edit(parsed_tests[index], edit.model_dump(exclude_unset=True)) if edit else parsed_tests[index]
        label = (test.get('name') or '').strip() or f"Row {index + 1}"
        if not (test.get('name') or '').strip():
            problems.append(f"{label}: enter the test name")
        if test.get('numeric_value') is None and not (test.get('result_text') or test.get('result') or '').strip():
            problems.append(f"{label}: enter the result")
        if edit and edit.lab_id and not db.query(Lab).filter_by(id=edit.lab_id).first():
            problems.append(f"{label}: the chosen test no longer exists")
        # Health summaries give each row its own date; other reports use the report's date
        own = row_date(test) if not (edit and edit.date_collected) else edit.date_collected
        if own:
            collected = _parse_collection_date(own, f"{label} in {import_log.filename}")
        else:
            report_date = report_date or _collection_date(confirmation, import_log)
            collected = report_date
        rows.append((index, test, edit, collected))
    if problems:
        raise HTTPException(status_code=400, detail=f"{import_log.filename}: " + "; ".join(problems))

    try:
        saved = []
        # A test can appear once per date (health summaries); rows with the same name and unit
        # that become a new test all go into that one new test
        created = {}
        for index, test, edit, collected in rows:
            if edit and edit.lab_id:
                lab = db.query(Lab).filter_by(id=edit.lab_id).first()
            else:
                key = ((test.get('name') or '').strip().lower(), normalize_unit(test.get('unit')))
                lab = created.get(key) or (None if (edit and edit.new_lab) else find_lab(test.get('name'), db))
                # "New test" never makes a second copy of a test already saved in this unit. The
                # review may not have known it: another report in the same batch, or a page
                # opened before an earlier import created it.
                lab = lab or find_lab_in_unit(test.get('name'), test.get('unit'), db)
                if lab is None:
                    lab = created[key] = _create_lab(test, db)

            if test.get('numeric_value') is not None:
                value, text = float(test['numeric_value']), None
            else:
                value, text = None, (test.get('result_text') or str(test.get('result') or '')).strip()

            result = LabResult(
                lab_id=lab.id,
                patient_id=patient_id,
                provider_id=confirmation.provider_id,
                result=value,
                result_text=text,
                date_collected=collected,
                notes=f"Imported from PDF: {import_log.filename} (test index: {index})",
                pdf_import_id=str(import_log.id),
                **_report_details(test, parsed_data)
            )
            result.lab = lab
            db.add(result)
            saved.append(result)

        db.flush()
        import_log.tests_imported = len(already) + len(saved)
        import_log.tests_skipped = max(len(parsed_tests) - import_log.tests_imported, 0)
        import_log.provider_id = confirmation.provider_id
        import_log.status = "completed"
        import_log.updated_at = datetime.now()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Saving results from {import_log.filename} failed.") from e
    audit("import.confirmed", import_id=import_log.id, saved=len(saved), new_tests=len(created),
          dates=len({r.date_collected.date() for r in saved}), provider_id=confirmation.provider_id,
          patient_id=patient_id)

    from ..utils.cache import api_cache
    api_cache.invalidate_pattern('results')
    api_cache.invalidate_pattern('dashboard')

    results = [{
        "lab_id": r.lab_id,
        "name": r.lab.name,
        "value": f"{r.result:g}" if r.result is not None else r.result_text,
        "unit": r.lab.unit.name if r.lab.unit else "",
        "status": r.status,
    } for r in saved]
    dates = sorted({r.date_collected.date().isoformat() for r in saved})
    return APIResponse(
        success=True,
        message=f"Imported {len(saved)} results from {import_log.filename}",
        data={
            "import_id": import_log.id,
            "filename": import_log.filename,
            "date_collected": dates[-1] if dates else None,
            "dates": dates,
            "imported_count": len(saved),
            "skipped_count": len(parsed_tests) - len(saved) - len(already),
            "results": results,
            "out_of_range": [r for r in results if r["status"] in ("high", "low", "abnormal")],
        }
    )

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

    # Results actually saved per import, counted in one query (the stored counter can be stale)
    from sqlalchemy import func
    saved = dict(
        db.query(LabResult.pdf_import_id, func.count(LabResult.id))
        .filter(LabResult.pdf_import_id.isnot(None))
        .group_by(LabResult.pdf_import_id)
        .all()
    )
    history = []
    for import_log in imports:
        item = import_log.to_dict()
        item.pop("parsed_data", None)  # large, and the page loads it per import when needed
        item["results_saved"] = saved.get(str(import_log.id), 0)
        history.append(item)
    return history

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

    # Delete the stored PDF (only ever inside the uploads folder)
    if import_log.file_path:
        try:
            file_path = validate_file_path(Path(import_log.file_path), UPLOADS_DIR)
            if file_path.exists():
                file_path.unlink()
        except Exception as e:
            logger.warning(f"Could not delete PDF file for import {import_log.id}: {str(e)}")

    # Delete the import log
    db.delete(import_log)
    db.commit()
    audit("import.deleted", import_id=import_id, results_deleted=deleted_results)

    return APIResponse(
        success=True,
        message=f"Successfully deleted PDF import '{import_log.filename}' and {deleted_results} associated lab results"
    )
