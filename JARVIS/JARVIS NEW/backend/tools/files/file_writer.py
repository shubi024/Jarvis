import os
import logging
import asyncio
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool
from backend.tools.files.file_security import secure_path_resolve

logger = logging.getLogger("JARVIS.Tools.FileWriter")

class FileWriterInput(BaseModel):
    file_path: str = Field(description="The path where the file will be saved.")
    content: str = Field(description="The exact text content to write.")
    overwrite: bool = Field(default=False, description="Allows overwriting an existing file.")

class FileWriterTool(BaseTool):
    name = "file_writer"
    description = "Writes text content to a local file within authorized paths."
    category = "files"
    args_schema = FileWriterInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, file_path: str, content: str, overwrite: bool = False) -> str:
        def _write():
            safe_path = secure_path_resolve(file_path)
            
            if os.path.exists(safe_path):
                if not overwrite: raise FileExistsError("File exists. Pass 'overwrite=True' to overwrite.")
                if not os.path.isfile(safe_path): raise IsADirectoryError("Path is a directory.")

            directory = os.path.dirname(safe_path)
            if directory: os.makedirs(directory, exist_ok=True)

            with open(safe_path, "w", encoding="utf-8") as file:
                file.write(content)
                
            return f"Successfully wrote {len(content)} characters to '{safe_path}'."

        try:
            return await asyncio.to_thread(_write)
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            raise RuntimeError(f"Could not write to file: {str(e)}")