import os
import sys
import logging
import subprocess
import asyncio
from typing import Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.TestRunner")

class TestRunnerInput(BaseModel):
    target_path: str = Field(description="Path to a specific test file or directory.")
    framework: Literal["pytest", "unittest"] = Field(default="pytest", description="Testing framework.")
    timeout: int = Field(default=60, ge=1, le=300, description="Max execution time.")

class TestRunnerTool(BaseTool):
    name = "test_runner"
    description = "Executes unit tests using pytest or unittest."
    category = "code"
    args_schema = TestRunnerInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, target_path: str, framework: str = "pytest", timeout: int = 60) -> str:
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"Target path does not exist: '{target_path}'")
        
        cmd = [sys.executable, "-m", framework, target_path]
        
        def _sync_test_run(command: list[str], time_limit: int) -> tuple[int, str, str]:
            res = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=time_limit
            )
            return res.returncode, res.stdout, res.stderr

        try:
            # Non-blocking offload
            returncode, stdout, stderr = await asyncio.to_thread(_sync_test_run, cmd, timeout)
            
            output = [
                f"--- Test Execution Report ({framework}) ---",
                f"Target     : {target_path}",
                f"Exit Code  : {returncode}"
            ]
            
            if stdout.strip(): output.append(f"\n--- Standard Output ---\n{stdout.strip()}")
            if stderr.strip(): output.append(f"\n--- Standard Error ---\n{stderr.strip()}")

            if returncode != 0:
                raise RuntimeError(f"Tests FAILED with exit code {returncode}.\n" + "\n".join(output))
                
            output.append("Status     : PASSED")
            return "\n".join(output)
            
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Test execution timed out after {timeout} seconds.")
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise e
            raise RuntimeError(f"Test runner failed: {str(e)}")