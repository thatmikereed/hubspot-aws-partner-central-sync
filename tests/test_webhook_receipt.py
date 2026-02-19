"""
Tests for webhook receipt handler.
"""

import json
from unittest.mock import Mock, patch, MagicMock

import pytest

from webhook_receipt.handler import WebhookReceiptHandler, lambda_handler


@pytest.fixture
def mock_sqs():
    """Mock SQS client."""
    with patch('webhook_receipt.handler.boto3') as mock_boto3:
        mock_sqs_client = MagicMock()
        mock_boto3.client.return_value = mock_sqs_client
        
        # Mock successful SendMessage response
        mock_sqs_client.send_message.return_value = {
            'MessageId': 'test-message-id-123',
            'MD5OfMessageBody': 'abc123',
        }
        
        yield mock_sqs_client


@pytest.fixture
def handler(mock_sqs, monkeypatch):
    """Create a webhook receipt handler with mocked dependencies."""
    monkeypatch.setenv('SQS_QUEUE_URL', 'https://sqs.us-east-1.amazonaws.com/123456789/test-queue.fifo')
    monkeypatch.setenv('HUBSPOT_ACCESS_TOKEN', 'test-token')
    monkeypatch.setenv('LOG_LEVEL', 'INFO')
    
    with patch('webhook_receipt.handler.BaseLambdaHandler.hubspot_client', new_callable=lambda: Mock()):
        handler = WebhookReceiptHandler()
        # Create a proper mock for the hubspot_client
        mock_client = Mock()
        mock_client.verify_webhook_signature = Mock(return_value=True)
        handler._hubspot_client = mock_client
        return handler


def test_handler_initialization(monkeypatch):
    """Test handler initializes correctly."""
    monkeypatch.setenv('SQS_QUEUE_URL', 'https://sqs.us-east-1.amazonaws.com/123456789/test-queue.fifo')
    
    with patch('webhook_receipt.handler.boto3'):
        handler = WebhookReceiptHandler()
        assert handler.queue_url == 'https://sqs.us-east-1.amazonaws.com/123456789/test-queue.fifo'


def test_handler_initialization_missing_queue_url(monkeypatch):
    """Test handler raises error if SQS_QUEUE_URL is missing."""
    monkeypatch.delenv('SQS_QUEUE_URL', raising=False)
    
    with patch('webhook_receipt.handler.boto3'):
        with pytest.raises(ValueError, match="SQS_QUEUE_URL environment variable is required"):
            WebhookReceiptHandler()


def test_process_single_webhook_event(handler, mock_sqs):
    """Test processing a single webhook event."""
    event = {
        'body': json.dumps([
            {
                'subscriptionType': 'deal.creation',
                'objectId': '12345',
                'propertyName': 'dealname',
                'propertyValue': 'Test Deal #AWS',
            }
        ]),
        'headers': {},
    }
    
    result = handler._execute(event, {})
    
    assert result['statusCode'] == 200
    body = json.loads(result['body'])
    assert body['enqueued'] == 1
    assert body['errors'] == 0
    assert body['processingTimeMs'] < 1000  # Should be fast
    
    # Verify SQS was called
    assert mock_sqs.send_message.called
    call_args = mock_sqs.send_message.call_args
    assert 'QueueUrl' in call_args[1]
    assert 'MessageBody' in call_args[1]
    assert 'MessageGroupId' in call_args[1]
    assert 'MessageDeduplicationId' in call_args[1]


def test_process_multiple_webhook_events(handler, mock_sqs):
    """Test processing multiple webhook events."""
    event = {
        'body': json.dumps([
            {
                'subscriptionType': 'deal.creation',
                'objectId': '12345',
            },
            {
                'subscriptionType': 'deal.propertyChange',
                'objectId': '67890',
                'propertyName': 'amount',
            },
            {
                'subscriptionType': 'company.propertyChange',
                'objectId': '11111',
                'propertyName': 'industry',
            },
        ]),
        'headers': {},
    }
    
    result = handler._execute(event, {})
    
    assert result['statusCode'] == 200
    body = json.loads(result['body'])
    assert body['enqueued'] == 3
    assert body['errors'] == 0
    
    # Verify SQS was called 3 times
    assert mock_sqs.send_message.call_count == 3


def test_process_empty_webhook_body(handler):
    """Test handling empty webhook body."""
    event = {
        'body': json.dumps([]),
        'headers': {},
    }
    
    result = handler._execute(event, {})
    
    assert result['statusCode'] == 200
    body = json.loads(result['body'])
    assert body['message'] == 'No events to process'


def test_enqueue_event_with_correct_fifo_attributes(handler, mock_sqs):
    """Test that events are enqueued with correct FIFO attributes."""
    event = {
        'body': json.dumps([
            {
                'subscriptionType': 'deal.creation',
                'objectId': '12345',
            }
        ]),
        'headers': {},
    }
    
    handler._execute(event, {})
    
    # Get the SQS send_message call
    call_args = mock_sqs.send_message.call_args[1]
    
    # Verify FIFO attributes
    assert call_args['MessageGroupId'] == '12345'  # Should be object_id
    assert 'MessageDeduplicationId' in call_args
    
    # Verify message body is valid JSON
    message_body = json.loads(call_args['MessageBody'])
    assert message_body['object_id'] == '12345'
    assert message_body['event_type'] == 'deal.creation'
    assert message_body['event_source'] == 'hubspot'


