"""
Lambda handler: Solution Management API

Provides API endpoints to list and search AWS Partner Central solutions,
enabling HubSpot workflows to discover and select solutions for opportunities.

Endpoints:
- GET /solutions - List all available solutions
- GET /solutions/search?q={query} - Search solutions by keyword
- GET /solutions/{solutionId} - Get solution details

This enables:
- Dynamic solution selection in HubSpot workflows
- Search for solutions by keyword (e.g., "database", "machine learning")
- Browse all available solutions with categories and descriptions
"""

import json
import logging
import os
import sys
from typing import Optional

sys.path.insert(0, "/var/task")

from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    """
    Handle solution management API requests.
    
    Routes:
    - GET /solutions - List all solutions
    - GET /solutions/search - Search solutions
    - GET /solutions/{solutionId} - Get solution details
    """
    logger.info("Received solution management request: %s", json.dumps(event, default=str))
    
    try:
        http_method = event.get("httpMethod", "GET")
        path = event.get("path", "")
        path_params = event.get("pathParameters") or {}
        query_params = event.get("queryStringParameters") or {}
        
        # Route to appropriate handler
        if path == "/solutions" or path.endswith("/solutions"):
            if http_method == "GET":
                return _list_solutions(query_params)
        
        elif "/solutions/search" in path:
            if http_method == "GET":
                return _search_solutions(query_params)
        
        elif path_params.get("solutionId"):
            solution_id = path_params["solutionId"]
            if http_method == "GET":
                return _get_solution(solution_id)
        
        return _error_response("Not Found", 404)
        
    except Exception as e:
        logger.error("Error handling solution request: %s", str(e), exc_info=True)
        return _error_response(str(e), 500)


def _list_solutions(query_params: dict) -> dict:
    """
    List all available Partner Central solutions.
    
    Query parameters:
    - category: Filter by category (e.g., "Database", "Analytics")
    - status: Filter by status (default: "Active")
    - limit: Max results to return (default: 100, max: 100)
    - nextToken: Pagination token
    """
    pc_client = get_partner_central_client()
    
    # Build ListSolutions request
    list_params = {
        "Catalog": PARTNER_CENTRAL_CATALOG,
        "MaxResults": min(int(query_params.get("limit", 100)), 100),
    }
    
    if query_params.get("category"):
        list_params["Category"] = [query_params["category"]]
    
    if query_params.get("status"):
        list_params["Status"] = [query_params["status"]]
    else:
        list_params["Status"] = ["Active"]
    
    if query_params.get("nextToken"):
        list_params["NextToken"] = query_params["nextToken"]
    
    logger.info("Listing solutions with params: %s", list_params)
    
    try:
        response = pc_client.list_solutions(**list_params)
        
        # Format response
        solutions = []
        for solution_summary in response.get("SolutionSummaries", []):
            solutions.append({
                "id": solution_summary.get("Id"),
                "name": solution_summary.get("Name"),
                "category": solution_summary.get("Category"),
                "status": solution_summary.get("Status"),
            })
        
        result = {
            "solutions": solutions,
            "count": len(solutions),
        }
        
        if response.get("NextToken"):
            result["nextToken"] = response["NextToken"]
        
        return _success_response(result)
        
    except Exception as e:
        logger.error("Error listing solutions: %s", str(e), exc_info=True)
        return _error_response(f"Failed to list solutions: {str(e)}", 500)


def _search_solutions(query_params: dict) -> dict:
    """
    Search solutions by keyword.
    
    Query parameters:
    - q: Search query (searches name and description)
    - category: Filter by category
    - limit: Max results (default: 50, max: 100)
    
    Note: Partner Central ListSolutions doesn't support text search,
    so we fetch solutions in batches and filter client-side. Search is
    limited to the first 500 solutions (5 batches of 100) to prevent
    excessive API calls. For large catalogs, this may take a few seconds.
    """
    query = query_params.get("q", "").lower()
    if not query:
        return _error_response("Missing required parameter: q", 400)
    
    category_filter = query_params.get("category")
    limit = min(int(query_params.get("limit", 50)), 100)
    
    # Get solutions in batches and filter client-side
    pc_client = get_partner_central_client()
    
    all_solutions = []
    next_token = None
    max_fetches = 5  # Limit to 500 solutions (5 * 100) to prevent excessive API calls
    fetch_count = 0
    
    while fetch_count < max_fetches:
        list_params = {
            "Catalog": PARTNER_CENTRAL_CATALOG,
            "MaxResults": 100,
            "Status": ["Active"],
        }
        
        if category_filter:
            list_params["Category"] = [category_filter]
        
        if next_token:
            list_params["NextToken"] = next_token
        
        response = pc_client.list_solutions(**list_params)
        all_solutions.extend(response.get("SolutionSummaries", []))
        
        next_token = response.get("NextToken")
        fetch_count += 1
        
        if not next_token:
            break
    
    # Filter by search query
    matching_solutions = []
    for solution in all_solutions:
        solution_name = (solution.get("Name") or "").lower()
        
        # Simple keyword matching
        if query in solution_name:
            matching_solutions.append({
                "id": solution.get("Id"),
                "name": solution.get("Name"),
                "category": solution.get("Category"),
                "status": solution.get("Status"),
                "relevanceScore": _calculate_relevance(query, solution),
            })
    
    # Sort by relevance
    matching_solutions.sort(key=lambda x: x["relevanceScore"], reverse=True)
    
    # Apply limit
    matching_solutions = matching_solutions[:limit]
    
    return _success_response({
        "solutions": matching_solutions,
        "count": len(matching_solutions),
        "query": query,
        "totalSolutionsFetched": len(all_solutions),
    })


def _get_solution(solution_id: str) -> dict:
    """Get detailed information about a specific solution."""
    pc_client = get_partner_central_client()
    
    try:
        response = pc_client.get_solution(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=solution_id,
        )
        
        solution = {
            "id": response.get("Id"),
            "arn": response.get("Arn"),
            "name": response.get("Name"),
            "category": response.get("Category"),
            "status": response.get("Status"),
            "description": response.get("Description"),
            "createdDate": response.get("CreatedDate"),
        }
        
        return _success_response(solution)
        
    except pc_client.exceptions.ResourceNotFoundException:
        return _error_response(f"Solution not found: {solution_id}", 404)
    except Exception as e:
        logger.error("Error getting solution: %s", str(e), exc_info=True)
        return _error_response(f"Failed to get solution: {str(e)}", 500)


def _calculate_relevance(query: str, solution: dict) -> int:
    """Calculate relevance score for search results."""
    score = 0
    name = (solution.get("Name") or "").lower()
    
    # Exact match in name
    if query == name:
        score += 100
    
    # Query at start of name
    elif name.startswith(query):
        score += 50
    
    # Query in name
    elif query in name:
        score += 25
    
    # Word match (e.g., "database" matches "AWS Database Migration Service")
    query_words = query.split()
    name_words = name.split()
    for qword in query_words:
        for nword in name_words:
            if qword in nword:
                score += 10
    
    return score


def _success_response(data: dict) -> dict:
    """Return API Gateway success response."""
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # CORS for HubSpot workflows
        },
        "body": json.dumps(data, default=str)
    }


def _error_response(error: str, status_code: int = 500) -> dict:
    """Return API Gateway error response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"error": error})
    }
