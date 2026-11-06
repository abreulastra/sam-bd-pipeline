"""`--source all` must skip retired sources; naming one explicitly still runs it."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from email_pipeline.run_email_pipeline import RETIRED_SOURCES, source_wanted

ACTIVE = ("idbbeo", "worldbank", "worldbankcorp", "ungm", "cdmx")


def test_devex_and_developmentaid_are_retired():
    assert RETIRED_SOURCES == {"devex", "developmentaid"}


def test_all_runs_active_sources_but_not_retired_ones():
    for name in ACTIVE:
        assert source_wanted("all", name)
    for name in RETIRED_SOURCES:
        assert not source_wanted("all", name)


def test_explicit_source_runs_even_if_retired():
    assert source_wanted("developmentaid", "developmentaid")
    assert source_wanted("devex", "devex")
    assert not source_wanted("developmentaid", "ungm")
    assert not source_wanted("ungm", "developmentaid")
