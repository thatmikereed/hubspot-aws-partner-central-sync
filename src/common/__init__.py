"""
Common modules package initialization.
Ensures proper import paths without sys.path manipulation.
"""

import sys
import os

# Add parent directory to path if running in Lambda
if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    sys.path.insert(0, "/var/task")

__all__ = [
    "base_handler",
    "sync_service",
    "exceptions",
    "hubspot_client",
    "aws_client",
    "mappers",
]
