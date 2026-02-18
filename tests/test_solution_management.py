"""
Tests for Solution Management API handler.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_pc_client():
    """Mock Partner Central client."""
    with patch('solution_management.handler.get_partner_central_client') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def sample_solutions():
    """Sample Partner Central solutions."""
    return [
        {
            "Id": "S-0000001",
            "Name": "AWS Database Migration Service",
            "Category": "Database",
            "Status": "Active"
        },
        {
            "Id": "S-0000002",
            "Name": "Amazon RDS for MySQL",
            "Category": "Database",
            "Status": "Active"
        },
        {
            "Id": "S-0000003",
            "Name": "AWS Analytics Platform",
            "Category": "Analytics",
            "Status": "Active"
        }
    ]


def test_list_solutions(mock_pc_client, sample_solutions):
    """Test listing all solutions."""
    from solution_management.handler import lambda_handler
    
    mock_pc_client.list_solutions.return_value = {
        "SolutionSummaries": sample_solutions
    }
    
    event = {
        "httpMethod": "GET",
        "path": "/solutions",
        "queryStringParameters": {}
    }
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "solutions" in body
    assert len(body["solutions"]) == 3
    assert body["count"] == 3
    
    # Verify API call
    mock_pc_client.list_solutions.assert_called_once()
    call_kwargs = mock_pc_client.list_solutions.call_args[1]
    assert call_kwargs["Catalog"] == "AWS"
    assert call_kwargs["Status"] == ["Active"]


def test_list_solutions_with_category_filter(mock_pc_client, sample_solutions):
    """Test listing solutions with category filter."""
    from solution_management.handler import lambda_handler
    
    filtered_solutions = [s for s in sample_solutions if s["Category"] == "Database"]
    mock_pc_client.list_solutions.return_value = {
        "SolutionSummaries": filtered_solutions
    }
    
    event = {
        "httpMethod": "GET",
        "path": "/solutions",
        "queryStringParameters": {"category": "Database"}
    }
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert len(body["solutions"]) == 2
    
    # Verify category filter was passed
    call_kwargs = mock_pc_client.list_solutions.call_args[1]
    assert call_kwargs["Category"] == ["Database"]


def test_list_solutions_with_pagination(mock_pc_client, sample_solutions):
    """Test solution listing with pagination."""
    from solution_management.handler import lambda_handler
    
    mock_pc_client.list_solutions.return_value = {
        "SolutionSummaries": sample_solutions,
        "NextToken": "next-page-token"
    }
    
    event = {
        "httpMethod": "GET",
        "path": "/solutions",
        "queryStringParameters": {"limit": "50"}
    }
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "nextToken" in body
    assert body["nextToken"] == "next-page-token"


def test_search_solutions(mock_pc_client, sample_solutions):
    """Test searching solutions by keyword."""
    from solution_management.handler import lambda_handler
    
    # Filter to only database solutions for this test
    database_solutions = [s for s in sample_solutions if "database" in s["Name"].lower()]
    
    mock_pc_client.list_solutions.return_value = {
        "SolutionSummaries": sample_solutions
    }
    
    event = {
        "httpMethod": "GET",
        "path": "/solutions/search",
        "queryStringParameters": {"q": "database"}
    }
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    
    # Should return solutions with "database" in name
    assert len(body["solutions"]) == 2  # Both database solutions
    assert all("database" in s["name"].lower() for s in body["solutions"])
    assert "query" in body
    assert body["query"] == "database"
    
    # Results should have relevance scores
    assert all("relevanceScore" in s for s in body["solutions"])


def test_search_solutions_without_query(mock_pc_client):
    """Test search without query parameter returns error."""
    from solution_management.handler import lambda_handler
    
    event = {
        "httpMethod": "GET",
        "path": "/solutions/search",
        "queryStringParameters": {}
    }
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert "error" in body
    assert "Missing required parameter: q" in body["error"]


def test_search_relevance_scoring(mock_pc_client, sample_solutions):
    """Test that search results are sorted by relevance."""
    from solution_management.handler import lambda_handler
    
    mock_pc_client.list_solutions.return_value = {
        "SolutionSummaries": sample_solutions
    }
    
    event = {
        "httpMethod": "GET",
        "path": "/solutions/search",
        "queryStringParameters": {"q": "aws database"}
    }
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    
    # First result should have higher score (exact match in name)
    if len(body["solutions"]) > 1:
        assert body["solutions"][0]["relevanceScore"] >= body["solutions"][1]["relevanceScore"]


def test_get_solution(mock_pc_client):
    """Test getting a specific solution."""
    from solution_management.handler import lambda_handler
    
    mock_pc_client.get_solution.return_value = {
        "Id": "S-0000001",
        "Arn": "arn:aws:partnercentral-selling:us-east-1:123456789012:solution/S-0000001",
        "Name": "AWS Database Migration Service",
        "Category": "Database",
        "Status": "Active",
        "Description": "Comprehensive database migration solution",
        "CreatedDate": "2024-01-01T00:00:00Z"
    }
    
    event = {
        "httpMethod": "GET",
        "path": "/solutions/S-0000001",
        "pathParameters": {"solutionId": "S-0000001"}
    }
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["id"] == "S-0000001"
    assert body["name"] == "AWS Database Migration Service"
    assert "description" in body
    
    # Verify API call
    mock_pc_client.get_solution.assert_called_once()
    call_kwargs = mock_pc_client.get_solution.call_args[1]
    assert call_kwargs["Catalog"] == "AWS"
    assert call_kwargs["Identifier"] == "S-0000001"


def test_get_solution_not_found(mock_pc_client):
    """Test getting non-existent solution returns 404."""
    from solution_management.handler import lambda_handler
    
    mock_pc_client.get_solution.side_effect = mock_pc_client.exceptions.ResourceNotFoundException(
        {"Error": {"Code": "ResourceNotFoundException"}}, 
        "GetSolution"
    )
    
    event = {
        "httpMethod": "GET",
        "path": "/solutions/S-9999999",
        "pathParameters": {"solutionId": "S-9999999"}
    }
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert "error" in body


def test_cors_headers(mock_pc_client, sample_solutions):
    """Test that CORS headers are present in responses."""
    from solution_management.handler import lambda_handler
    
    mock_pc_client.list_solutions.return_value = {
        "SolutionSummaries": sample_solutions
    }
    
    event = {
        "httpMethod": "GET",
        "path": "/solutions",
        "queryStringParameters": {}
    }
    
    response = lambda_handler(event, None)
    
    assert "headers" in response
    assert "Access-Control-Allow-Origin" in response["headers"]
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"


def test_invalid_route(mock_pc_client):
    """Test that invalid routes return 404."""
    from solution_management.handler import lambda_handler
    
    event = {
        "httpMethod": "GET",
        "path": "/invalid-path",
        "queryStringParameters": {}
    }
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 404


def test_relevance_calculation():
    """Test the relevance score calculation algorithm."""
    from solution_management.handler import _calculate_relevance
    
    # Exact match should score highest
    solution1 = {"Name": "database"}
    assert _calculate_relevance("database", solution1) == 100
    
    # Starts with query should score high
    solution2 = {"Name": "database migration service"}
    score2 = _calculate_relevance("database", solution2)
    assert score2 >= 50
    
    # Contains query should score medium
    solution3 = {"Name": "AWS Database Tools"}
    score3 = _calculate_relevance("database", solution3)
    assert score3 >= 25
    
    # Word match should score lower
    solution4 = {"Name": "Migration Service for DB"}
    score4 = _calculate_relevance("database", solution4)
    assert score4 >= 0
