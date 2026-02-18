"""
Tests for BaseLambdaHandler class.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from common.base_handler import BaseLambdaHandler


class TestHandler(BaseLambdaHandler):
    """Concrete implementation for testing"""

    def _execute(self, event: dict, context: dict) -> dict:
        """Simple test implementation"""
        if event.get("fail"):
            raise ValueError("Test error")
        return self._success_response({"result": "success"})


def test_base_handler_initialization():
    """Test handler initialization"""
    handler = TestHandler()
    assert handler.logger is not None
    assert handler._hubspot_client is None
    assert handler._pc_client is None


def test_hubspot_client_lazy_initialization():
    """Test HubSpot client is initialized on first access"""
    handler = TestHandler()
    with patch("common.hubspot_client.HubSpotClient") as mock_client:
        client1 = handler.hubspot_client
        client2 = handler.hubspot_client
        # Should only be created once
        assert mock_client.call_count == 1
        assert client1 == client2


def test_pc_client_lazy_initialization():
    """Test Partner Central client is initialized on first access"""
    handler = TestHandler()
    with patch("common.aws_client.get_partner_central_client") as mock_client:
        client1 = handler.pc_client
        client2 = handler.pc_client
        # Should only be created once
        assert mock_client.call_count == 1
        assert client1 == client2


def test_handle_success():
    """Test successful handler execution"""
    handler = TestHandler()
    event = {"test": "data"}
    context = MagicMock()

    result = handler.handle(event, context)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["result"] == "success"


def test_handle_error():
    """Test handler error handling"""
    handler = TestHandler()
    event = {"fail": True}
    context = MagicMock()

    result = handler.handle(event, context)

    assert result["statusCode"] == 500
    body = json.loads(result["body"])
    assert "error" in body
    assert "Test error" in body["error"]


def test_success_response():
    """Test success response formatting"""
    handler = TestHandler()
    data = {"message": "Success", "count": 5}

    response = handler._success_response(data)

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "application/json"
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    body = json.loads(response["body"])
    assert body == data


def test_success_response_custom_status():
    """Test success response with custom status code"""
    handler = TestHandler()
    data = {"created": True}

    response = handler._success_response(data, 201)

    assert response["statusCode"] == 201


def test_error_response():
    """Test error response formatting"""
    handler = TestHandler()

    response = handler._error_response("Something went wrong", 400)

    assert response["statusCode"] == 400
    assert response["headers"]["Content-Type"] == "application/json"
    body = json.loads(response["body"])
    assert body["error"] == "Something went wrong"


def test_parse_webhook_body_json_string():
    """Test parsing JSON string body"""
    handler = TestHandler()
    event = {"body": json.dumps({"objectId": "123", "propertyName": "dealname"})}

    result = handler._parse_webhook_body(event)

    assert result["objectId"] == "123"
    assert result["propertyName"] == "dealname"


def test_parse_webhook_body_base64():
    """Test parsing base64 encoded body"""
    import base64

    handler = TestHandler()
    payload = {"objectId": "456", "propertyName": "amount"}
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
    event = {"body": encoded, "isBase64Encoded": True}

    result = handler._parse_webhook_body(event)

    assert result["objectId"] == "456"
    assert result["propertyName"] == "amount"


def test_parse_webhook_body_already_dict():
    """Test parsing body that's already a dict"""
    handler = TestHandler()
    payload = {"objectId": "789"}
    event = {"body": payload}

    result = handler._parse_webhook_body(event)

    assert result == payload


def test_parse_webhook_body_empty():
    """Test parsing empty body"""
    handler = TestHandler()
    event = {}

    result = handler._parse_webhook_body(event)

    # Empty string body should return empty dict
    assert result == {}
