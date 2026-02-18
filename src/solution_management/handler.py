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


from common.base_handler import BaseLambdaHandler
from common.aws_client import PARTNER_CENTRAL_CATALOG


class SolutionManagementHandler(BaseLambdaHandler):
    """
    Handles solution management API requests.

    Routes:
    - GET /solutions - List all solutions
    - GET /solutions/search - Search solutions
    - GET /solutions/{solutionId} - Get solution details
    """

    def _execute(self, event: dict, context: dict) -> dict:
        http_method = event.get("httpMethod", "GET")
        path = event.get("path", "")
        path_params = event.get("pathParameters") or {}
        query_params = event.get("queryStringParameters") or {}

        # Route to appropriate handler
        if path == "/solutions" or path.endswith("/solutions"):
            if http_method == "GET":
                return self._list_solutions(query_params)

        elif "/solutions/search" in path:
            if http_method == "GET":
                return self._search_solutions(query_params)

        elif path_params.get("solutionId"):
            solution_id = path_params["solutionId"]
            if http_method == "GET":
                return self._get_solution(solution_id)

        return self._error_response("Not Found", 404)

    def _list_solutions(self, query_params: dict) -> dict:
        """
        List all available Partner Central solutions.

        Query parameters:
        - category: Filter by category (e.g., "Database", "Analytics")
        - status: Filter by status (default: "Active")
        - limit: Max results to return (default: 100, max: 100)
        - nextToken: Pagination token
        """
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

        self.logger.info("Listing solutions with params: %s", list_params)

        try:
            response = self.pc_client.list_solutions(**list_params)

            # Format response
            solutions = []
            for solution_summary in response.get("SolutionSummaries", []):
                solutions.append(
                    {
                        "id": solution_summary.get("Id"),
                        "name": solution_summary.get("Name"),
                        "category": solution_summary.get("Category"),
                        "status": solution_summary.get("Status"),
                    }
                )

            result = {
                "solutions": solutions,
                "count": len(solutions),
            }

            if response.get("NextToken"):
                result["nextToken"] = response["NextToken"]

            return self._success_response(result)

        except Exception as e:
            self.logger.error("Error listing solutions: %s", str(e), exc_info=True)
            return self._error_response(f"Failed to list solutions: {str(e)}", 500)

    def _search_solutions(self, query_params: dict) -> dict:
        """
        Search solutions by keyword.

        Query parameters:
        - q: Search query (searches name and description)
        - category: Filter by category
        - limit: Max results (default: 50, max: 100)

        Performance note: Partner Central ListSolutions doesn't support text search,
        so we fetch solutions in batches and filter client-side. Search is limited
        to the first 500 solutions (5 API calls @ 100 solutions each) to balance
        completeness with performance. Typical response time: 2-8 seconds depending
        on Partner Central API latency.
        """
        query = query_params.get("q", "").lower()
        if not query:
            return self._error_response("Missing required parameter: q", 400)

        category_filter = query_params.get("category")
        limit = min(int(query_params.get("limit", 50)), 100)

        # Get solutions in batches and filter client-side
        all_solutions = []
        next_token = None
        max_fetches = (
            5  # Limit to 500 solutions (5 * 100) to prevent excessive API calls
        )
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

            response = self.pc_client.list_solutions(**list_params)
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
                matching_solutions.append(
                    {
                        "id": solution.get("Id"),
                        "name": solution.get("Name"),
                        "category": solution.get("Category"),
                        "status": solution.get("Status"),
                        "relevanceScore": self._calculate_relevance(query, solution),
                    }
                )

        # Sort by relevance
        matching_solutions.sort(key=lambda x: x["relevanceScore"], reverse=True)

        # Apply limit
        matching_solutions = matching_solutions[:limit]

        return self._success_response(
            {
                "solutions": matching_solutions,
                "count": len(matching_solutions),
                "query": query,
                "totalSolutionsFetched": len(all_solutions),
            }
        )

    def _get_solution(self, solution_id: str) -> dict:
        """Get detailed information about a specific solution."""
        try:
            response = self.pc_client.get_solution(
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

            return self._success_response(solution)

        except self.pc_client.exceptions.ResourceNotFoundException:
            return self._error_response(f"Solution not found: {solution_id}", 404)
        except Exception as e:
            self.logger.error("Error getting solution: %s", str(e), exc_info=True)
            return self._error_response(f"Failed to get solution: {str(e)}", 500)

    def _calculate_relevance(self, query: str, solution: dict) -> int:
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


# Lambda entry point
def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda entry point for solution management handler.

    Args:
        event: API Gateway event with HTTP request
        context: Lambda context

    Returns:
        HTTP response with solutions data
    """
    return SolutionManagementHandler().handle(event, context)
