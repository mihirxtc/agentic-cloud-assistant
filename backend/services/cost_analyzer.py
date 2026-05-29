import json
from datetime import date

import boto3
from dateutil.relativedelta import relativedelta

from services.llm_service import (
    chat_with_anthropic,
    chat_with_groq,
    chat_with_ollama,
)


def get_aws_session(credentials: dict) -> boto3.Session:
    """Build a per-request boto3 Session from an explicit credentials dict."""
    return boto3.Session(
        aws_access_key_id=credentials.get("aws_access_key_id"),
        aws_secret_access_key=credentials.get("aws_secret_access_key"),
        region_name=credentials.get(
            "aws_region", credentials.get("region", "us-east-1")
        ),
    )


def get_current_month_cost(credentials: dict = None) -> dict:
    """
    Retrieve the total AWS spend for the current calendar month.

    Calls the AWS Cost Explorer API with MONTHLY granularity
    from the first day of the current month to today.

    Returns:
        dict with keys:
          amount   (float)  — total spend rounded to 2 decimal places
          currency (str)    — e.g. "USD"
          period   (str)    — human-readable date range e.g. "2026-03-01 to 2026-03-21"

        On any error, returns safe defaults with an "error" key added.
        The error key allows the /cost endpoint to surface the reason
        without crashing.
    """
    try:
        ce = (
            get_aws_session(credentials).client("ce", region_name="us-east-1")
            if credentials
            else boto3.client("ce", region_name="us-east-1")
        )

        start = date.today().replace(day=1)
        end = date.today()

        # Cost Explorer requires Start < End. On the 1st of the month start==end,
        # so advance end by one day to satisfy the constraint.
        if end <= start:
            from datetime import timedelta
            end = start + timedelta(days=1)

        response = ce.get_cost_and_usage(
            TimePeriod={
                "Start": start.strftime("%Y-%m-%d"),
                "End": end.strftime("%Y-%m-%d"),
            },
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
        )

        results = response.get("ResultsByTime", [])
        if not results:
            return {"amount": 0.0, "currency": "USD", "period": f"{start} to {end}"}
        result = results[0]
        amount = result["Total"]["UnblendedCost"]["Amount"]
        currency = result["Total"]["UnblendedCost"]["Unit"]

        return {
            "amount": round(float(amount), 2),
            "currency": currency,
            "period": f"{start} to {end}",
        }

    except Exception as e:
        return {
            "amount": 0.0,
            "currency": "USD",
            "period": "unavailable",
            "error": str(e),
        }


def get_monthly_trend(months: int = 3, credentials: dict = None) -> list:
    """
    Retrieve the total AWS spend for each of the last N calendar months.

    Uses relativedelta for month arithmetic because timedelta works in days.
    Subtracting 90 days from March 1st gives January 1st (correct), but
    subtracting 90 days from March 15th gives December 15th (wrong).
    relativedelta(months=3) always navigates by full calendar months.

    Parameters:
        months (int): How many months of history to retrieve. Default is 3.

    Returns:
        list of dicts, each with:
          month    (str)   — "YYYY-MM" e.g. "2026-01"
          amount   (float) — spend for that month
          currency (str)   — e.g. "USD"

        Sorted chronologically (oldest first).
        Returns an empty list on any error.
    """
    try:
        ce = (
            get_aws_session(credentials).client("ce", region_name="us-east-1")
            if credentials
            else boto3.client("ce", region_name="us-east-1")
        )

        today = date.today()

        start = (today - relativedelta(months=months)).replace(day=1)
        end = today

        response = ce.get_cost_and_usage(
            TimePeriod={
                "Start": start.strftime("%Y-%m-%d"),
                "End": end.strftime("%Y-%m-%d"),
            },
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
        )

        trend = []

        for item in response.get("ResultsByTime", []):
            month = item["TimePeriod"]["Start"][:7]
            amount = item["Total"]["UnblendedCost"]["Amount"]
            currency = item["Total"]["UnblendedCost"]["Unit"]

            trend.append(
                {
                    "month": month,
                    "amount": round(float(amount), 2),
                    "currency": currency,
                }
            )

        trend.sort(key=lambda x: x["month"])

        return trend

    except Exception:
        return []


