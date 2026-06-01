def passes_naics_filter(naics: str, exclude_naics: set) -> bool:
    """Exclude explicitly blacklisted NAICS codes. All others pass through."""
    return naics not in exclude_naics
