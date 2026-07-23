# NAICS sector prefixes to exclude entirely.
# Any opportunity whose NAICS code starts with one of these prefixes is filtered out.
EXCLUDED_NAICS_PREFIXES = (
    "11",  # Agriculture, Forestry, Fishing and Hunting
    "21",  # Mining, Quarrying, and Oil and Gas Extraction
    "22",  # Utilities
    "23",  # Construction
    "31",  # Manufacturing
    "32",  # Manufacturing
    "33",  # Manufacturing
    "42",  # Wholesale Trade
    "44",  # Retail Trade
    "45",  # Retail Trade
    "48",  # Transportation and Warehousing
    "49",  # Transportation and Warehousing
    "52",  # Finance and Insurance
    "53",  # Real Estate and Rental and Leasing
    "55",  # Management of Companies and Enterprises
    "62",  # Health Care and Social Assistance
    "71",  # Arts, Entertainment, and Recreation
    "72",  # Accommodation and Food Services
)


def passes_naics_filter(naics: str, exclude_naics: set) -> bool:
    """
    Returns True (keep) if the opportunity should be collected.

    Rules:
    - No NAICS code → always keep (let the agent decide)
    - NAICS starts with an excluded sector prefix → filter out
    - NAICS is in the specific exclusion list → filter out
    - Everything else → keep
    """
    if not naics:
        return True  # no NAICS — always include

    if naics.startswith(EXCLUDED_NAICS_PREFIXES):
        return False

    if naics in exclude_naics:
        return False

    return True


def passes_agency_filter(full_parent_path_name: str, exclude_agencies: set) -> bool:
    """
    Returns True (keep) if the opportunity should be collected.

    Excludes opportunities whose top-level department (the first segment of
    fullParentPathName, e.g. "DEPT OF DEFENSE") is in exclude_agencies — for
    departments that buy broadly enough across NAICS codes that the NAICS
    filter alone won't catch them, but where C230 has no realistic fit
    regardless of NAICS.
    """
    if not full_parent_path_name or not exclude_agencies:
        return True

    top_level = full_parent_path_name.split(".")[0].strip()
    return top_level not in exclude_agencies