def test_webhook_signature_verification_success(handler, monkeypatch):
    """Test webhook signature verification when secret is configured."""
    monkeypatch.setenv('HUBSPOT_WEBHOOK_SECRET', 'test-secret')
    
    event = {
        'body': json.dumps([{'subscriptionType': 'deal.creation', 'objectId': '12345'}]),
        'headers': {
            'X-HubSpot-Signature-v3': 'test-signature',
        },
    }
    
    handler._hubspot_client.verify_webhook_signature.return_value = True
    
    result = handler._execute(event, {})
    
    assert result['statusCode'] == 200
    handler._hubspot_client.verify_webhook_signature.assert_called_once()


def test_webhook_signature_verification_failure(handler, monkeypatch):
    """Test webhook signature verification failure."""
    monkeypatch.setenv('HUBSPOT_WEBHOOK_SECRET', 'test-secret')
    
    event = {
        'body': json.dumps([{'subscriptionType': 'deal.creation', 'objectId': '12345'}]),
        'headers': {
            'X-HubSpot-Signature-v3': 'invalid-signature',
        },
    }
    
    handler._hubspot_client.verify_webhook_signature.return_value = False
    
    # Use handle() instead of _execute() to catch the exception
    result = handler.handle(event, {})
    
    # Should return error
    assert result['statusCode'] == 500
    body = json.loads(result['body'])
    assert 'error' in body
    assert 'signature' in body['error'].lower()


def test_webhook_without_signature_secret(handler, monkeypatch):
    """Test webhook processing without signature secret (no verification)."""
    monkeypatch.delenv('HUBSPOT_WEBHOOK_SECRET', raising=False)
    
    # Reset the mock to track calls
    handler._hubspot_client.verify_webhook_signature.reset_mock()
    
    event = {
        'body': json.dumps([{'subscriptionType': 'deal.creation', 'objectId': '12345'}]),
        'headers': {},
    }
    
    result = handler._execute(event, {})
    
    # Should succeed without verification
    assert result['statusCode'] == 200
    # Should not have called verify_webhook_signature
    assert not handler._hubspot_client.verify_webhook_signature.called


def test_partial_failure_still_returns_success(handler, mock_sqs):
    """Test that partial failures don't prevent response."""
    # Make second message fail
    mock_sqs.send_message.side_effect = [
        {'MessageId': 'msg-1'},
        Exception("SQS error"),
        {'MessageId': 'msg-3'},
    ]
    
    event = {
        'body': json.dumps([
            {'subscriptionType': 'deal.creation', 'objectId': '1'},
            {'subscriptionType': 'deal.creation', 'objectId': '2'},
            {'subscriptionType': 'deal.creation', 'objectId': '3'},
        ]),
        'headers': {},
    }
    
    result = handler._execute(event, {})
    
    # Should return success even with partial failure
    assert result['statusCode'] == 200
    body = json.loads(result['body'])
    assert body['enqueued'] == 2
    assert body['errors'] == 1
    assert body['errorDetails'] is not None


def test_lambda_handler_entry_point(mock_sqs, monkeypatch):
    """Test the lambda_handler entry point."""
    monkeypatch.setenv('SQS_QUEUE_URL', 'https://sqs.us-east-1.amazonaws.com/123456789/test-queue.fifo')
    monkeypatch.setenv('HUBSPOT_ACCESS_TOKEN', 'test-token')
    
    event = {
        'body': json.dumps([{'subscriptionType': 'deal.creation', 'objectId': '12345'}]),
        'headers': {},
    }
    
    with patch('webhook_receipt.handler.WebhookReceiptHandler') as MockHandler:
        mock_handler_instance = Mock()
        mock_handler_instance.handle.return_value = {'statusCode': 200, 'body': '{}'}
        MockHandler.return_value = mock_handler_instance
        
        result = lambda_handler(event, {})
        
        assert result['statusCode'] == 200
        MockHandler.assert_called_once()
        mock_handler_instance.handle.assert_called_once_with(event, {})


def test_response_time_under_100ms(handler, mock_sqs):
    """Test that response time is fast (<100ms for typical case)."""
    event = {
        'body': json.dumps([
            {'subscriptionType': 'deal.creation', 'objectId': '12345'}
        ]),
        'headers': {},
    }
    
    result = handler._execute(event, {})
    
    body = json.loads(result['body'])
    # Should be very fast with mocked SQS
    assert body['processingTimeMs'] < 100


def test_parse_webhook_body_with_base64(handler):
    """Test parsing base64-encoded webhook body."""
    import base64
    
    webhook_data = [{'subscriptionType': 'deal.creation', 'objectId': '12345'}]
    encoded_body = base64.b64encode(json.dumps(webhook_data).encode()).decode()
    
    event = {
        'body': encoded_body,
        'isBase64Encoded': True,
        'headers': {},
    }
    
    result = handler._execute(event, {})
    
    assert result['statusCode'] == 200
    body = json.loads(result['body'])
    assert body['enqueued'] == 1
