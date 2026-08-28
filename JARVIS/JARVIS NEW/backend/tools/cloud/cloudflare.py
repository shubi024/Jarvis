import os
import httpx
import logging
from typing import Literal, Optional
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.Cloudflare")

def _get_cf_client():
    token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not token: raise ValueError("Missing 'CLOUDFLARE_API_TOKEN'.")
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=15.0)

class CloudflareReadInput(BaseModel):
    action: Literal["list_zones", "list_dns"] = Field(description="The read operation.")
    zone_id: Optional[str] = Field(default=None, description="Zone ID required for list_dns.")

class CloudflareReaderTool(BaseTool):
    name = "cloudflare_reader"
    description = "Read-only Cloudflare operations (list zones and DNS records)."
    category = "cloud"
    args_schema = CloudflareReadInput
    risk_level = "low"
    requires_approval = False

    async def _run(self, action: str, zone_id: Optional[str] = None) -> str:
        base_url = "https://api.cloudflare.com/client/v4"
        async with _get_cf_client() as client:
            if action == "list_zones":
                resp = await client.get(f"{base_url}/zones")
                resp.raise_for_status()
                zones = resp.json().get("result", [])
                return "\n".join([f"Name: {z['name']} | ID: {z['id']} | Status: {z['status']}" for z in zones]) if zones else "No zones found."

            elif action == "list_dns":
                if not zone_id: raise ValueError("zone_id required.")
                records, page = [], 1
                while True:
                    resp = await client.get(f"{base_url}/zones/{zone_id}/dns_records", params={"page": page, "per_page": 100})
                    resp.raise_for_status()
                    data = resp.json()
                    records.extend(data.get("result", []))
                    if page >= data.get("result_info", {}).get("total_pages", 1): break
                    page += 1
                return "\n".join([f"[{r['type']}] {r['name']} -> {r['content']} (ID: {r['id']})" for r in records]) if records else "No DNS records found."

class CloudflareWriteInput(BaseModel):
    zone_id: str = Field(description="Cloudflare Zone ID.")
    name: str = Field(description="DNS record name.")
    content: str = Field(description="DNS record content/target.")
    record_type: Literal["A", "AAAA", "CNAME", "TXT"] = Field(default="A")
    proxied: bool = Field(default=False)

class CloudflareWriterTool(BaseTool):
    name = "cloudflare_writer"
    description = "Modifies Cloudflare infrastructure (e.g., create DNS records)."
    category = "cloud"
    args_schema = CloudflareWriteInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, zone_id: str, name: str, content: str, record_type: str = "A", proxied: bool = False) -> str:
        base_url = "https://api.cloudflare.com/client/v4"
        payload = {"type": record_type, "name": name, "content": content, "ttl": 3600, "proxied": proxied if record_type in {"A", "AAAA", "CNAME"} else False}
        async with _get_cf_client() as client:
            resp = await client.post(f"{base_url}/zones/{zone_id}/dns_records", json=payload)
            resp.raise_for_status()
            if not resp.json().get("success", False): raise RuntimeError(f"API error: {resp.json().get('errors')}")
            return f"Created {record_type} record for '{name}' -> '{content}'."