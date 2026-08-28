import os
import logging
import asyncio
from typing import Optional
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool
from backend.tools.files.file_security import secure_path_resolve

logger = logging.getLogger("JARVIS.Tools.FileReader")

class FileReaderInput(BaseModel):
    file_path: str = Field(description="The path to the file to be read.")
    start_line: Optional[int] = Field(default=None, description="Starting line number (1-indexed).")
    end_line: Optional[int] = Field(default=None, description="Ending line number (inclusive).")

class FileReaderTool(BaseTool):
    name = "file_reader"
    description = "Reads the contents of a local text file within authorized paths."
    category = "files"
    args_schema = FileReaderInput
    risk_level = "low"
    requires_approval = False

    async def _run(self, file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        def _read():
            safe_path = secure_path_resolve(file_path)
            
            if not os.path.exists(safe_path): raise FileNotFoundError(f"File not found: '{safe_path}'")
            if not os.path.isfile(safe_path): raise IsADirectoryError(f"Path '{safe_path}' is a directory.")
            if start_line is not None and start_line < 1: raise ValueError("start_line must be >= 1.")
            if start_line is not None and end_line is not None and start_line > end_line:
                raise ValueError("start_line cannot be > end_line.")

            with open(safe_path, "r", encoding="utf-8") as file:
                if start_line is None and end_line is None:
                    return file.read()
                
                lines = file.readlines()
                total_lines = len(lines)
                start_idx = max(0, (start_line or 1) - 1)
                end_idx = min(total_lines, (end_line or total_lines))
                
                content = "".join(lines[start_idx:end_idx])
                return f"--- Lines {start_idx + 1} to {end_idx} of {safe_path} ---\n{content}"

        try:
            return await asyncio.to_thread(_read)
        except UnicodeDecodeError:
            raise ValueError("Failed to decode file as UTF-8. Binary files are not supported.")
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            raise RuntimeError(f"Could not read file: {str(e)}")