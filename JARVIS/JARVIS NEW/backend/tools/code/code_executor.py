import os
import tempfile
import logging
import asyncio
import uuid
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.CodeExecutor")

class CodeExecutorInput(BaseModel):
    code: str = Field(description="The raw Python code string to execute safely within a Docker sandbox.")
    timeout: int = Field(default=15, ge=1, le=60, description="Maximum allowed execution time in seconds.")

class CodeExecutorTool(BaseTool):
    name = "code_executor"
    description = "Executes arbitrary Python code in a strictly secured Docker sandbox."
    category = "code"
    args_schema = CodeExecutorInput
    risk_level = "high"
    requires_approval = True

    async def _check_docker(self):
        """Verifies Docker is installed and the daemon is accessible."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError("Docker daemon is not running or accessible. Please start Docker.")
        except FileNotFoundError:
            raise RuntimeError("Docker is not installed or not in PATH. Required for sandboxed code execution.")

    async def _run(self, code: str, timeout: int = 15) -> str:
        await self._check_docker()
        logger.warning(f"Executing Docker-isolated Python code (Length: {len(code)} chars)")
        
        # 1. Generate unique container name for guaranteed lifecycle management
        container_name = f"jarvis_sandbox_{uuid.uuid4().hex[:8]}"
        
        # 2. Prepare host script with explicit read permissions for UID 10000
        temp_dir = tempfile.mkdtemp()
        script_path = os.path.join(temp_dir, "script.py")
        
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)
            
        os.chmod(temp_dir, 0o755)
        os.chmod(script_path, 0o644)

        try:
            docker_cmd = [
                "docker", "run",
                "--name", container_name,                      # Explicit name for explicit kill
                "--rm",                                        # Cleanup container automatically on clean exit
                "--network", "none",                           # Network isolation
                "--cpus", "0.5",                               # CPU limit
                "-m", "128m",                                  # RAM limit
                "--pids-limit", "64",                          # Prevent fork/process bombs
                "--cap-drop=ALL",                              # Drop all Linux capabilities
                "--security-opt=no-new-privileges",            # Prevent privilege escalation
                "--user", "10000:10000",                       # Run as non-root
                "--read-only",                                 # Read-only root filesystem
                "--tmpfs", "/tmp:rw,nosuid,nodev,exec,size=32m", # Limited writable /tmp
                "-v", f"{script_path}:/sandbox/script.py:ro",  # Strict read-only mount
                "python:3.12-slim",                            
                "timeout", str(timeout),                       # Internal container timeout fallback
                "python", "/sandbox/script.py"
            ]

            process = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # 3. Stream reader with strict output size limit (5 MB)
            MAX_OUTPUT_BYTES = 5 * 1024 * 1024
            
            async def _read_stream(stream, buffer, limit_tracker):
                while True:
                    chunk = await stream.read(8192)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    limit_tracker["total"] += len(chunk)
                    if limit_tracker["total"] > MAX_OUTPUT_BYTES:
                        raise BufferError("Maximum output size (5MB) exceeded.")

            stdout_buffer = bytearray()
            stderr_buffer = bytearray()
            limit_tracker = {"total": 0}

            try:
                # Execute with Timeout and Size constraints concurrently
                await asyncio.wait_for(
                    asyncio.gather(
                        _read_stream(process.stdout, stdout_buffer, limit_tracker),
                        _read_stream(process.stderr, stderr_buffer, limit_tracker)
                    ),
                    timeout=timeout + 3 
                )
                await process.wait()
            except asyncio.TimeoutError:
                raise RuntimeError(f"Code execution timed out after {timeout} seconds. The Docker sandbox was forcefully terminated.")
            except BufferError as e:
                raise RuntimeError(f"Code execution terminated: {str(e)}")

            returncode = process.returncode
            stdout = stdout_buffer.decode('utf-8', errors='replace')
            stderr = stderr_buffer.decode('utf-8', errors='replace')

            output = [f"Execution Exit Code: {returncode}"]
            
            if stdout.strip():
                output.append(f"\n--- Standard Output ---\n{stdout.strip()}")
            if stderr.strip():
                output.append(f"\n--- Standard Error ---\n{stderr.strip()}")

            if returncode == 124:
                raise RuntimeError(f"Code execution exceeded the {timeout}s limit inside the container.\n" + "\n".join(output))
            elif returncode != 0 and returncode is not None:
                raise RuntimeError(f"Code execution failed with exit code {returncode}.\n" + "\n".join(output))

            if not stdout.strip() and not stderr.strip():
                output.append("\n(No output generated)")

            return "\n".join(output)

        except Exception as e:
            if isinstance(e, RuntimeError):
                raise e
            raise RuntimeError(f"Docker sandbox execution failed: {type(e).__name__} - {str(e)}")
            
        finally:
            # 4. Guaranteed Cleanup Pipeline
            # Force kill and remove the container if it's still running (e.g. after a timeout/buffer overflow)
            cleanup_proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await cleanup_proc.communicate()

            # Clean up the host files
            if os.path.exists(script_path):
                try: os.remove(script_path)
                except Exception: pass
            if os.path.exists(temp_dir):
                try: os.rmdir(temp_dir)
                except Exception: pass