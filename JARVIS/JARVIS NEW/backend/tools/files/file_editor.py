import os
import logging
import asyncio
from typing import Optional, Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool
from backend.tools.files.file_security import secure_path_resolve

logger = logging.getLogger("JARVIS.Tools.FileEditor")

class FileEditorInput(BaseModel):
    action: Literal["append", "replace", "insert"] = Field(description="Edit operation.")
    file_path: str = Field(description="Path to the file to edit.")
    text_to_insert: str = Field(description="Text to inject, append, or replace with.")
    text_to_replace: Optional[str] = Field(default=None, description="Text to find (mandatory for 'replace').")
    line_number: Optional[int] = Field(default=None, description="Line number (mandatory for 'insert').")

class FileEditorTool(BaseTool):
    name = "file_editor"
    description = "Surgically edits an existing text file within authorized paths."
    category = "files"
    args_schema = FileEditorInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, action: str, file_path: str, text_to_insert: str, text_to_replace: Optional[str] = None, line_number: Optional[int] = None) -> str:
        def _edit():
            safe_path = secure_path_resolve(file_path)
            
            if not os.path.exists(safe_path): raise FileNotFoundError(f"File not found: '{safe_path}'")
            if not os.path.isfile(safe_path): raise IsADirectoryError(f"Path '{safe_path}' is a directory.")

            with open(safe_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            if action == "append":
                if content and not content.endswith("\n"): text_to_insert = "\n" + text_to_insert
                new_content = content + text_to_insert
            elif action == "replace":
                if not text_to_replace: raise ValueError("'text_to_replace' is mandatory for 'replace'.")
                if text_to_replace not in content: raise ValueError("Exact 'text_to_replace' string not found.")
                new_content = content.replace(text_to_replace, text_to_insert)
            elif action == "insert":
                if line_number is None or line_number < 1: raise ValueError("Valid 'line_number' mandatory for 'insert'.")
                with open(safe_path, "r", encoding="utf-8") as f_lines:
                    lines = f_lines.readlines()
                insert_idx = min(len(lines), line_number - 1)
                if not text_to_insert.endswith("\n"): text_to_insert += "\n"
                lines.insert(insert_idx, text_to_insert)
                new_content = "".join(lines)
            else:
                raise ValueError(f"Invalid action '{action}'.")

            with open(safe_path, "w", encoding="utf-8") as f:
                f.write(new_content)
                
            return f"Successfully executed '{action}' on '{safe_path}'."

        try:
            return await asyncio.to_thread(_edit)
        except UnicodeDecodeError:
            raise ValueError("Failed to decode file as UTF-8.")
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            raise RuntimeError(f"Could not edit file: {str(e)}")