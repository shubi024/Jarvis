import os
import shlex
import logging
import asyncio
from pathlib import Path
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.Terminal")

class TerminalInput(BaseModel):
    command: str = Field(description="The shell command to execute. Pipes and shell-specific operators (&&, >, |) are blocked.")
    timeout: int = Field(default=30, ge=1, le=120, description="Maximum execution time in seconds.")

class TerminalTool(BaseTool):
    name = "terminal"
    description = "Executes pre-approved native OS commands. All arguments are aggressively validated to prevent filesystem escapes."
    category = "system"
    args_schema = TerminalInput
    risk_level = "high"
    requires_approval = True

    def _validate_argument(self, arg: str, workspace_path: Path):
        """Aggressively inspects a single argument to ensure it doesn't escape the workspace boundary."""
        
        # 1. Block overt traversal attempts outright
        if ".." in arg:
            raise PermissionError(f"Security Violation: Directory traversal ('..') is strictly forbidden (Argument: '{arg}').")
        
        # 2. Handle flag=value pairs by checking both sides (e.g., --output=/etc/passwd)
        pieces = arg.split('=', 1) if '=' in arg else [arg]
        
        for piece in pieces:
            # If the piece looks like a path (contains slashes or drive letters)
            if '/' in piece or '\\' in piece or os.path.exists(piece):
                try:
                    # Attempt to resolve it as a path relative to the workspace.
                    # Note: If 'piece' is an absolute path (e.g., /etc/passwd), pathlib 
                    # overrides the workspace_path, which we will catch in the next check.
                    target = (workspace_path / piece).resolve()
                    
                    # Verify the resolved target is strictly contained within the workspace
                    if workspace_path not in target.parents and target != workspace_path:
                        raise PermissionError(
                            f"Security Violation: Argument '{piece}' attempts to reference a path outside the mandatory JARVIS workspace."
                        )
                except Exception as e:
                    if isinstance(e, PermissionError):
                        raise e
                    # If it fails to resolve normally but has path separators, block it to be safe
                    if '/' in piece or '\\' in piece:
                        raise PermissionError(f"Security Violation: Could not securely resolve path argument '{piece}'.")

    async def _run(self, command: str, timeout: int = 30) -> str:
        # 1. Enforce Workspace Boundary
        workspace_env = os.getenv("JARVIS_WORKSPACE")
        if not workspace_env:
            raise RuntimeError("JARVIS_WORKSPACE is missing. Terminal execution is disabled without a secure boundary.")
        
        workspace_path = Path(workspace_env).resolve()

        # 2. Safely parse command (Prevents shell injection vulnerabilities)
        try:
            parts = shlex.split(command, posix=(os.name == "posix"))
        except ValueError as e:
            raise ValueError(f"Malformed command syntax: {str(e)}")
            
        if not parts:
            raise ValueError("Empty command provided.")

        base_cmd = parts[0].lower()

        # 3. Command Policy / Allowlist Enforcement
        allowed_raw = os.getenv("JARVIS_ALLOWED_COMMANDS", "ls,pwd,echo,cat,dir,ping,git")
        allowed_commands = {cmd.strip().lower() for cmd in allowed_raw.split(",")}

        if base_cmd not in allowed_commands:
            raise PermissionError(
                f"Security Policy Violation: Command '{base_cmd}' is not in the approved allowlist. "
                f"Approved commands: {list(allowed_commands)}"
            )

        # 4. Argument Path-Validation (The Sandbox)
        for arg in parts[1:]:
            self._validate_argument(arg, workspace_path)

        logger.warning(f"Executing approved and validated command: '{parts}' in {workspace_path}")

        try:
            # 5. Safe Execution (Strictly no shell=True)
            process = await asyncio.create_subprocess_exec(
                *parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace_path)
            )

            # 6. Output Buffer Guard (5MB limit)
            MAX_OUTPUT_BYTES = 5 * 1024 * 1024
            stdout_buffer, stderr_buffer = bytearray(), bytearray()
            limit_tracker = {"total": 0}

            async def _read_stream(stream, buffer):
                while True:
                    chunk = await stream.read(8192)
                    if not chunk: break
                    buffer.extend(chunk)
                    limit_tracker["total"] += len(chunk)
                    if limit_tracker["total"] > MAX_OUTPUT_BYTES:
                        raise BufferError("Maximum output size (5MB) exceeded.")

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _read_stream(process.stdout, stdout_buffer),
                        _read_stream(process.stderr, stderr_buffer)
                    ),
                    timeout=timeout
                )
                await process.wait()
            except asyncio.TimeoutError:
                try: process.kill()
                except Exception: pass
                raise RuntimeError(f"Command timed out after {timeout} seconds.")
            except BufferError as e:
                try: process.kill()
                except Exception: pass
                raise RuntimeError(f"Execution terminated: {str(e)}")

            output = [f"Command executed. Exit Code: {process.returncode}"]
            stdout_str = stdout_buffer.decode('utf-8', errors='replace').strip()
            stderr_str = stderr_buffer.decode('utf-8', errors='replace').strip()

            if stdout_str: output.append(f"\n--- Standard Output ---\n{stdout_str}")
            if stderr_str: output.append(f"\n--- Standard Error ---\n{stderr_str}")
            if not stdout_str and not stderr_str: output.append("\n(No output returned)")
                
            return "\n".join(output)
            
        except FileNotFoundError:
            raise RuntimeError(f"Command executable '{base_cmd}' not found in system PATH.")
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            raise RuntimeError(f"Failed to execute command '{base_cmd}': {str(e)}")