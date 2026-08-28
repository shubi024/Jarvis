import os
import logging
from typing import Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.MetaAds")

class MetaAdsInput(BaseModel):
    action: Literal["list_campaigns", "get_insights"] = Field(
        description="The Meta Ads operation to perform."
    )

class MetaAdsTool(BaseTool):
    name = "meta_ads_manager"
    description = "Interacts with the Meta Marketing API to inspect ad campaigns and insights."
    category = "cloud"
    args_schema = MetaAdsInput
    risk_level = "low"         # READ-ONLY operation
    requires_approval = False

    async def _run(self, action: str) -> str:
        try:
            from facebook_business.api import FacebookAdsApi
            from facebook_business.adobjects.adaccount import AdAccount
            from facebook_business.adobjects.campaign import Campaign
            from facebook_business.adobjects.adsinsights import AdsInsights
        except ImportError:
            raise RuntimeError("The 'facebook_business' library is required.")

        app_id = os.getenv("META_APP_ID")
        app_secret = os.getenv("META_APP_SECRET")
        access_token = os.getenv("META_ACCESS_TOKEN")
        account_id = os.getenv("META_AD_ACCOUNT_ID")

        if not all([app_id, app_secret, access_token, account_id]):
            raise ValueError("Missing Meta configuration in environment variables.")

        try:
            FacebookAdsApi.init(app_id, app_secret, access_token)
            account = AdAccount(account_id if account_id.startswith("act_") else f"act_{account_id}")

            if action == "list_campaigns":
                campaigns = account.get_campaigns(fields=[Campaign.Field.name, Campaign.Field.status, Campaign.Field.objective])
                if not campaigns: return f"No ad campaigns found for account {account_id}."
                output = [f"--- Meta Ads Campaigns ({account_id}) ---"]
                for camp in campaigns:
                    output.append(f"• [{camp.get('status', 'UNKNOWN')}] {camp.get('name', 'Unnamed')} (ID: {camp.get('id')})")
                return "\n".join(output)

            elif action == "get_insights":
                insights = account.get_insights(
                    fields=[AdsInsights.Field.campaign_name, AdsInsights.Field.spend, AdsInsights.Field.impressions, AdsInsights.Field.clicks], 
                    params={'date_preset': 'last_30_days'}
                )
                if not insights: return "No ad insights available."
                output = [f"--- Meta Ads Performance Insights (Last 30 Days) ---"]
                for i in insights:
                    output.append(f"• Campaign: {i.get('campaign_name')} | Spend: ${i.get('spend')} | Impressions: {i.get('impressions')} | Clicks: {i.get('clicks')}")
                return "\n".join(output)
            else:
                raise ValueError(f"Unsupported action: {action}")
        except Exception as e:
            raise RuntimeError(f"Meta Ads error: {str(e)}")