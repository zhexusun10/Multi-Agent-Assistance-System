"""Native contract bounds shared by the request/response models, the controller proposal and
the settings loader (recommendation.max_result_limit may not exceed MAX_RESULT_LIMIT).

The request keeps the main-branch orchestrator contract version 1.0 (plus behavior_context and
cold_start); the response is the recommendation-owned contract 2.0."""

REQUEST_CONTRACT_VERSION = "1.0"
CONTRACT_VERSION = "2.0"  # response
MAX_RESULT_LIMIT = 10