def get_cost_by_service(credentials: dict = None) -> list:
    """
    Retrieve AWS spend for the current month, broken down by service.

    Calls Cost Explorer with GroupBy SERVICE so we get one row per AWS
    service (e.g. Amazon EC2, Amazon S3, AWS Lambda).

    Services with zero spend are excluded — AWS returns every service
    the account has ever used, most of which will be $0.00 for any
    given month. Showing those would clutter the UI.

    Returns:
        list of dicts sorted by amount descending (most expensive first):
          service  (str)   — AWS service name e.g. "Amazon EC2"
          amount   (float) — spend for this month
          currency (str)   — e.g. "USD"

        Returns an empty list on any error.
    """
    try:
        ce = (
            get_aws_session(credentials).client("ce", region_name="us-east-1")
            if credentials
            else boto3.client("ce", region_name="us-east-1")
        )

        start = date.today().replace(day=1)
        end = date.today()

        if end <= start:
            from datetime import timedelta
            end = start + timedelta(days=1)

        response = ce.get_cost_and_usage(
            TimePeriod={
                "Start": start.strftime("%Y-%m-%d"),
                "End": end.strftime("%Y-%m-%d"),
            },
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )

        services = []
        results = response.get("ResultsByTime", [])
        if not results:
            return []

        for group in results[0].get("Groups", []):
            service = group["Keys"][0]
            amount = group["Metrics"]["UnblendedCost"]["Amount"]
            currency = group["Metrics"]["UnblendedCost"]["Unit"]

            rounded_amount = round(float(amount), 2)
            if rounded_amount == 0.0:
                continue

            services.append(
                {
                    "service": service,
                    "amount": rounded_amount,
                    "currency": currency,
                }
            )

        services.sort(key=lambda x: x["amount"], reverse=True)

        return services

    except Exception:
        return []


def detect_cost_anomaly(monthly_trend: list) -> dict:
    """
    Flag a cost anomaly if the most recent month's spend is 20% or more
    above the previous month's spend.

    Why 20%?
      This is a common threshold in FinOps tooling. A small increase
      (e.g. 5–10%) is expected growth; 20%+ suggests an unexpected
      change such as a forgotten running resource or a billing error.

    Parameters:
        monthly_trend (list): Output of get_monthly_trend() — a list of
                              monthly spend dicts sorted chronologically.
                              Needs at least 2 entries to compare.

    Returns:
        dict with keys:
          detected   (bool)  — True if spend increased by 20%+
          message    (str)   — Human-readable explanation
          percentage (float) — Actual % change (0.0 if not detected)
    """
    if len(monthly_trend) < 2:
        return {"detected": False, "message": "", "percentage": 0.0}

    try:
        current = float(monthly_trend[-1]["amount"])
        previous = float(monthly_trend[-2]["amount"])

        if previous == 0:
            return {"detected": False, "message": "", "percentage": 0.0}

        pct_change = ((current - previous) / previous) * 100

        if pct_change >= 20:
            return {
                "detected": True,
                "message": (
                    f"Spend increased by {pct_change:.1f}% compared to last month "
                    f"(${previous:.2f} → ${current:.2f})."
                ),
                "percentage": round(pct_change, 1),
            }

        return {"detected": False, "message": "", "percentage": 0.0}

    except Exception:
        return {"detected": False, "message": "", "percentage": 0.0}


async def get_cost_insights_llm(
    current_month: dict,
    monthly_trend: list,
    by_service: list,
    anomaly: dict,
    model: str = "groq",
    api_key: str = None,
) -> str:
    """
    Send cost data to an LLM and return a plain-English cost summary.

    If there is no cost data at all (amount is 0 and no services), we
    return a canned explanation rather than calling the LLM — there is
    nothing useful the LLM can say about an empty dataset.

    Parameters:
        current_month (dict): Output of get_current_month_cost()
        monthly_trend (list): Output of get_monthly_trend()
        by_service    (list): Output of get_cost_by_service()
        anomaly       (dict): Output of detect_cost_anomaly()
        model         (str) : Which LLM to use — groq, anthropic, ollama
        api_key       (str) : Optional user-supplied API key override

    Returns:
        str: Plain-English cost summary from the LLM, or a fallback
             string if the LLM call fails or no data is available.
             Never raises an exception to the caller.
    """
    amount = current_month.get("amount", 0)
    has_error = "error" in current_month
    no_data = amount == 0.0 and len(by_service) == 0

    if no_data or has_error:
        error_detail = current_month.get("error", "")
        return (
            "No cost data available. This may be because AWS Cost Explorer "
            "is not enabled for this account, or billing access is not "
            "configured for the current IAM user. "
            + (f"AWS error: {error_detail}" if error_detail else "")
        ).strip()

    prompt = f"""You are an AWS cost optimisation expert.

Here is the current cost data for this AWS account:

Current month spend: ${current_month.get("amount", 0)} USD
Period: {current_month.get("period", "unknown")}

Monthly trend (last 3 months):
{json.dumps(monthly_trend, indent=2)}

Cost breakdown by service:
{json.dumps(by_service, indent=2)}

Anomaly detected: {anomaly.get("detected", False)}
{anomaly.get("message", "")}

Please provide:
1. A 2-sentence executive summary of current spend
2. The top 2 cost drivers and why they cost what they do
3. Three specific, actionable cost optimisation recommendations
4. Whether the spending trend looks healthy or concerning

Be specific, reference actual service names and dollar amounts."""

    try:
        if model == "anthropic":
            return await chat_with_anthropic(prompt, {}, [], api_key)
        elif model == "ollama":
            return await chat_with_ollama(prompt, {}, [], api_key)
        else:
            return await chat_with_groq(prompt, {}, [], api_key)

    except Exception as e:
        return f"Could not generate cost summary: {str(e)}"
