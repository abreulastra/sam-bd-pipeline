def passes_naics_filter(naics: str, exclude_naics: set) -> bool:
    """
    Exclude explicitly blacklisted NAICS codes. All others pass through,
    including opportunities with no NAICS code (blank string).
    """
    if not naics:
        return True  # no NAICS — always include, let the agent decide
    return naics not in exclude_naics
