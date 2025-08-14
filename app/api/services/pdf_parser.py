"""PDF Parser Service for MyLabVault"""

import re
import io
from typing import Dict, List, Optional, Any
import pypdf
import pypdf.errors
from dateutil import parser as date_parser
import pdfplumber

class PDFParser:
    """
    Enhanced PDF parser for extracting lab results from medical reports.

    Supports multiple PDF formats using dual parsing strategy:
    1. pdfplumber for structured table extraction
    2. pypdf for fallback text extraction

    Features:
        - Automatic format detection (LabCorp, Quest, etc.)
        - Table-based and text-based result extraction
        - Date and provider information extraction
        - Reference range parsing with multiple formats
        - Panel organization and test categorization
        - Robust error handling for malformed PDFs
    """

    # Patterns for filtering out instructional text that shouldn't be parsed as test names or panels
    INSTRUCTIONAL_SKIP_PATTERNS = [
        r'Comments?:',          # Comment fields
        r'Interpretation:',     # Interpretation fields
        r'Please note',         # Instructional text
        r'Note:',              # Note fields
        r'Important:',         # Important notices
        r'Instructions?:',     # Instruction fields
        r'Disclaimer:',        # Disclaimer text
        r'Warning:',           # Warning text
        r'Request Problem',    # Request/processing error text
        r'^\s*Borderline\s+High\s*$',     # Result flag
        r'^\s*Very\s+High\s*$',           # Result flag
        r'^\s*High\s*$',                  # Result flag (standalone)
        r'^\s*Low\s*$',                   # Result flag (standalone)
        r'^\s*Normal\s*$',                # Result flag (standalone)
        r'^\s*Abnormal\s*$',              # Result flag (standalone)
        r'^\s*High\s+Risk\s*$',           # Risk assessment
        r'^\s*Moderate\s+Risk\s*$',       # Risk assessment
        r'^\s*Low\s+Risk\s*$',            # Risk assessment
        r'insufficiency\s+as\s+a\s+level', # Partial sentence artifact
        r'guideline\.\s*JCEM',            # Reference text artifact
        r'between\s*$',                   # Incomplete sentence
        r'^\s*Reference\s+Range\s*$',     # Column header
        r'^\s*Flag\s*$',                  # Column header
        r'^\s*Units?\s*$',                # Column header
        r'^\s*Result\s*$',                # Column header
        r'^\s*Test\s*$',                  # Column header
        r'^\s*Component\s*$',             # Column header
        r'^\s*Status\s*$',                # Column header
    ]

    # Patterns for pagination text that gets mixed with unit names
    PAGINATION_PATTERNS = [
        r'\s*page\s+\d+\s+of\s+\d+',      # "Page 1 of 2", "page 2 of 3"
        r'\s*\d+\s+of\s+\d+',             # "1 of 2", "2 of 3" 
        r'\s*p\.\s*\d+\s*/\s*\d+',        # "p. 1/2", "p.2/3"
        r'\s*\(\s*\d+\s*/\s*\d+\s*\)',    # "(1/2)", "(2/3)"
    ]

    def _is_instructional_text(self, text: str) -> bool:
        """
        Check if text matches any instructional/skip patterns.
        
        Args:
            text: Text to check against patterns
            
        Returns:
            bool: True if text matches any instructional pattern
        """
        if not text:
            return False
            
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in self.INSTRUCTIONAL_SKIP_PATTERNS)

    def _clean_pagination_text(self, text: str) -> str:
        """Remove pagination text patterns from extracted text."""
        if not text:
            return text
            
        cleaned_text = text
        for pattern in self.PAGINATION_PATTERNS:
            cleaned_text = re.sub(pattern, '', cleaned_text, flags=re.IGNORECASE)
        
        return cleaned_text.strip()
    
    def _is_valid_test_name(self, test_name: str) -> bool:
        """Check if a test name is valid (not instructional text or invalid artifacts)."""
        if not test_name or len(test_name) < 3:
            return False
        
        if self._is_instructional_text(test_name):
            return False
        
        invalid_artifacts = [
            'borderline high', 'high', 'low', 'normal', 'abnormal', 'very high',
            'high risk', 'moderate risk', 'low risk', 'risk',
            'result', 'flag', 'units', 'reference', 'interval',
            'component', 'status', 'test', 'range',
            'insufficiency', 'guideline', 'jcem', 'between',
            'previous', 'current', 'date', 'collected'
        ]
        
        return not any(artifact in test_name.lower() for artifact in invalid_artifacts)

    def __init__(self):
        """Initialize PDF parser with regex patterns for data extraction."""
        self.numeric_pattern = re.compile(r'^[<>]?[\d.]+$')
        self.unit_pattern = re.compile(r'^[A-Za-z/%\-\(\).]+$')
        self.range_pattern = re.compile(r'-|>|<')
        self.code_pattern = re.compile(r'([^;()]{5,})\s*\([0-9]+\)')
        self.test_result_pattern = re.compile(r'\d+\.?\d*\s*(mg/dL|mmol/L|g/dL|%|x10E\d|uIU/mL)')
        self.date_patterns = [
            re.compile(r'(\d{1,2}/\d{1,2}/\d{4})'),
            re.compile(r'(\d{4}-\d{2}-\d{2})'),
            re.compile(r'(\w+\s+\d{1,2},?\s+\d{4})'),
            re.compile(r'(\d{1,2}-\w{3}-\d{4})'),
        ]
        self.physician_patterns = [
            re.compile(r'(?:physician|doctor|dr\.?|md)\s*:?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]*)*)', re.IGNORECASE),
            re.compile(r'([A-Z][a-z]+\s+[A-Z][a-z]+),?\s*(?:MD|M\.D\.)', re.IGNORECASE),
            re.compile(r'Dr\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]*)*)', re.IGNORECASE),
        ]

    async def parse_pdf_content(self, content: bytes) -> Dict[str, Any]:
        """Parse PDF content and extract lab results using dual parsing strategy."""
        if not content:
            raise ValueError("PDF content is empty")

        try:
            # Try pdfplumber first (better for tables)
            result = self.parse_with_pdfplumber(content)
            if result and result.get('tests'):
                return result

            # Fallback to pypdf text extraction
            text = self.extract_text_from_pdf(content)
            if not text or len(text.strip()) < 50:
                raise ValueError("PDF appears to be empty or contains no readable text")

            result = self.parse_labcorp_report(text)

            # Check if we got any meaningful results
            if not result.get('tests') and not result.get('date_collected'):
                raise ValueError("No lab results or date found - this may not be a compatible lab report")

            return result

        except pypdf.errors.PdfReadError as e:
            raise pypdf.errors.PdfReadError(f"Cannot read PDF file: {str(e)}")
        except Exception as e:
            print(f"Error parsing PDF: {e}")
            raise ValueError(f"PDF parsing failed: {str(e)}")

    def parse_with_pdfplumber(self, content: bytes) -> Dict[str, Any]:
        """Parse PDF using pdfplumber library for structured table extraction."""
        try:
            pypdf_text = self.extract_text_from_pdf(content)
            date_collected = self.extract_date_from_text(pypdf_text)
            physician = self.extract_physician_from_text(pypdf_text)

            with pdfplumber.open(io.BytesIO(content)) as pdf:
                all_tests = []

                ordered_panels = self.extract_ordered_panels(pdf.pages[0]) if pdf.pages else []

                for page_num, page in enumerate(pdf.pages):
                    tables = page.extract_tables()

                    if tables:
                        for table in tables:
                            tests = self.process_table_with_ordered_panels(table, ordered_panels)
                            all_tests.extend(tests)
                    else:
                        page_text = page.extract_text()
                        if page_text:
                            text_tests = self.extract_tests_from_text(page_text, ordered_panels)
                            all_tests.extend(text_tests)

                return {
                    'date_collected': date_collected,
                    'physician': physician,
                    'tests': all_tests,
                    'ordered_panels': ordered_panels,
                    'panels': [],
                    'errors': []
                }

        except Exception as e:
            print(f"pdfplumber parsing failed: {e}")
            return None

    def extract_ordered_panels(self, page) -> List[str]:
        """Extract panel names from the 'Tests Ordered' section."""
        ordered_panels = []

        try:
            page_text = page.extract_text()
            if not page_text:
                return ordered_panels

            lines = page_text.split('\n')

            tests_ordered_section = False
            for i, line in enumerate(lines):
                line = line.strip()

                if 'tests ordered' in line.lower() or 'test ordered' in line.lower():
                    tests_ordered_section = True
                    continue

                if tests_ordered_section:
                    if any(header in line.lower() for header in ['patient', 'specimen', 'physician', 'test', 'result']):
                        if 'test' not in line.lower() or 'result' in line.lower():
                            break

                    panels_in_line = self.extract_panels_from_ordered_line(line)
                    ordered_panels.extend(panels_in_line)

            for line in lines:
                if ';' in line and '(' in line and ')' in line:
                    panels_in_line = self.extract_panels_from_ordered_line(line)
                    ordered_panels.extend(panels_in_line)

            unique_panels = []
            for panel in ordered_panels:
                if panel not in unique_panels:
                    unique_panels.append(panel)

            if not unique_panels:
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    # Skip instructional text that shouldn't be panel names
                    if self._is_instructional_text(line):
                        continue

                    if (len(line) > 10 and
                        not re.search(r'\d+\.?\d*\s*(mg/dL|mmol/L|g/dL|%|x10E\d|uIU/mL)', line) and
                        ('panel' in line.lower() or 'cbc' in line.lower() or 'count' in line.lower() or
                         'metabolic' in line.lower() or 'lipid' in line.lower() or 'hepatitis' in line.lower())):

                        clean_line = re.sub(r'\s+', ' ', line).strip()
                        if clean_line not in unique_panels:
                            unique_panels.append(clean_line)
            return unique_panels

        except Exception as e:
            return ordered_panels

    def extract_panels_from_ordered_line(self, line: str) -> List[str]:
        """Extract panel names from a single line in the Tests Ordered section."""
        panels = []

        # Pattern: Panel Name (code); Panel Name (code);
        # Example: "CBC With Differential/Platelet (005009); Comp. Metabolic Panel (14) (322000);"

        if ';' in line:
            # Split by semicolon for multiple panels
            parts = line.split(';')
            for part in parts:
                panel = self.extract_single_panel_from_text(part.strip())
                if panel:
                    panels.append(panel)
        else:
            # Single panel in the line
            panel = self.extract_single_panel_from_text(line)
            if panel:
                panels.append(panel)

        return panels

    def extract_single_panel_from_text(self, text: str) -> Optional[str]:
        """Extract a single panel name from text"""
        if not text or len(text) < 5:
            return None

        # Skip instructional text that shouldn't be panel names
        if self._is_instructional_text(text):
            return None

        # Pattern: Panel Name (code)
        # Extract everything before the last parentheses that contains numbers
        match = re.match(r'([^()]+)\s*\([0-9]+\)', text)
        if match:
            panel_name = match.group(1).strip()
            if len(panel_name) > 5:  # Must be substantial text
                return panel_name

        # If no parentheses with numbers, check if it's a descriptive panel name
        if len(text) > 10 and not re.search(r'\d+', text):
            # Could be a panel name without a code
            return text.strip()

        return None

    def process_table_with_ordered_panels(self, table: List[List[str]], ordered_panels: List[str]) -> List[Dict[str, Any]]:
        """Process a table using ordered panels to determine test panel assignments"""
        tests = []

        if not table or len(table) < 2:
            return tests

        # Find header row and determine column structure
        header_row = None
        for i, row in enumerate(table):
            if row and any(cell and ('test' in cell.lower() or 'result' in cell.lower() or 'reference' in cell.lower()) for cell in row if cell):
                header_row = i
                break

        if header_row is None:
            header_row = 0

        headers = table[header_row] if header_row < len(table) else []


        # Only process tables that look like test results tables
        if not self.is_test_results_table(headers):
            return tests

        # Process data rows and match with ordered panels
        current_panel = None

        for row in table[header_row + 1:]:
            if not row or not any(cell and cell.strip() for cell in row):
                continue

            # Check if this row is a panel header from our ordered panels
            panel_name = self.match_row_to_ordered_panel(row, ordered_panels)
            if panel_name:
                current_panel = panel_name
                continue

            test = self.process_table_row(row, headers)
            if test:

                # Associate test with current panel
                if current_panel:
                    test['panel_name'] = current_panel
                else:
                    # If no current panel, try to infer from ordered panels
                    inferred_panel = self.infer_panel_from_test_name(test.get('name', ''), ordered_panels)
                    if inferred_panel:
                        test['panel_name'] = inferred_panel
                        current_panel = inferred_panel  # Set as current for subsequent tests

                tests.append(test)

        return tests

    def match_row_to_ordered_panel(self, row: List[str], ordered_panels: List[str]) -> Optional[str]:
        """Check if a table row matches one of the ordered panels"""
        if not row or not ordered_panels:
            return None

        # Get the first non-empty cell
        first_cell = None
        for cell in row:
            if cell and cell.strip():
                # Clean up multi-line text by joining lines and removing extra whitespace
                first_cell = ' '.join(cell.strip().split())
                break

        if not first_cell:
            return None

        # Check if this row contains test result data (if so, it's not a panel header)
        has_numeric_result = any(
            cell and self.numeric_pattern.search(cell.strip())
            for cell in row[1:] if cell
        )

        if has_numeric_result:
            return None

        # Try to match against ordered panels
        for panel in ordered_panels:
            # Exact match
            if first_cell.lower() == panel.lower():
                return panel

            # Partial match (panel name contains the cell text or vice versa)
            if panel.lower() in first_cell.lower() or first_cell.lower() in panel.lower():
                # Make sure it's a substantial match (not just a single word)
                if len(first_cell) > 5:
                    return panel

        return None

    def infer_panel_from_test_name(self, test_name: str, ordered_panels: List[str]) -> Optional[str]:
        """Try to infer which ordered panel a test belongs to based on common medical knowledge"""
        if not test_name or not ordered_panels:
            return None

        test_name_lower = test_name.lower().replace('\n', ' ').replace('  ', ' ').strip()

        # Common test patterns for different panel types
        panel_test_patterns = {
            'cbc': ['wbc', 'rbc', 'hemoglobin', 'hematocrit', 'mcv', 'mch', 'mchc', 'rdw', 'platelets', 'neutrophils', 'lymphs', 'monocytes', 'eos', 'basos', 'platelet'],
            'metabolic': ['glucose', 'bun', 'creatinine', 'egfr', 'sodium', 'potassium', 'chloride', 'co2', 'carbon dioxide', 'albumin', 'protein', 'bilirubin', 'alkaline', 'ast', 'alt'],
            'lipid': ['cholesterol', 'triglycerides', 'hdl', 'ldl', 'vldl'],
            'hepatitis': ['hbsag', 'hcv', 'hep a', 'hep b', 'hepatitis'],
            'thyroid': ['tsh', 'thyroid'],
            'vitamin': ['vitamin', 'b12', 'folate'],
            'hormone': ['testosterone', 'estrogen', 'hormone'],
            'diabetes': ['a1c', 'hemoglobin a1c']
        }

        # Try to match test to panel type, then find corresponding ordered panel
        best_match = None
        best_match_count = 0

        # Check each panel type in order of specificity (most specific first)
        for panel_type, test_patterns in panel_test_patterns.items():
            # Check if the test name matches any pattern in this panel type
            matches = sum(1 for pattern in test_patterns if pattern in test_name_lower)

            if matches > 0:
                # Find the ordered panel that matches this type
                for ordered_panel in ordered_panels:
                    ordered_panel_lower = ordered_panel.lower()

                    # More specific matching logic - check most specific patterns first
                    type_match = False

                    if panel_type == 'metabolic':
                        # Metabolic panels: must contain "metabolic" OR be a CMP
                        if 'metabolic' in ordered_panel_lower or 'cmp' in ordered_panel_lower:
                            type_match = True
                    elif panel_type == 'cbc':
                        # CBC panels: must contain "cbc" OR "blood count" but NOT metabolic
                        if ('cbc' in ordered_panel_lower or 'blood count' in ordered_panel_lower) and 'metabolic' not in ordered_panel_lower:
                            type_match = True
                    elif panel_type == 'lipid':
                        if 'lipid' in ordered_panel_lower:
                            type_match = True
                    elif panel_type == 'hepatitis':
                        if 'hepatitis' in ordered_panel_lower:
                            type_match = True
                    elif panel_type == 'thyroid':
                        if 'thyroid' in ordered_panel_lower:
                            type_match = True
                    elif panel_type == 'vitamin':
                        if 'vitamin' in ordered_panel_lower:
                            type_match = True
                    elif panel_type == 'hormone':
                        if 'hormone' in ordered_panel_lower or 'testosterone' in ordered_panel_lower:
                            type_match = True
                    elif panel_type == 'diabetes':
                        if 'a1c' in ordered_panel_lower:
                            type_match = True

                    if type_match and matches > best_match_count:
                        best_match = ordered_panel
                        best_match_count = matches

        return best_match


    def is_test_results_table(self, headers: List[str]) -> bool:
        """Determine if a table contains test results based on headers"""
        if not headers:
            return False

        normalized_headers = [header.strip().upper() if header else '' for header in headers]

        # Check if we have the basic test results structure
        has_test_column = any('TEST' in header for header in normalized_headers)
        has_result_column = any('RESULT' in header for header in normalized_headers)

        if not (has_test_column and has_result_column):
            return False

        # Additional check: make sure it's not a patient info or specimen table
        patient_info_indicators = ['SPECIMEN', 'PATIENT', 'ACCOUNT', 'CONTROL']
        is_patient_info = any(indicator in ' '.join(normalized_headers) for indicator in patient_info_indicators)

        if is_patient_info:
            return False

        # Check for "Tests Ordered" table (not actual results)
        if len(normalized_headers) == 1 and 'TESTS ORDERED' in normalized_headers[0]:
            return False

        return True

    def process_table_row(self, row: List[str], headers: List[str]) -> Optional[Dict[str, Any]]:
        """Process a single table row using column headers to properly map data"""
        if not row:
            return None

        # Clean the row
        cleaned_row = [cell.strip() if cell else '' for cell in row]

        # Skip empty rows
        if not any(cleaned_row):
            return None

        # Skip rows that are clearly not test results
        first_cell = cleaned_row[0] if cleaned_row else ''
        if self._is_instructional_text(first_cell):
            return None

        # Map columns based on headers if available
        if headers and len(headers) > 0:
            return self.process_row_with_headers(cleaned_row, headers)
        else:
            # Fallback to heuristic method if no headers
            return self.process_row_heuristic(cleaned_row)

    def process_row_with_headers(self, row: List[str], headers: List[str]) -> Optional[Dict[str, Any]]:
        """Process row using column headers to identify data"""
        # Normalize headers for comparison
        normalized_headers = [header.strip().upper() if header else '' for header in headers]

        # Create column mapping
        column_map = {}
        ignored_columns = []
        for i, header in enumerate(normalized_headers):
            if 'TEST' in header:
                column_map['test'] = i
            elif 'RESULT' in header:
                column_map['result'] = i
            elif 'UNIT' in header:
                column_map['unit'] = i
            elif 'REFERENCE' in header or 'INTERVAL' in header:
                column_map['reference'] = i
            # Explicitly ignore FLAG and LAB columns
            elif 'FLAG' in header or 'LAB' in header:
                ignored_columns.append(i)
                continue  # Skip these columns entirely


        # Extract values based on column mapping
        test_name = None
        result = None
        unit = None
        reference_range = None

        # Get test name
        if 'test' in column_map and column_map['test'] < len(row):
            test_name = row[column_map['test']]
            # Clean up multi-line lab names by joining lines and removing extra whitespace
            if test_name:
                test_name = ' '.join(test_name.split())
                test_name = self._clean_pagination_text(test_name)
                
                if not self._is_valid_test_name(test_name):
                    return None

        # Get result
        if 'result' in column_map and column_map['result'] < len(row):
            result = row[column_map['result']]

        # Get unit (ignore FLAG column entirely)
        if 'unit' in column_map and column_map['unit'] < len(row):
            unit = row[column_map['unit']]
            # Clean pagination text that may have been mixed with the unit
            if unit:
                unit = self._clean_pagination_text(unit)

        # Get reference range
        if 'reference' in column_map and column_map['reference'] < len(row):
            reference_range = row[column_map['reference']]

        # If no explicit column mapping worked, try fallback
        if not test_name or not result:
            return self.process_row_heuristic(row)

        # Skip rows that are clearly not test results (apply same patterns as main method)
        if self._is_instructional_text(test_name):
            return None

        # Skip tests with results that indicate cancellation or non-applicable
        if result == "01" and any(keyword in test_name.lower() for keyword in ['immature', 'nrbc', 'comment']):
            return None

        # Apply known unit mappings for common tests
        unit = self.apply_unit_mappings(test_name, unit)

        # Parse numeric result
        numeric_value = self.parse_numeric_result(result)

        # Check if this is a qualitative result
        is_qualitative = self.is_qualitative_result(result)
        result_text = None

        if is_qualitative:
            result_text = self.standardize_qualitative_result(result)
            # For qualitative results, set numeric_value to None
            numeric_value = None

        # Parse reference range
        ref_range = self.parse_reference_range(reference_range or '')

        return {
            'name': test_name,
            'result': result,
            'result_text': result_text,
            'unit': unit or '',
            'reference_range': ref_range,
            'is_numeric': numeric_value is not None,
            'is_qualitative': is_qualitative,
            'numeric_value': numeric_value
        }

    def process_row_heuristic(self, row: List[str]) -> Optional[Dict[str, Any]]:
        """Fallback heuristic method when headers are not available"""
        test_name = None
        result = None
        unit = None
        reference_range = None

        # Note: Flag values are now handled by column mapping, not needed here

        # Simple heuristic: first non-empty cell is usually test name
        for i, cell in enumerate(row):
            if cell and not test_name:
                # Check if this looks like a test name (not a number)
                if not self.numeric_pattern.match(cell):
                    # Clean up multi-line lab names by joining lines and removing extra whitespace
                    test_name = ' '.join(cell.split())
                    test_name = self._clean_pagination_text(test_name)
                    
                    if not self._is_valid_test_name(test_name):
                        test_name = None
                        continue
                    
                    continue

            # Look for numeric result
            if cell and not result and self.numeric_pattern.match(cell):
                result = cell
                continue

            # Look for unit (letters, not numbers)
            if cell and not unit and self.unit_pattern.match(cell):
                unit = cell
                # Clean pagination text that may have been mixed with the unit
                unit = self._clean_pagination_text(unit)
                continue

            # Look for reference range
            if cell and not reference_range and self.range_pattern.search(cell):
                reference_range = cell
                continue

        if not test_name or not result:
            return None

        # Skip tests with results that indicate cancellation or non-applicable
        if result == "01" and any(keyword in test_name.lower() for keyword in ['immature', 'nrbc', 'comment']):
            return None

        # Apply known unit mappings for common tests
        unit = self.apply_unit_mappings(test_name, unit)

        # Parse numeric result
        numeric_value = self.parse_numeric_result(result)

        # Check if this is a qualitative result
        is_qualitative = self.is_qualitative_result(result)
        result_text = None

        if is_qualitative:
            result_text = self.standardize_qualitative_result(result)
            # For qualitative results, set numeric_value to None
            numeric_value = None

        # Parse reference range
        ref_range = self.parse_reference_range(reference_range or '')

        return {
            'name': test_name,
            'result': result,
            'result_text': result_text,
            'unit': unit or '',
            'reference_range': ref_range,
            'is_numeric': numeric_value is not None,
            'is_qualitative': is_qualitative,
            'numeric_value': numeric_value
        }

    def apply_unit_mappings(self, test_name: str, current_unit: str) -> str:
        """Clean and return the unit, removing pagination artifacts and applying known mappings"""
        if current_unit:
            # Clean pagination text that may have been mixed with the unit
            cleaned_unit = self._clean_pagination_text(current_unit)
            if cleaned_unit:
                return cleaned_unit
        
        # Apply known unit mappings for common tests that might not have units in the PDF
        test_name_lower = test_name.lower()
        
        # Common unit mappings for LabCorp tests
        if 'egfr' in test_name_lower:
            return 'mL/min/1.73m²'
        elif 'bun/creatinine' in test_name_lower or 'bun/crea' in test_name_lower:
            return 'ratio'
        elif 'a/g ratio' in test_name_lower:
            return 'ratio'
        elif 'globulin' in test_name_lower and 'total' in test_name_lower:
            return 'g/dL'  # Globulin is typically measured in g/dL
        
        return current_unit or ''
    
    def apply_reference_range_mappings(self, test_name: str, current_range: Dict[str, Any]) -> Dict[str, Any]:
        """Apply known reference ranges for common tests that might not have ranges in the PDF"""
        if current_range and current_range.get('text'):
            return current_range
        
        test_name_lower = test_name.lower()
        
        # Common reference ranges for LabCorp tests
        if 'egfr' in test_name_lower:
            return {
                'low': 90.0,
                'high': None,
                'text': '≥90'
            }
        elif 'bun/creatinine' in test_name_lower or 'bun/crea' in test_name_lower:
            return {
                'low': 8.0,
                'high': 27.0,
                'text': '8-27'
            }
        elif 'a/g ratio' in test_name_lower:
            return {
                'low': 1.2,
                'high': 2.2,
                'text': '1.2-2.2'
            }
        elif 'globulin' in test_name_lower and 'total' in test_name_lower:
            return {
                'low': 1.5,
                'high': 4.5,
                'text': '1.5-4.5'
            }
        
        return current_range or {
            'low': None,
            'high': None,
            'text': None
        }

    def extract_tests_from_text(self, text: str, ordered_panels: List[str]) -> List[Dict[str, Any]]:
        """Extract tests from plain text when no tables are found."""
        tests = []
        lines = text.split('\n')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Look for lines that look like test results
            # Pattern: Test Name [value] [unit] [reference]
            match = re.match(r'^([A-Za-z][A-Za-z\s,\-]+?)\s+([<>]?[\d.]+)(?:\s+([A-Za-z/%\-\(\)]+))?(?:\s+([<>]?[\d.<>-]+(?:\.\d+)?(?:-[<>]?[\d.]+(?:\.\d+)?)?))?$', line)

            if match:
                name = match.group(1).strip()
                # Clean up multi-line lab names by joining lines and removing extra whitespace
                name = ' '.join(name.split())
                name = self._clean_pagination_text(name)
                result = match.group(2).strip()
                unit = match.group(3).strip() if match.group(3) else ''
                unit = self._clean_pagination_text(unit)
                ref_range_text = match.group(4).strip() if match.group(4) else ''

                if not self._is_valid_test_name(name) or re.match(r'^[\d.]+$', name):
                    continue

                numeric_value = self.parse_numeric_result(result)
                ref_range = self.parse_reference_range(ref_range_text)

                test = {
                    'name': name,
                    'result': result,
                    'unit': unit,
                    'reference_range': ref_range,
                    'is_numeric': numeric_value is not None,
                    'numeric_value': numeric_value
                }

                # Try to infer panel from ordered panels
                if ordered_panels:
                    inferred_panel = self.infer_panel_from_test_name(name, ordered_panels)
                    if inferred_panel:
                        test['panel_name'] = inferred_panel

                tests.append(test)

        return tests

    def extract_date_from_text(self, text: str) -> Optional[str]:
        """Extract collection date from text."""

        # Look for "Date/Time Collected" section and extract just the date part
        for pattern in self.date_patterns:
            match = pattern.search(text, re.IGNORECASE | re.DOTALL)
            if match:
                try:
                    date_str = match.group(1)
                    parsed_date = date_parser.parse(date_str)
                    # Return just the date part in ISO format (YYYY-MM-DD)
                    result = parsed_date.date().isoformat()
                    return result
                except Exception as e:
                    continue
        return None

    def extract_physician_from_text(self, text: str) -> Optional[str]:
        """Extract physician from text."""

        # Look for "Physician Name" section and extract the name from the line below
        for pattern in self.physician_patterns:
            match = pattern.search(text, re.IGNORECASE)
            if match:
                physician = match.group(1).strip()                # Clean up common suffixes and prefixes
                physician = re.sub(r'\s+NPI.*$', '', physician, re.IGNORECASE)
                physician = re.sub(r'\s+MD.*$', '', physician, re.IGNORECASE)
                physician = re.sub(r'\s+DO.*$', '', physician, re.IGNORECASE)
                physician = re.sub(r'\s+Dr\.?\s*', '', physician, re.IGNORECASE)
                physician = re.sub(r'^Dr\.?\s+', '', physician, re.IGNORECASE)

                # Skip if it looks like a title, header text, or empty
                skip_values = ['physician', 'provider', 'doctor', 'npi #', 'physician id', 'npi # physician id']
                if len(physician) > 2 and physician.lower() not in skip_values:
                    return physician
        return None

    def parse_reference_range(self, text: str) -> Dict[str, Any]:
        """Parse reference range from text with support for multiple formats."""
        if not text:
            return {'low': None, 'high': None, 'text': ''}

        text = text.strip()

        # Handle different reference range formats
        if '-' in text and not text.startswith('<') and not text.startswith('>'):
            # Format: "5.0-10.0"
            parts = text.split('-')
            if len(parts) == 2:
                try:
                    low = float(parts[0].strip())
                    high = float(parts[1].strip())
                    return {'low': low, 'high': high, 'text': text}
                except ValueError:
                    pass

        elif text.startswith('<'):
            # Format: "<5.0"
            try:
                value = float(text[1:].strip())
                return {'low': None, 'high': value, 'text': text}
            except ValueError:
                pass

        elif text.startswith('>'):
            # Format: ">10.0"
            try:
                value = float(text[1:].strip())
                return {'low': value, 'high': None, 'text': text}
            except ValueError:
                pass

        return {'low': None, 'high': None, 'text': text}

    def parse_numeric_result(self, result: str) -> Optional[float]:
        """Parse numeric result, handling < and > symbols"""
        if not result:
            return None

        # Remove < and > symbols for numeric parsing
        numeric_part = re.sub(r'[<>]', '', result)

        try:
            return float(numeric_part)
        except ValueError:
            return None

    def is_qualitative_result(self, result: str) -> bool:
        """Check if a result is qualitative (text-based)"""
        if not result:
            return False

        result_clean = result.strip().upper()

        # Known qualitative result patterns
        qualitative_patterns = [
            'NEGATIVE', 'POSITIVE', 'NON REACTIVE', 'REACTIVE', 'INDETERMINATE',
            'NOT DETECTED', 'DETECTED', 'BORDERLINE', 'ABNORMAL', 'NORMAL',
            'SATISFACTORY', 'UNSATISFACTORY', 'PRESENT', 'ABSENT',
            'HIGH', 'LOW', 'CRITICAL', 'TOXIC', 'THERAPEUTIC'
        ]

        return any(pattern in result_clean for pattern in qualitative_patterns)

    def standardize_qualitative_result(self, result: str) -> str:
        """Standardize qualitative results to consistent values"""
        if not result:
            return result

        result_clean = result.strip().upper()

        # Standardization mapping
        negative_variants = ['NEGATIVE', 'NON REACTIVE', 'NOT DETECTED', 'ABSENT']
        positive_variants = ['POSITIVE', 'REACTIVE', 'DETECTED', 'PRESENT']
        indeterminate_variants = ['INDETERMINATE', 'BORDERLINE', 'INCONCLUSIVE']

        for variant in negative_variants:
            if variant in result_clean:
                return "Negative"

        for variant in positive_variants:
            if variant in result_clean:
                return "Positive"

        for variant in indeterminate_variants:
            if variant in result_clean:
                return "Indeterminate"

        # Return original if no standardization applies
        return result.strip()

    # Keep the original methods as fallbacks
    def extract_text_from_pdf(self, content: bytes) -> str:
        """Extract text from PDF using pypdf as fallback."""
        try:
            pdf_reader = pypdf.PdfReader(io.BytesIO(content))
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            return text
        except Exception as e:
            print(f"Error extracting text from PDF: {e}")
            return ""

    def parse_labcorp_report(self, text: str) -> Dict[str, Any]:
        """Enhanced parser specifically for official LabCorp reports."""
        # Try to detect if this is an official LabCorp report
        is_official_labcorp = any(pattern in text for pattern in [
            'Patient Report',
            'Date Collected:',
            'Date Received:',
            'Ordering Physician:',
            'Reference Interval'
        ])
        
        if is_official_labcorp:
            return self.parse_official_labcorp_report(text)
        else:
            # Use the original fallback method for non-official reports
            return {
                'date_collected': self.extract_date_from_text(text),
                'physician': self.extract_physician_from_text(text),
                'tests': self.extract_tests_from_text(text, []),
                'panels': [],
                'errors': []
            }
    
    def parse_official_labcorp_report(self, text: str) -> Dict[str, Any]:
        """Parse official LabCorp reports with specific format handling."""
        # Extract collection date specifically for LabCorp format
        collection_date = self.extract_labcorp_collection_date(text)
        
        # Extract provider specifically for LabCorp format
        provider_name = self.extract_labcorp_provider(text)
        
        # Extract tests and panels from LabCorp format
        tests = self.extract_labcorp_tests(text)
        panels = self.extract_labcorp_panels(text)
        
        return {
            'date_collected': collection_date,
            'physician': provider_name,
            'tests': tests,
            'panels': panels,
            'errors': []
        }
    
    def extract_labcorp_collection_date(self, text: str) -> Optional[str]:
        """Extract collection date from LabCorp report."""
        # Look for "Date Collected: MM/DD/YYYY" pattern
        date_pattern = r'Date Collected:\s*(\d{1,2}/\d{1,2}/\d{4})'
        match = re.search(date_pattern, text, re.IGNORECASE)
        if match:
            try:
                date_str = match.group(1)
                parsed_date = date_parser.parse(date_str)
                return parsed_date.strftime('%Y-%m-%d')
            except Exception as e:
                pass
        
        return None
    
    def extract_labcorp_provider(self, text: str) -> Optional[str]:
        """Extract provider name from LabCorp report."""
        # Look for "Ordering Physician: PROVIDER NAME" pattern
        provider_pattern = r'Ordering Physician:\s*([A-Z\s]+?)(?:\n|$)'
        match = re.search(provider_pattern, text, re.IGNORECASE)
        if match:
            provider_name = match.group(1).strip()
            # Clean up the provider name
            if provider_name and len(provider_name) > 1:
                return provider_name
        
        return None
    
    def extract_labcorp_panels(self, text: str) -> List[Dict[str, Any]]:
        """Extract panel information from LabCorp report."""
        panels = []
        panel_names = set()
        
        # Look for clean panel headers that appear as standalone lines
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
                
            # Skip instructional text
            if self._is_instructional_text(line):
                continue
            
            # Look for panel headers that are standalone lines
            # Common LabCorp panel patterns
            if re.match(r'^(Comp\. Metabolic Panel.*\(\d+\))$', line, re.IGNORECASE):
                panel_name = line.strip()
            elif re.match(r'^(Lipid Panel)$', line, re.IGNORECASE):
                panel_name = line.strip()
            elif re.match(r'^(CBC.*Panel.*)$', line, re.IGNORECASE):
                panel_name = line.strip()
            elif re.match(r'^([A-Za-z\s]+Panel)$', line, re.IGNORECASE) and 'panel' in line.lower():
                panel_name = line.strip()
            elif re.match(r'^(Apolipoprotein [A-Z])$', line, re.IGNORECASE):
                panel_name = line.strip()
            else:
                continue
            
            # Clean up the panel name
            panel_name = re.sub(r'\s+', ' ', panel_name)
            
            # Skip panels that contain problematic content
            if (panel_name and panel_name not in panel_names and len(panel_name) > 3 and
                not any(invalid in panel_name.lower() for invalid in ['high', 'low', 'mg/dl', 'ordered items'])):
                panel_names.add(panel_name)
                panels.append({
                    'name': panel_name,
                    'tests': []
                })
        return panels
    
    def extract_labcorp_tests(self, text: str) -> List[Dict[str, Any]]:
        """Extract test results from LabCorp report."""
        tests = []
        lines = text.split('\n')
        current_panel = None
        
        # Define patterns to identify panel headers and test results
        panel_header_patterns = [
            r'^(Comp\. Metabolic Panel[^:\n]*)',
            r'^(Lipid Panel[^:\n]*)',
            r'^([A-Za-z][A-Za-z\s\.,/()]+Panel[^:\n]*)',
            r'^([A-Za-z][A-Za-z\s\.,/()]+\(\d+\))'
        ]
        
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            
            # Check if this line is a panel header
            is_panel_header = False
            for pattern in panel_header_patterns:
                if re.match(pattern, line, re.IGNORECASE):
                    current_panel = re.match(pattern, line, re.IGNORECASE).group(1).strip()
                    current_panel = re.sub(r'\s+', ' ', current_panel)
                    is_panel_header = True
                    break
            
            if is_panel_header:
                continue
            
            # Skip header lines
            if any(header in line.lower() for header in ['test', 'current result', 'reference interval', 'units']):
                continue
                
            # Try to parse as a test result line
            test = self.parse_labcorp_test_line(line)
            if test:
                test['panel_name'] = current_panel if current_panel else 'Unknown Panel'
                tests.append(test)
        
        return tests
    
    def parse_labcorp_test_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a single test result line from LabCorp report."""
        if not line or len(line.strip()) < 3:
            return None
            
        if not self._is_valid_test_name(line):
            return None
            
        # LabCorp format: TestName 01 Value [Flag] PreviousValue Date Unit ReferenceRange
        # Example: "Glucose 01 87 90* 08/19/2022 mg/dL 70-99"
        
        # Pattern to match LabCorp test result lines
        labcorp_pattern = r'^([A-Za-z][A-Za-z\s\.,/()]+?)\s+\d{2}\s+([\d\.]+)\s*([A-Za-z]*)\s+[\d\.\*]*\s+\d{2}/\d{2}/\d{4}\s+([A-Za-z/\d]+)\s+([\d\.\-<>]+)'
        
        match = re.match(labcorp_pattern, line)
        if match:
            test_name = match.group(1).strip()
            result_value = match.group(2)
            flag = match.group(3) if match.group(3) else None
            unit = match.group(4)
            reference_range = match.group(5)
            
            if not self._is_valid_test_name(test_name):
                return None
            
            # Parse numeric result
            try:
                numeric_result = float(result_value)
            except:
                numeric_result = None
            
            # Parse reference range
            ref_data = self.parse_reference_range(reference_range)
            
            # Apply reference range mappings for tests that might not have ranges
            reference_range_obj = {
                'low': ref_data.get('low'),
                'high': ref_data.get('high'),
                'text': reference_range
            }
            reference_range_obj = self.apply_reference_range_mappings(test_name, reference_range_obj)
            
            return {
                'name': test_name,
                'result': numeric_result,
                'result_text': result_value,
                'unit': unit,
                'flag': flag,
                'ref_low': reference_range_obj.get('low'),
                'ref_high': reference_range_obj.get('high'),
                'ref_type': ref_data.get('type', 'range'),
                'ref_value': ref_data.get('value'),
                'reference_range': reference_range_obj
            }
        
        # Try a simpler pattern for lines that don't match the full format
        simple_pattern = r'^([A-Za-z][A-Za-z\s\.,/()]+?)\s+([\d\.]+)\s*([A-Za-z]*)\s'
        match = re.match(simple_pattern, line)
        if match:
            test_name = match.group(1).strip()
            result_value = match.group(2)
            
            if not self._is_valid_test_name(test_name):
                return None
            
            try:
                numeric_result = float(result_value)
            except:
                numeric_result = None
            
            # Apply unit mappings even for simple pattern
            unit = self.apply_unit_mappings(test_name, match.group(3) if match.group(3) else None)
            
            # Apply reference range mappings for tests that might not have ranges in simple pattern
            reference_range_obj = self.apply_reference_range_mappings(test_name, {
                'low': None,
                'high': None,
                'text': None
            })
            
            return {
                'name': test_name,
                'result': numeric_result,
                'result_text': result_value,
                'unit': unit,
                'flag': match.group(3) if match.group(3) else None,
                'ref_low': reference_range_obj.get('low'),
                'ref_high': reference_range_obj.get('high'),
                'ref_type': 'range',
                'ref_value': None,
                'reference_range': reference_range_obj
            }
        
        return None
