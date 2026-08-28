import logging
import asyncio
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool
from backend.tools.web.web_security import validate_secure_url

logger = logging.getLogger("JARVIS.Tools.WebFetch")

class WebFetchInput(BaseModel):
    url: str = Field(description="The complete URL of the web page to fetch and read.")
    max_characters: int = Field(default=10000, description="Maximum number of characters to extract.")

class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = "Fetches a webpage with hop-by-hop redirect validation and strict SSRF protection."
    category = "web"
    args_schema = WebFetchInput
    risk_level = "low"
    requires_approval = False

    async def _run(self, url: str, max_characters: int = 10000) -> str:
        # Initial URL validation
        safe_initial_url = validate_secure_url(url)

        def _fetch_with_redirect_validation():
            import httpx
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            }

            # Custom client that manually checks redirect locations at every hop
            with httpx.Client(headers=headers, follow_redirects=False, timeout=10.0) as client:
                current_url = safe_initial_url
                max_hops = 5
                hop_count = 0

                while hop_count < max_hops:
                    response = client.get(current_url)
                    
                    # Check if status code indicates a redirect
                    if response.is_redirect:
                        redirect_url = response.headers.get("Location")
                        if not redirect_url:
                            break
                        
                        # Handle relative redirect links
                        if redirect_url.startswith("/"):
                            parsed_orig = httpx.URL(current_url)
                            redirect_url = f"{parsed_orig.scheme}://{parsed_orig.netloc}{redirect_url}"

                        # CRITICAL: Re-validate redirect target against SSRF policy
                        validate_secure_url(redirect_url)
                        
                        current_url = redirect_url
                        hop_count += 1
                    else:
                        response.raise_for_status()
                        break
                else:
                    raise RuntimeError("Maximum redirect limit exceeded or infinite redirect loop detected.")

                # Parse final response content
                soup = BeautifulSoup(response.text, "html.parser")
                for element in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
                    element.decompose()

                text = soup.get_text(separator=" ")
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                clean_text = "\n".join(chunk for chunk in chunks if chunk)

                if len(clean_text) > max_characters:
                    clean_text = clean_text[:max_characters] + f"\n\n... [Content truncated at {max_characters} characters]"
                return current_url, clean_text

        try:
            logger.info(f"Fetching web content securely with redirect validation from: {safe_initial_url}")
            final_url, clean_text = await asyncio.to_thread(_fetch_with_redirect_validation)
            return f"--- Content of {final_url} ---\n\n{clean_text}"
        except Exception as e:
            if isinstance(e, (PermissionError, RuntimeError, ValueError)): raise e
            logger.error(f"Failed to fetch content from {url}: {str(e)}")
            raise RuntimeError(f"Could not read webpage: {str(e)}")