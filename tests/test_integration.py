"""Integration test -- verify runner.py imports and runs without crashing."""

from unittest.mock import patch, MagicMock

import store


def test_runner_imports():
    """Verify all modules import cleanly."""
    import config
    import scraper
    import disruption_scanner
    import notifier
    import runner


def test_full_scan_with_mocked_network():
    """Run a full scan cycle with all network calls mocked."""
    store.init_db()

    with patch("scraper._fetch") as mock_scraper_fetch, \
         patch("disruption_scanner._fetch") as mock_scanner_fetch, \
         patch("notifier.requests") as mock_notifier_requests:

        mock_scraper_fetch.return_value = None
        mock_scanner_fetch.return_value = None

        import runner
        result = runner.run_scan()

        assert isinstance(result, dict)
        assert "total" in result
        assert "errors" in result
