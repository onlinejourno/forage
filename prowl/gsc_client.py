"""Thin wrapper around the Search Console API for Crawl Stats and
URL Inspection data. Requires a service account with access added as a
user in Search Console (Settings > Users and permissions).
"""

from datetime import date

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def get_client(credentials_json: str):
    creds = service_account.Credentials.from_service_account_file(credentials_json, scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds)


def fetch_crawl_stats(client, site_url: str) -> dict:
    """Crawl Stats lives under the Search Console "Settings" report, which
    is only exposed via the UI/bulk-data-export, not the public API as of
    2025. This pulls the closest API-accessible proxy: Search Analytics
    (impressions/clicks won't show crawl requests directly, but pairing
    this with log-file data gives the full picture - see diff_engine).
    """
    raise NotImplementedError(
        "Google does not expose raw Crawl Stats via the Search Console API. "
        "Export Settings > Crawl Stats > 'Export data' (BigQuery/CSV) manually, "
        "or use the bulk data export to BigQuery, then load that CSV/table here."
    )


def inspect_url(client, site_url: str, inspection_url: str) -> dict:
    body = {"inspectionUrl": inspection_url, "siteUrl": site_url}
    return client.urlInspection().index().inspect(body=body).execute()


def fetch_search_analytics(client, site_url: str, start_date: date, end_date: date, dimensions=("page",)) -> dict:
    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": list(dimensions),
        "rowLimit": 25000,
    }
    return client.searchanalytics().query(siteUrl=site_url, body=body).execute()
