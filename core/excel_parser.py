import re
import pandas as pd
from typing import Dict, List, Any, Optional

COURT_MAP = {
    # Texas
    "txed": "txed", "e.d. tex.": "txed", "eastern district of texas": "txed", "texas eastern": "txed",
    "txwd": "txwd", "w.d. tex.": "txwd", "western district of texas": "txwd", "texas western": "txwd",
    "txnd": "txnd", "n.d. tex.": "txnd", "northern district of texas": "txnd", "texas northern": "txnd",
    "txsd": "txsd", "s.d. tex.": "txsd", "southern district of texas": "txsd", "texas southern": "txsd",
    # Delaware
    "ded": "ded", "d. del.": "ded", "district of delaware": "ded", "delaware": "ded",
    # California
    "cand": "cand", "ndca": "cand", "n.d. cal.": "cand", "northern district of california": "cand",
    "cacd": "cacd", "cdca": "cacd", "c.d. cal.": "cacd", "central district of california": "cacd",
    "casd": "casd", "sdca": "casd", "s.d. cal.": "casd", "southern district of california": "casd",
    "caed": "caed", "edca": "caed", "e.d. cal.": "caed", "eastern district of california": "caed",
    # New York
    "nysd": "nysd", "sdny": "nysd", "s.d.n.y.": "nysd", "southern district of new york": "nysd",
    "nyed": "nyed", "edny": "nyed", "e.d.n.y.": "nyed", "eastern district of new york": "nyed",
    "nynd": "nynd", "ndny": "nynd", "n.d.n.y.": "nynd", "northern district of new york": "nynd",
    "nywd": "nywd", "wdny": "nywd", "w.d.n.y.": "nywd", "western district of new york": "nywd",
    # Illinois
    "ilnd": "ilnd", "ndil": "ilnd", "n.d. ill.": "ilnd", "northern district of illinois": "ilnd",
    "ilsd": "ilsd", "sdil": "ilsd", "s.d. ill.": "ilsd", "southern district of illinois": "ilsd",
    "ilcd": "ilcd", "cdil": "ilcd", "c.d. ill.": "ilcd", "central district of illinois": "ilcd",
    # New Jersey & Florida & Virginia & Washington & Massachusetts & Minnesota
    "njd": "njd", "d.n.j.": "njd", "district of new jersey": "njd", "new jersey": "njd",
    "flsd": "flsd", "sdfl": "flsd", "s.d. fla.": "flsd", "southern district of florida": "flsd",
    "flmd": "flmd", "mdfl": "flmd", "m.d. fla.": "flmd", "middle district of florida": "flmd",
    "flnd": "flnd", "ndfl": "flnd", "n.d. fla.": "flnd", "northern district of florida": "flnd",
    "vaed": "vaed", "edva": "vaed", "e.d. va.": "vaed", "eastern district of virginia": "vaed",
    "vawd": "vawd", "wdva": "vawd", "w.d. va.": "vawd", "western district of virginia": "vawd",
    "wawd": "wawd", "wdwa": "wawd", "w.d. wash.": "wawd", "western district of washington": "wawd",
    "waed": "waed", "edwa": "waed", "e.d. wash.": "waed", "eastern district of washington": "waed",
    "mad": "mad", "d. mass.": "mad", "district of massachusetts": "mad", "massachusetts": "mad",
    "mnd": "mnd", "d. minn.": "mnd", "district of minnesota": "mnd", "minnesota": "mnd",
    "ohnd": "ohnd", "ndoh": "ohnd", "n.d. ohio": "ohnd", "northern district of ohio": "ohnd",
    "ohsd": "ohsd", "sdoh": "ohsd", "s.d. ohio": "ohsd", "southern district of ohio": "ohsd",
    "cafc": "cafc", "ca-fed": "cafc", "federal circuit": "cafc", "us court of appeals for the federal circuit": "cafc"
}

def normalize_court_id(court_raw: Any) -> str:
    """Standardizes court/jurisdiction string to CourtListener identifier."""
    if not court_raw or pd.isna(court_raw):
        return ""
    cleaned = str(court_raw).strip().lower()
    cleaned = re.sub(r"[,\.]", "", cleaned)
    
    if cleaned in COURT_MAP:
        return COURT_MAP[cleaned]
    
    for key, val in COURT_MAP.items():
        if re.sub(r"[,\.]", "", key) in cleaned:
            return val
        district = re.fullmatch(r"(eastern|western|northern|southern|central|middle) district of (.+)", key)
        if district and f"{district[2]} {district[1]}" in cleaned:
            return val
            
    return re.sub(r"[^a-z0-9]", "", cleaned)

