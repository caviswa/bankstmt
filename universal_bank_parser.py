import pdfplumber
import pandas as pd
import re
from typing import List, Dict, Any, Optional

class UniversalBankParser:
    """
    Universal Bank Statement Parser prioritizing Spatial Data, Dynamic Anchoring,
    Multi-line Merging and Mathematical Self-Healing.
    Designed for Indian bank statement idiosyncrasies (SBI, HDFC, ICICI, etc.)
    """
    def __init__(self, x_tolerance_percentage: float = 0.15, y_tolerance_pts: float = 5.0):
        self.x_tolerance_percentage = x_tolerance_percentage # 15% drift allowed
        self.y_tolerance_pts = y_tolerance_pts # Vertical alignment grouping tolerance
        
        self.header_synonyms = {
            'Date': ['date', 'txn date', 'value date', 'transaction date'],
            'Narration': ['narration', 'description', 'particulars', 'remarks', 'details', 'transaction particulars'],
            'Cheque No': ['cheque', 'chq', 'ref', 'instrument', 'referance', 'reference'],
            'Debit': ['withdrawal', 'dr', 'debit', 'withdrawals'],
            'Credit': ['deposit', 'cr', 'credit', 'deposits'],
            'Balance': ['balance', 'bal']
        }

    def _clean_number(self, num_str: str) -> Optional[float]:
        """Cleans and converts a string to a float."""
        if not num_str: return None
        # Remove commas, currency symbols, Dr/Cr suffixes
        cleaned = re.sub(r'[^0-9\.\-]', '', num_str)
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _find_headers(self, words: List[Dict]) -> Dict[str, Dict[str, float]]:
        """
        Scans words to identify the dynamic column boundaries (x0, x1) for the page.
        Returns a mapping like: {'Date': {'x0': 10, 'x1': 50}, ...}
        """
        page_headers = {}
        for h_key, synonyms in self.header_synonyms.items():
            for word in words:
                text_clean = re.sub(r'[^a-zA-Z]', '', word['text']).lower()
                if any(syn in text_clean for syn in synonyms):
                    # Found a header! Expand its boundaries slightly for safety later.
                    width = word['x1'] - word['x0']
                    tolerance = width * self.x_tolerance_percentage
                    
                    if h_key not in page_headers:
                        page_headers[h_key] = {
                            'x0': max(0, word['x0'] - tolerance),
                            'x1': word['x1'] + tolerance,
                            'top': word['top'],
                            'bottom': word['bottom']
                        }
                    else:
                        # Some headers have multiple words (e.g. "Value Date"), expand existing box
                        page_headers[h_key]['x0'] = min(page_headers[h_key]['x0'], word['x0'] - tolerance)
                        page_headers[h_key]['x1'] = max(page_headers[h_key]['x1'], word['x1'] + tolerance)
                        page_headers[h_key]['bottom'] = max(page_headers[h_key]['bottom'], word['bottom'])
        
        return page_headers

    def _group_words_by_row(self, words: List[Dict], header_bottom: float) -> List[List[Dict]]:
        """
        Groups words into rows based on vertical proximity.
        Filters out words that are conceptually part of the header.
        """
        transaction_words = [w for w in words if w['top'] > header_bottom]
        transaction_words.sort(key=lambda w: w['top'])

        rows = []
        current_row = []
        current_y = None

        for w in transaction_words:
            if current_y is None:
                current_y = w['top']
                current_row.append(w)
            elif abs(w['top'] - current_y) <= self.y_tolerance_pts:
                current_row.append(w)
            else:
                rows.append(current_row)
                current_row = [w]
                current_y = w['top']
                
        if current_row:
            rows.append(current_row)
            
        return rows

    def _assign_words_to_columns(self, row_words: List[Dict], headers: Dict[str, Dict[str, float]]) -> Dict[str, str]:
        """
        Maps the row's words to the closest header column based on X-coordinates.
        Handles pixel-level bleeding by nearest distance instead of strict containment.
        """
        assigned = {k: [] for k in self.header_synonyms.keys()}
        
        for w in row_words:
            best_col = None
            min_dist = float('inf')
            
            w_center = (w['x0'] + w['x1']) / 2.0
            
            for col_name, bounds in headers.items():
                col_center = (bounds['x0'] + bounds['x1']) / 2.0
                dist = abs(w_center - col_center)

                # Prioritize explicit containment first
                if bounds['x0'] <= w_center <= bounds['x1']:
                    best_col = col_name
                    break
                elif dist < min_dist:
                    min_dist = dist
                    best_col = col_name

            if best_col:
                # To distinguish adjacent Bleed vs normal column placement:
                # If a word is really far from the column, it might be heavily unaligned, but this generic pass will group them.
                assigned[best_col].append(w)

        # Sort words inside columns left-to-right to reconstruct sentences
        for col_name in assigned:
            assigned[col_name].sort(key=lambda x: x['x0'])
            assigned[col_name] = " ".join([w['text'] for w in assigned[col_name]]).strip()
            
        return assigned

    def _fallback_spatial_scan_for_missing_math(self, row_words: List[Dict]) -> Dict[str, Optional[float]]:
        """
        When mathematical validation fails, scan all available words in the specific ROW
        for hidden floating point numbers (ignoring strict x0, x1 assignments).
        """
        possible_numbers = []
        for w in row_words:
            val = self._clean_number(w['text'])
            if val is not None:
                possible_numbers.append(val)
        
        # We can't automatically know which is debit/credit, but we know balance is usually the largest or right-most
        # For simplicity, just return the detected numbers list to be evaluated.
        # Advanced implementations would rebuild the permutation.
        return possible_numbers

    def _process_page(self, words: List[Dict], running_balance: float = 0.0) -> List[Dict]:
        """
        Process a single page of words and applies multiline merging and math healing.
        """
        headers = self._find_headers(words)
        
        if not headers:
            return [] # No table on this page

        # Find where headers end vertically to start reading data
        header_bottom = max([h['bottom'] for h in headers.values()])
        
        raw_rows = self._group_words_by_row(words, header_bottom)
        
        processed_transactions = []
        
        for raw_row in raw_rows:
            parsed_row = self._assign_words_to_columns(raw_row, headers)
            
            # Check for multi-line narration merging (No Date, No Balance, but has Narration)
            if not parsed_row['Date'] and parsed_row['Narration'] and not parsed_row['Balance']:
                if processed_transactions:
                    processed_transactions[-1]['Narration'] += " " + parsed_row['Narration']
                continue

            # It's a conceptual new row. Let's do Mathematical validation.
            # Clean numbers first
            debit = self._clean_number(parsed_row['Debit']) or 0.0
            credit = self._clean_number(parsed_row['Credit']) or 0.0
            balance_str = parsed_row['Balance']
            
            if not parsed_row['Date'] and debit == 0.0 and credit == 0.0:
                continue # Random header or footer junk row

            current_balance = self._clean_number(balance_str)
            
            # If running balance is completely zero, assume starting statement balance
            if running_balance == 0.0 and current_balance is not None:
                pass # Accept it. In a real system we'd parse opening balance specifically.
                
            # Perform Math Healing Validation
            if running_balance != 0.0 and current_balance is not None:
                expected_balance = round(running_balance - debit + credit, 2)
                if round(current_balance, 2) != expected_balance:
                    # MATH BROKE! Spatial drift caused values to misalign.
                    # Fallback Rescan
                    loose_numbers = self._fallback_spatial_scan_for_missing_math(raw_row)
                    
                    found_debit, found_credit = debit, credit
                    # Simple heuristic fallback (Real life requires permutation testing)
                    for num in loose_numbers:
                        if round(running_balance - num, 2) == round(current_balance, 2):
                            found_debit = num
                            found_credit = 0.0
                            break
                        elif round(running_balance + num, 2) == round(current_balance, 2):
                            found_credit = num
                            found_debit = 0.0
                            break
                    
                    # Apply healed values
                    parsed_row['Debit'] = found_debit
                    parsed_row['Credit'] = found_credit
                    # If still broken, we keep it as is, but log error internally

            # Set the running balance for next row
            if current_balance is not None:
                running_balance = current_balance
            
            processed_transactions.append(parsed_row)
            
        return processed_transactions

    def parse_pdf(self, file_path: str) -> pd.DataFrame:
        """
        Main entry point for PDF parsing using pdfplumber spatial extraction.
        """
        all_transactions = []
        running_balance = 0.0

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words()
                page_data = self._process_page(words, running_balance=running_balance)
                
                all_transactions.extend(page_data)
                
                # Update running balance carrying over pages
                if page_data:
                    last_valid_bal = self._clean_number(page_data[-1].get('Balance'))
                    if last_valid_bal is not None:
                        running_balance = last_valid_bal

        return pd.DataFrame(all_transactions)

    def parse_spreadsheet(self, file_path: str) -> pd.DataFrame:
        """
        Implements row-by-row logical grouping for Excel and CSV files.
        Handles dynamic header discovery (ignoring top junk rows),
        multi-line narrations, and applies the same mathematical self-healing.
        """
        lower_path = str(file_path).lower()
        if lower_path.endswith('.csv'):
            df = pd.read_csv(file_path, header=None)
        else:
            # openpyxl handles .xlsx, xlrd handles .xls 
            # (pandas intelligently dispatches base on the engine/extension)
            df = pd.read_excel(file_path, header=None)
        
        # 1. Find Header Row
        header_row_idx = -1
        col_mapping = {} # maps standard header key (e.g. 'Date') to excel column index (0, 1, 2...)
        
        for idx, row in df.iterrows():
            found_cols = {}
            for col_idx, cell_value in enumerate(row):
                if pd.isna(cell_value): continue
                cell_str = str(cell_value).lower().strip()
                
                for h_key, synonyms in self.header_synonyms.items():
                    # Exact or substring match for synonyms
                    if any(syn in cell_str for syn in synonyms):
                        # Avoid overwriting if we hit a second column with same synonym 
                        # (though that's rare, we'll keep the first found)
                        if h_key not in found_cols:
                            found_cols[h_key] = col_idx
                        break
            
            # If we found at least 3 standard headers, we probably found the header row
            if len(found_cols) >= 3:
                header_row_idx = idx
                col_mapping = found_cols
                break
                
        if header_row_idx == -1:
            return pd.DataFrame() # No identifiable headers found
            
        # 2. Process Rows
        processed_transactions = []
        running_balance = 0.0
        
        for idx, row in df.loc[header_row_idx + 1:].iterrows():
            # Extract basic cell values mapped to our standard columns
            parsed_row = {k: "" for k in self.header_synonyms.keys()}
            
            for h_key, col_idx in col_mapping.items():
                val = row[col_idx]
                if not pd.isna(val):
                    parsed_row[h_key] = str(val).strip()
                    
            # Check for Multi-line narration merging 
            # (No Date, No Balance, but has Narration)
            if not parsed_row['Date'] and parsed_row['Narration'] and not parsed_row['Balance']:
                if processed_transactions:
                    processed_transactions[-1]['Narration'] += " " + parsed_row['Narration']
                continue
                
            debit = self._clean_number(parsed_row['Debit']) or 0.0
            credit = self._clean_number(parsed_row['Credit']) or 0.0
            balance_str = parsed_row['Balance']
            
            # If completely empty of meaningful data, skip
            if not parsed_row['Date'] and debit == 0.0 and credit == 0.0 and not balance_str:
                continue 
                
            current_balance = self._clean_number(balance_str)
            
            # If running balance is zero, grab the current as opening
            if running_balance == 0.0 and current_balance is not None:
                pass
                
            # Perform Mathematical Validation (Self-Healing)
            if running_balance != 0.0 and current_balance is not None:
                expected_balance = round(running_balance - debit + credit, 2)
                if round(current_balance, 2) != expected_balance:
                    # In Excel, spatial drift doesn't exist, but values might be mixed up 
                    # or placed in adjacent merged cells causing columns to shift.
                    # We can scan the entire raw row for loose numbers to see if we can heal the math.
                    loose_numbers = []
                    for cell_val in row:
                        val = self._clean_number(str(cell_val)) if not pd.isna(cell_val) else None
                        if val is not None:
                            loose_numbers.append(val)
                            
                    found_debit, found_credit = debit, credit
                    
                    # Try to find a number that perfectly balances the equation
                    for num in loose_numbers:
                        if round(running_balance - num, 2) == round(current_balance, 2):
                            found_debit = num
                            found_credit = 0.0
                            break
                        elif round(running_balance + num, 2) == round(current_balance, 2):
                            found_credit = num
                            found_debit = 0.0
                            break
                    
                    parsed_row['Debit'] = found_debit
                    parsed_row['Credit'] = found_credit
                    
            if current_balance is not None:
                running_balance = current_balance
                
            processed_transactions.append(parsed_row)
            
        return pd.DataFrame(processed_transactions)
