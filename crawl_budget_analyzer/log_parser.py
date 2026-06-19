"""Parse Nginx/Apache access logs into a DataFrame of bot requests."""

import re
from pathlib import Path

import advertools as adv
import pandas as pd

# Combined Apache/Nginx log_format. Override via `log_format` if the
# site uses a custom format (e.g. JSON logs, extra fields).
COMBINED_LOG_FORMAT = (
    r'(?P<client>\S+) \S+ \S+ \[(?P<datetime>.+)\] '
    r'"(?P<method>\S+) (?P<url>\S+) (?P<protocol>\S+)" '
    r'(?P<status>\d+) (?P<size>\S+) "(?P<referer>.*?)" "(?P<user_agent>.*?)"'
)

COMBINED_LOG_FIELDS = [
    "client", "datetime", "method", "url", "protocol",
    "status", "size", "referer", "user_agent",
]

BOT_UA_PATTERN = re.compile(
    r"googlebot|bingbot|adsbot-google|google-inspectiontool|"
    r"applebot|duckduckbot|yandexbot|gptbot|ccbot|claudebot|"
    r"perplexitybot|bytespider",
    re.IGNORECASE,
)


def parse_logs(
    log_path: str,
    output_path: str,
    log_format: str = COMBINED_LOG_FORMAT,
    fields=COMBINED_LOG_FIELDS,
) -> pd.DataFrame:
    """Parse a raw access log (or glob of rotated logs) into a tidy DataFrame.

    Writes an intermediate parquet file (advertools requirement as of 0.16)
    and returns the parsed frame with `datetime` coerced to pandas Timestamp.
    """
    output_path = str(Path(output_path).with_suffix(".parquet"))
    adv.logs_to_df(
        log_file=log_path,
        output_file=output_path,
        errors_file=str(Path(output_path).with_suffix(".errors.txt")),
        log_format=log_format,
        fields=fields,
    )
    df = pd.read_parquet(output_path)
    df["datetime"] = pd.to_datetime(df["datetime"], format="%d/%b/%Y:%H:%M:%S %z", errors="coerce")
    return df


def filter_bot_traffic(df: pd.DataFrame, ua_pattern: re.Pattern = BOT_UA_PATTERN) -> pd.DataFrame:
    """Return only requests whose user-agent string matches a known crawler."""
    mask = df["user_agent"].fillna("").str.contains(ua_pattern)
    return df[mask].copy()


def bot_name(user_agent: str) -> str:
    """Best-effort label for which crawler issued the request, from the UA string."""
    ua = user_agent.lower()
    if "googlebot" in ua or "google-inspectiontool" in ua or "adsbot-google" in ua:
        return "googlebot"
    if "bingbot" in ua:
        return "bingbot"
    if "gptbot" in ua:
        return "gptbot"
    if "ccbot" in ua:
        return "ccbot"
    if "claudebot" in ua:
        return "claudebot"
    if "yandexbot" in ua:
        return "yandexbot"
    if "applebot" in ua:
        return "applebot"
    return "other"