def normalize_case_number(case_raw: Any) -> Dict[str, str]:
    if not case_raw or pd.isna(case_raw):
        return {"original": "", "base": "", "clean": ""}
    
    orig = str(case_raw).strip()
    # Preserve the office in spreadsheet forms such as 1-25-cv-01337.
    searchable = re.sub(r"\b(\d+)-(\d{2}-[a-zA-Z]{2,4}-)", r"\1:\2", orig)
    m = re.search(r"(\d+:)?\d{2}-[a-zA-Z]{2,4}-\d{1,6}", searchable, re.IGNORECASE)
    if m:
        base = m.group(0)
    else:
        base = orig.split()[0] if orig else ""
        base = re.sub(r"-[A-Z]{2,5}(-[A-Z]{2,5})?$", "", base)
    
    return {
        "original": orig,
        "base": base,
        "clean": re.sub(r"\s+", "", orig)
    }

def map_column_names(df_columns: List[str]) -> Dict[str, str]:
    mapping = {}
    
    # Priority ordered list of fields and regex
    patterns = [
        ("pacermonitor_url", [r"pacer\s*monitor.*(?:url|link)", r"^docket\s*(?:url|link)$"]),
        ("case_number", [r"^casenumber$", r"case\s*no", r"case\s*number", r"docket"]),
        ("date_filed", [r"date\s*of\s*filing", r"filing\s*date", r"^date_filed$", r"^filed$"]),
        ("plaintiff", [r"^plaintiff", r"^claimant"]),
        ("defendants", [r"^defendent", r"^defendants?", r"^respondent"]),
        ("jurisdiction", [r"^jurisdiction", r"^court", r"^district", r"^venue"]),
        ("num_patents", [r"no\s*\.?\s*of\s*patents", r"number\s*of\s*patents", r"patent\s*count", r"^num_patents$"]),
        ("patent_numbers", [r"patent\s*numbers?", r"^patents?$", r"^patent_no$"]),
        ("patent_title", [r"technology\s*of\s*patents\s*title", r"^technology", r"^title", r"patent\s*title"])
    ]
    
    used_columns = set()
    for field, regex_list in patterns:
        for col in df_columns:
            if col in used_columns:
                continue
            col_clean = col.strip().lower()
            matched = False
            for reg in regex_list:
                if re.search(reg, col_clean):
                    mapping[field] = col
                    used_columns.add(col)
                    matched = True
                    break
            if matched:
                break

    return mapping

def parse_excel_file(filepath: str) -> Dict[str, Any]:
    if filepath.endswith(".csv"):
        df = pd.read_csv(filepath)
    else:
        df = pd.read_excel(filepath)
    
    col_mapping = map_column_names(list(df.columns))
    
    records = []
    for idx, row in df.iterrows():
        if all(pd.isna(v) or not str(v).strip() for v in row):
            continue
        case_col = col_mapping.get("case_number")
        case_val = row[case_col] if case_col and case_col in row else ""
        norm_case = normalize_case_number(case_val)
        
        filed_col = col_mapping.get("date_filed")
        filed_val = row[filed_col] if filed_col and filed_col in row else ""
        if pd.notna(filed_val) and hasattr(filed_val, "strftime"):
            filed_val = filed_val.strftime("%Y-%m-%d")
        else:
            filed_val = str(filed_val) if pd.notna(filed_val) else ""
            
        court_col = col_mapping.get("jurisdiction")
        court_raw = row[court_col] if court_col and court_col in row else ""
        court_code = normalize_court_id(court_raw)
        
        plaintiff_col = col_mapping.get("plaintiff")
        plaintiff = str(row[plaintiff_col]) if plaintiff_col and pd.notna(row.get(plaintiff_col)) else ""
        
        defendants_col = col_mapping.get("defendants")
        defendants = str(row[defendants_col]) if defendants_col and pd.notna(row.get(defendants_col)) else ""
        
        num_pat_col = col_mapping.get("num_patents")
        num_pat = str(row[num_pat_col]) if num_pat_col and pd.notna(row.get(num_pat_col)) else ""
        
        pat_nums_col = col_mapping.get("patent_numbers")
        pat_nums = str(row[pat_nums_col]) if pat_nums_col and pd.notna(row.get(pat_nums_col)) else ""
        
        tech_col = col_mapping.get("patent_title")
        tech = str(row[tech_col]) if tech_col and pd.notna(row.get(tech_col)) else ""

        records.append({
            "pacermonitor_url": str(row[col_mapping["pacermonitor_url"]]).strip() if col_mapping.get("pacermonitor_url") and pd.notna(row.get(col_mapping["pacermonitor_url"])) else "",
            "id": idx + 1,
            "original_case_number": norm_case["original"],
            "case_number": norm_case["base"] or norm_case["original"],
            "date_filed_input": filed_val,
            "plaintiff": plaintiff,
            "defendants": defendants,
            "jurisdiction_input": str(court_raw),
            "court_code": court_code,
            "num_patents": num_pat,
            "patent_numbers": pat_nums,
            "patent_title": tech,
            "raw_row": {str(k): ("" if pd.isna(v) else str(v)) for k, v in row.items()}
        })
        
    return {
        "total": len(records),
        "columns": list(df.columns),
        "column_mapping": col_mapping,
        "cases": records
    }
