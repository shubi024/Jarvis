import os
import logging
from typing import Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.GoogleAds")

class GoogleAdsInput(BaseModel):
    action: Literal["list_campaigns", "get_metrics"] = Field(
        description="The Google Ads operation to perform: 'list_campaigns' or 'get_metrics'."
    )

class GoogleAdsTool(BaseTool):
    name = "google_ads_manager"
    description = "Interacts with the Google Ads API to inspect campaigns and performance metrics."
    category = "cloud"
    args_schema = GoogleAdsInput
    risk_level = "low"         # READ-ONLY operation
    requires_approval = False

    async def _run(self, action: str) -> str:
        try:
            from google.ads.googleads.client import GoogleAdsClient
            from google.ads.googleads.errors import GoogleAdsException
        except ImportError:
            raise RuntimeError("The 'google-ads' library is required. Run 'pip install google-ads'.")

        customer_id = os.getenv("GOOGLE_ADS_CUSTOMER_ID")
        if not customer_id:
            raise ValueError("Missing 'GOOGLE_ADS_CUSTOMER_ID' in environment variables.")

        customer_id = customer_id.replace("-", "")

        try:
            client = GoogleAdsClient.load_from_storage()
        except Exception as e:
            raise RuntimeError(f"Google Ads configuration initialization failed: {str(e)}")

        ga_service = client.get_service("GoogleAdsService")

        try:
            if action == "list_campaigns":
                query = "SELECT campaign.id, campaign.name, campaign.status FROM campaign ORDER BY campaign.name ASC LIMIT 50"
                response = ga_service.search(customer_id=customer_id, query=query)
                output = [f"--- Google Ads Campaigns (Customer: {customer_id}) ---"]
                count = 0
                for row in response:
                    count += 1
                    output.append(f"• [{row.campaign.status.name}] {row.campaign.name} (ID: {row.campaign.id})")
                
                return "\n".join(output) if count > 0 else f"No campaigns found for customer ID {customer_id}."

            elif action == "get_metrics":
                query = "SELECT campaign.id, campaign.name, metrics.impressions, metrics.clicks, metrics.cost_micros FROM campaign DURING LAST_30_DAYS"
                response = ga_service.search(customer_id=customer_id, query=query)
                output = [f"--- Google Ads Performance Metrics (Last 30 Days) ---"]
                count = 0
                for row in response:
                    count += 1
                    spend = row.metrics.cost_micros / 1_000_000 if row.metrics.cost_micros else 0.0
                    output.append(
                        f"• Campaign: {row.campaign.name} | Impressions: {row.metrics.impressions} | "
                        f"Clicks: {row.metrics.clicks} | Spend: ${spend:.2f}"
                    )
                
                return "\n".join(output) if count > 0 else f"No performance metrics found for customer ID {customer_id}."
            else:
                raise ValueError(f"Unsupported Google Ads action: {action}")

        except GoogleAdsException as ex:
            error_msg = "; ".join([f"Error: {e.message}" for e in ex.failure.errors])
            raise RuntimeError(f"Google Ads API failure: {error_msg}")