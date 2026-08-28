import os
import ast
import logging
from typing import Optional
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.CodeAnalyzer")

class CodeAnalyzerInput(BaseModel):
    code: Optional[str] = Field(default=None, description="Raw Python code to analyze.")
    file_path: Optional[str] = Field(default=None, description="Path to a Python file.")

class CodeAnalyzerTool(BaseTool):
    name = "code_analyzer"
    description = "Performs static analysis on Python code using AST."
    category = "code"
    args_schema = CodeAnalyzerInput
    risk_level = "low"
    requires_approval = False

    async def _run(self, code: Optional[str] = None, file_path: Optional[str] = None) -> str:
        if not code and not file_path: 
            raise ValueError("Provide either 'code' or 'file_path'.")
        if code and file_path: 
            raise ValueError("Provide only one, not both.")

        source_code = code
        if file_path:
            if not os.path.exists(file_path): 
                raise FileNotFoundError(f"File not found: {file_path}")
            with open(file_path, "r", encoding="utf-8") as f: 
                source_code = f.read()

        try:
            tree = ast.parse(source_code)
            classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
            functions = [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
            
            output = [
                "--- Code Analysis Report ---",
                "Status: VALID (No Syntax Errors)",
                f"Total Lines: {len(source_code.splitlines())}",
                f"Classes: {len(classes)} | Functions: {len(functions)}"
            ]
            return "\n".join(output)
            
        except SyntaxError as e:
            # Syntax failure must bubble up as a runtime error so the ToolResult marks success=False
            error_msg = f"Syntax Error at line {e.lineno}, offset {e.offset}: {e.msg} -> {e.text.strip() if e.text else ''}"
            logger.warning(error_msg)
            raise RuntimeError(error_msg)
            
        except Exception as e:
            raise RuntimeError(f"Analysis failed: {str(e)}")