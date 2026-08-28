import os
import shutil
import logging
import asyncio
from typing import Optional, Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool
from backend.tools.files.file_security import secure_path_resolve

logger = logging.getLogger("JARVIS.Tools.FileManager")

# --- READER TOOL ---
class FileManagerReadInput(BaseModel):
    action: Literal["list"] = Field(description="The read action to perform.")
    source_path: str = Field(description="The target directory path.")

class FileManagerReaderTool(BaseTool):
    name = "file_manager_reader"
    description = "Lists the contents of directories safely within authorized paths."
    category = "files"
    args_schema = FileManagerReadInput
    risk_level = "low"
    requires_approval = False

    async def _run(self, action: str, source_path: str) -> str:
        def _read_fs():
            safe_source = secure_path_resolve(source_path)
            if action == "list":
                if not os.path.exists(safe_source): raise FileNotFoundError(f"Directory not found: '{safe_source}'")
                if not os.path.isdir(safe_source): raise NotADirectoryError(f"Path '{safe_source}' is a file.")

                entries = os.scandir(safe_source)
                output = [f"[{'DIR ' if e.is_dir() else 'FILE'}] {e.name}" for e in entries]
                return f"Contents of '{safe_source}':\n" + "\n".join(output) if output else f"Directory '{safe_source}' is empty."
            raise ValueError(f"Unsupported read action: {action}")

        try:
            return await asyncio.to_thread(_read_fs)
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            raise RuntimeError(f"File manager read error: {str(e)}")

# --- WRITER TOOL ---
class FileManagerWriteInput(BaseModel):
    action: Literal["copy", "move", "delete", "mkdir"] = Field(description="Action to perform.")
    source_path: str = Field(description="Target file or directory.")
    destination_path: Optional[str] = Field(default=None, description="Destination (mandatory for copy/move).")

class FileManagerWriterTool(BaseTool):
    name = "file_manager_writer"
    description = "Creates directories, and copies, moves, or deletes files within authorized paths."
    category = "files"
    args_schema = FileManagerWriteInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, action: str, source_path: str, destination_path: Optional[str] = None) -> str:
        def _write_fs():
            safe_source = secure_path_resolve(source_path)
            safe_dest = secure_path_resolve(destination_path) if destination_path else None

            if action == "mkdir":
                if os.path.exists(safe_source): raise FileExistsError(f"Path already exists: '{safe_source}'")
                os.makedirs(safe_source, exist_ok=True)
                return f"Successfully created directory at '{safe_source}'."

            elif action == "delete":
                if not os.path.exists(safe_source): raise FileNotFoundError(f"Path does not exist: '{safe_source}'")
                if os.path.isfile(safe_source): os.remove(safe_source)
                else: shutil.rmtree(safe_source)
                return f"Successfully deleted: '{safe_source}'"

            elif action in ["copy", "move"]:
                if not safe_dest: raise ValueError(f"'destination_path' is mandatory for '{action}'.")
                if not os.path.exists(safe_source): raise FileNotFoundError(f"Source does not exist: '{safe_source}'")
                
                dest_parent = os.path.dirname(safe_dest)
                if dest_parent: os.makedirs(dest_parent, exist_ok=True)

                if action == "copy":
                    if os.path.isdir(safe_source): shutil.copytree(safe_source, safe_dest, dirs_exist_ok=True)
                    else: shutil.copy2(safe_source, safe_dest)
                    return f"Successfully copied to '{safe_dest}'."
                elif action == "move":
                    shutil.move(safe_source, safe_dest)
                    return f"Successfully moved to '{safe_dest}'."

        try:
            return await asyncio.to_thread(_write_fs)
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            raise RuntimeError(f"File manager write error: {str(e)}")