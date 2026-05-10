"""Custom exception classes for scan handler failures.

The worker's `poll_and_execute` retries any exception up to max_attempts before
marking the scan failed. For some failures (e.g., user provided a domain that
HaloScan has no data for), retrying is pointless — the result will be identical.
Raising `PermanentScanError` skips retries entirely AND signals the UI to hide
the "Retry from where it failed" button.
"""


class PermanentScanError(Exception):
    """Raised by handlers when the failure is not retryable.

    Examples:
    - HaloScan returns no keywords for the requested domain (domain doesn't
      rank on Google FR — running again won't change that)
    - Domain doesn't resolve / 404s permanently
    - User input validation that should have caught earlier

    The worker sees this and:
    1. Sets job.attempts = max_attempts immediately (no retry loop)
    2. Marks scan.status = 'failed', error_message = str(exc)
    3. Sets scan.summary['retryable'] = False so the UI hides the retry button
    """
    pass
