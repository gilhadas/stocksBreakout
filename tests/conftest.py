# conftest.py — shared pytest fixtures and CI guards
#
# CRITICAL: ib_insync must be stubbed BEFORE any project module is imported,
# because market_data.py does `from ib_insync import IB, Stock, util` at the
# top level, and scanner.py imports market_data. Without this stub, all tests
# that touch scanner.py or market_data.py crash immediately in CI (no IB Gateway).
#
# This conftest.py is loaded by pytest before any test file is collected,
# so the stub is in place before any import happens.

import sys
from unittest.mock import MagicMock

# Stub the entire ib_insync namespace so CI runners without IB Gateway can import
if 'ib_insync' not in sys.modules:
    _ib_mock = MagicMock()
    _ib_mock.IB = MagicMock
    _ib_mock.Stock = MagicMock
    _ib_mock.util = MagicMock()
    sys.modules['ib_insync'] = _ib_mock

# Load test environment variables (dummy values — no real secrets in CI)
try:
    from dotenv import load_dotenv
    load_dotenv("tests/.env.test", override=False)
except ImportError:
    pass  # python-dotenv not installed — env vars simply won't be set
