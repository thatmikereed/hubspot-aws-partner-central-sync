"""
Pytest configuration — adds src/ to the path so all modules can be imported.
"""

import sys
import os

# Add the src directory so Lambda modules can be imported without packaging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Pre-import handler modules so @patch decorators can resolve dotted paths
import hubspot_to_partner_central.handler  # noqa: F401
import partner_central_to_hubspot.handler  # noqa: F401
import eventbridge_events.handler  # noqa: F401
