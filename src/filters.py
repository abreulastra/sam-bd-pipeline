def passes_naics_filter(naics: str, exclude_naics: set) -> bool:
    """Keep only NAICS codes starting with 5 that are not explicitly excluded."""
    return naics.startswith("5") and naics not in exclude_naics
