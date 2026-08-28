import logging
import asyncio
from typing import Optional
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.WebSearch")

class WebSearchInput(BaseModel):
    query: str = Field(description="The exact search query to execute on the web.")
    max_results: Optional[int] = Field(default=5, ge=1, le=15, description="Maximum number of search results (1 to 15).")

class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Searches the internet for up-to-date information, news, and facts."
    category = "web"
    args_schema = WebSearchInput
    risk_level = "low"
    requires_approval = False

    async def _run(self, query: str, max_results: int = 5) -> str:
        def _execute_search():
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                raise RuntimeError("The 'duckduckgo-search' library is required. Run 'pip install duckduckgo-search'.")

            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return results

        try:
            logger.info(f"Executing web search for: '{query}'")
            results = await asyncio.to_thread(_execute_search)
            
            if not results:
                return f"No results found on the web for query: '{query}'"
            
            formatted_results = [f"Search Results for '{query}':\n"]
            for i, res in enumerate(results, 1):
                title = res.get('title', 'No Title')
                href = res.get('href', 'No URL')
                body = res.get('body', 'No snippet available.')
                
                formatted_results.append(f"{i}. {title}")
                formatted_results.append(f"   URL: {href}")
                formatted_results.append(f"   Snippet: {body}\n")
            
            return "\n".join(formatted_results)
            
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            logger.error(f"Web search failed for query '{query}': {str(e)}")
            raise RuntimeError(f"Web search encountered an error: {str(e)}")