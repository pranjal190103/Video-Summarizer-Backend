"""Safe, actionable OpenAI API error messages for the UI and worker logs."""


BILLING_LIMIT_CODES = {
    'credit_balance_exhausted',
    'insufficient_quota',
    'organization_spend_limit_exceeded',
    'project_spend_limit_exceeded',
    'organization_usage_limit_exceeded',
}

RATE_LIMIT_CODES = {'rate_limit_exceeded', 'slow_down'}


def openai_error_code(error):
    """Extract the structured API error code without exposing the raw response."""
    code = getattr(error, 'code', None)
    body = getattr(error, 'body', None)
    if not code and isinstance(body, dict):
        details = body.get('error')
        if isinstance(details, dict):
            code = details.get('code')
    return str(code or '').strip().lower()


def openai_rate_limit_message(error):
    code = openai_error_code(error)
    suffix = f' (OpenAI error code: {code})' if code else ''
    if code in BILLING_LIMIT_CODES:
        return (
            'OpenAI billing or usage quota is exhausted'
            f'{suffix}. Check the API organization/project billing, credits, and spend limits before retrying.'
        )
    if code in RATE_LIMIT_CODES:
        return (
            'OpenAI temporarily rate-limited requests'
            f'{suffix}. Wait before retrying and check the project request/token limits.'
        )
    return (
        'OpenAI returned HTTP 429'
        f'{suffix}. Check the API usage, billing, and rate-limit details for the key\'s organization/project.'
    )
