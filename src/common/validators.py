"""
Input validation and sanitization utilities for security.

Validates external inputs to prevent injection attacks and ensure
data conforms to expected formats before sending to AWS or HubSpot APIs.
"""

import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Maximum lengths for various fields to prevent DoS via large inputs
MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 10000
MAX_NAME_LENGTH = 255
MAX_EMAIL_LENGTH = 254
MAX_URL_LENGTH = 2048

# Regex patterns for validation
EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
URL_PATTERN = re.compile(
    r'^https?://'  # http:// or https://
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
    r'localhost|'  # localhost
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
    r'(?::\d+)?'  # optional port
    r'(?:/?|[/?]\S+)$', re.IGNORECASE
)

# Pattern for AWS Partner Central IDs (alphanumeric with hyphens, colons, slashes, dots for ARNs)
PC_ID_PATTERN = re.compile(r'^[A-Za-z0-9\-:/.]+$')

# Pattern for HubSpot IDs (numeric)
HUBSPOT_ID_PATTERN = re.compile(r'^\d+$')


def sanitize_string(value: Any, max_length: int = None, field_name: str = "field") -> str:
    """
    Sanitize a string value for safe use in APIs.
    
    Args:
        value: The value to sanitize
        max_length: Maximum allowed length (None for no limit)
        field_name: Name of the field for logging
        
    Returns:
        Sanitized string
        
    Raises:
        ValueError: If input is invalid
    """
    if value is None:
        return ""
    
    # Convert to string
    str_value = str(value).strip()
    
    # Check length
    if max_length and len(str_value) > max_length:
        logger.warning(
            f"{field_name} exceeds maximum length {max_length}, truncating from {len(str_value)} chars"
        )
        str_value = str_value[:max_length]
    
    # Remove control characters (ASCII 0-31 except newline, carriage return, tab)
    # Keep printable characters (ASCII 32 and above) plus allowed whitespace
    allowed_control_chars = {'\n', '\r', '\t'}
    str_value = ''.join(
        char for char in str_value 
        if ord(char) >= 32 or char in allowed_control_chars
    )
    
    return str_value


def validate_email(email: str) -> Optional[str]:
    """
    Validate and sanitize an email address.
    
    Returns:
        Sanitized email or None if invalid
    """
    if not email:
        return None
    
    email = sanitize_string(email, MAX_EMAIL_LENGTH, "email").lower()
    
    if not EMAIL_PATTERN.match(email):
        logger.warning(f"Invalid email format: {email[:50]}...")
        return None
    
    return email


def validate_url(url: str) -> Optional[str]:
    """
    Validate and sanitize a URL.
    
    Returns:
        Sanitized URL or None if invalid
    """
    if not url:
        return None
    
    url = sanitize_string(url, MAX_URL_LENGTH, "url")
    
    if not URL_PATTERN.match(url):
        logger.warning(f"Invalid URL format: {url[:100]}...")
        return None
    
    return url


def validate_partner_central_id(pc_id: str, field_name: str = "Partner Central ID") -> str:
    """
    Validate a Partner Central resource ID.
    
    Args:
        pc_id: The ID to validate (can be ARN or short ID)
        field_name: Name of the field for error messages
        
    Returns:
        The validated ID
        
    Raises:
        ValueError: If ID is invalid
    """
    if not pc_id:
        raise ValueError(f"{field_name} is required")
    
    pc_id = str(pc_id).strip()
    
    if not PC_ID_PATTERN.match(pc_id):
        raise ValueError(
            f"{field_name} contains invalid characters. "
            "Only alphanumeric, hyphens, colons, slashes, and dots are allowed."
        )
    
    if len(pc_id) > 512:  # Increased to accommodate ARNs
        raise ValueError(f"{field_name} is too long (max 512 characters)")
    
    return pc_id


def validate_hubspot_id(hs_id: str, field_name: str = "HubSpot ID") -> str:
    """
    Validate a HubSpot object ID.
    
    Args:
        hs_id: The ID to validate
        field_name: Name of the field for error messages
        
    Returns:
        The validated ID
        
    Raises:
        ValueError: If ID is invalid
    """
    if not hs_id:
        raise ValueError(f"{field_name} is required")
    
    hs_id = str(hs_id).strip()
    
    if not HUBSPOT_ID_PATTERN.match(hs_id):
        raise ValueError(f"{field_name} must be numeric")
    
    return hs_id


def validate_amount(amount: Any) -> Optional[float]:
    """
    Validate and convert a monetary amount.
    
    Returns:
        Validated amount as float or None if invalid
    
    Notes:
        Maximum amount is set to 1 trillion USD to prevent overflow issues
        and catch obvious data entry errors. This limit is configurable if
        needed for specific use cases (e.g., very large enterprise deals).
    """
    if amount is None or amount == "":
        return None
    
    try:
        amount_float = float(amount)
        
        if amount_float < 0:
            logger.warning("Negative amount provided, converting to None")
            return None
        
        # Sanity limit to prevent overflow and catch data entry errors
        # Adjust this if your business regularly handles deals > $1T
        MAX_REASONABLE_AMOUNT = 1_000_000_000_000  # 1 trillion USD
        if amount_float > MAX_REASONABLE_AMOUNT:
            logger.warning(f"Amount {amount_float} exceeds reasonable limit of {MAX_REASONABLE_AMOUNT}")
            return None
        
        return amount_float
    
    except (ValueError, TypeError) as e:
        logger.warning(f"Invalid amount value: {amount} - {e}")
        return None


def sanitize_deal_name(deal_name: str) -> str:
    """
    Sanitize a deal/opportunity name.
    
    Ensures the name is safe for both HubSpot and Partner Central APIs.
    """
    return sanitize_string(deal_name, MAX_TITLE_LENGTH, "deal_name")


def sanitize_description(description: str) -> str:
    """
    Sanitize a description field.
    
    Allows longer content but still limits to prevent DoS.
    """
    return sanitize_string(description, MAX_DESCRIPTION_LENGTH, "description")
