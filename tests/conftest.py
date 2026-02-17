"""
Pytest configuration — adds src/ to the path so all modules can be imported.
"""

import sys
import os

# Add the src directory so Lambda modules can be imported without packaging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
