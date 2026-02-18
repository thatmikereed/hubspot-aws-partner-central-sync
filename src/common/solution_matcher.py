"""
Solution matching logic: auto-discovers and associates multiple solutions
with opportunities based on deal properties, use cases, and industry.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def match_solutions(deal: dict, available_solutions: list) -> list[str]:
    """
    Match a HubSpot deal to one or more Partner Central solutions.
    
    Returns a list of solution IDs (max 10) ranked by relevance.
    
    Matching criteria:
    - aws_use_case property matches solution category
    - Industry alignment
    - Keywords in deal name/description
    - Explicit aws_solution_ids override property
    """
    props = deal.get("properties", {})
    
    # Priority 1: Explicit override
    explicit_ids = props.get("aws_solution_ids", "")
    if explicit_ids:
        return [s.strip() for s in explicit_ids.split(",") if s.strip()][:10]
    
    # Priority 2: Match by use case
    use_case = props.get("aws_use_case", "").lower()
    industry = props.get("industry", "").lower()
    deal_text = f"{props.get('dealname', '')} {props.get('description', '')}".lower()
    
    scored_solutions = []
    
    for solution in available_solutions:
        score = 0
        solution_name = solution.get("Name", "").lower()
        solution_category = solution.get("Category", "").lower()
        solution_id = solution.get("Id", "")
        
        # Skip inactive solutions
        if solution.get("Status") != "Active":
            continue
        
        # Use case matching
        if use_case:
            if use_case in solution_category or use_case in solution_name:
                score += 10
        
        # Industry matching
        if industry:
            if industry in solution_name or industry in solution_category:
                score += 5
        
        # Keyword matching in deal text
        keywords = solution_name.split()
        for keyword in keywords:
            if len(keyword) > 3 and keyword in deal_text:
                score += 2
        
        # Category-based scoring
        if "migration" in use_case and "migration" in solution_category:
            score += 15
        if "database" in use_case and "database" in solution_category:
            score += 15
        if "ai" in use_case or "ml" in use_case:
            if "ai" in solution_category or "machine learning" in solution_category:
                score += 15
        
        if score > 0:
            scored_solutions.append((score, solution_id))
    
    # Sort by score descending, return top 10
    scored_solutions.sort(reverse=True, key=lambda x: x[0])
    solution_ids = [s[1] for s in scored_solutions[:10]]
    
    if solution_ids:
        logger.info("Matched %d solutions for deal %s", len(solution_ids), deal.get("id"))
    
    return solution_ids


def associate_multiple_solutions(
    pc_client,
    opportunity_id: str,
    solution_ids: list[str],
    catalog: str = "AWS"
) -> dict:
    """
    Associate multiple solutions with an opportunity.
    Returns summary of successes and failures.
    """
    results = {"succeeded": [], "failed": []}
    
    for solution_id in solution_ids:
        try:
            pc_client.associate_opportunity(
                Catalog=catalog,
                OpportunityIdentifier=opportunity_id,
                RelatedEntityIdentifier=solution_id,
                RelatedEntityType="Solutions",
            )
            results["succeeded"].append(solution_id)
            logger.info("Associated solution %s with opportunity %s", solution_id, opportunity_id)
        except Exception as exc:
            logger.warning("Failed to associate solution %s: %s", solution_id, exc)
            results["failed"].append({"solutionId": solution_id, "error": str(exc)})
    
    return results


def get_cached_solutions(pc_client, catalog: str = "AWS") -> list:
    """
    Fetch all active solutions. In production, this should be cached
    (e.g., in DynamoDB or ElastiCache) to avoid repeated API calls.
    """
    solutions = []
    next_token = None
    
    while True:
        kwargs = {"Catalog": catalog, "MaxResults": 50}
        if next_token:
            kwargs["NextToken"] = next_token
        
        response = pc_client.list_solutions(**kwargs)
        solutions.extend(response.get("SolutionSummaries", []))
        
        next_token = response.get("NextToken")
        if not next_token:
            break
    
    logger.info("Fetched %d total solutions from Partner Central", len(solutions))
    return solutions
