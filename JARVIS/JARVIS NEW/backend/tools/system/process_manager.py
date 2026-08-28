import os
import logging
import asyncio
from typing import Optional, Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.ProcessManager")

# --- READER TOOL (LOW RISK) ---
class ProcessReaderInput(BaseModel):
    action: Literal["list"] = Field(description="Action to perform.")
    process_name: Optional[str] = Field(default=None, description="Search filter.")
    sort_by: Literal["cpu", "memory", "name"] = Field(default="cpu", description="Sorting metric.")
    limit: int = Field(default=10, ge=1, le=50, description="Max processes to return.")

class ProcessReaderTool(BaseTool):
    name = "process_reader"
    description = "Lists running OS processes with CPU/Memory stats."
    category = "system"
    args_schema = ProcessReaderInput
    risk_level = "low"
    requires_approval = False

    async def _run(self, action: str, process_name: Optional[str] = None, sort_by: str = "cpu", limit: int = 10) -> str:
        def _read_procs():
            import psutil
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    pinfo = proc.info
                    if process_name and process_name.lower() not in (pinfo['name'] or "").lower(): continue
                    processes.append(pinfo)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            
            if not processes: return "No processes found matching the criteria."
            
            if sort_by == "cpu": processes = sorted(processes, key=lambda p: p['cpu_percent'] or 0.0, reverse=True)
            elif sort_by == "memory": processes = sorted(processes, key=lambda p: p['memory_percent'] or 0.0, reverse=True)
            
            output = [f"--- Top {len(processes[:limit])} Processes ---", f"{'PID':<8} | {'NAME':<25} | {'CPU %':<8} | {'MEM %':<8}"]
            for p in processes[:limit]:
                output.append(f"{p['pid']:<8} | {(p['name'] or 'Unknown')[:24]:<25} | {round(p['cpu_percent'] or 0, 1):<8} | {round(p['memory_percent'] or 0, 1):<8}")
            return "\n".join(output)

        try:
            return await asyncio.to_thread(_read_procs)
        except Exception as e:
            raise RuntimeError(f"Process read failed: {str(e)}")


# --- WRITER TOOL (HIGH RISK) ---
class ProcessWriterInput(BaseModel):
    action: Literal["kill"] = Field(description="Action to perform.")
    pid: Optional[int] = Field(default=None, description="Target Process ID.")
    process_name: Optional[str] = Field(default=None, description="Target process name. Will abort if multiple matches are found.")

class ProcessWriterTool(BaseTool):
    name = "process_writer"
    description = "Safely terminates processes by PID or Name (prevents mass-termination)."
    category = "system"
    args_schema = ProcessWriterInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, action: str, pid: Optional[int] = None, process_name: Optional[str] = None) -> str:
        # Prevent termination of critical OS, container, and agent processes
        PROTECTED_PROCESSES = {
            "python", "python3", "python.exe", "dockerd", "docker", 
            "explorer.exe", "svchost.exe", "systemd", "init", "sshd"
        }

        def _kill_procs():
            import psutil
            if pid is None and not process_name: raise ValueError("Must provide 'pid' or 'process_name'.")
            
            jarvis_pid = os.getpid()

            def _check_protected(proc):
                if proc.pid == jarvis_pid:
                    raise PermissionError("Security Violation: Cannot kill the host JARVIS process.")
                if proc.name().lower() in PROTECTED_PROCESSES:
                    raise PermissionError(f"Security Violation: '{proc.name()}' is a protected system/critical process.")

            def _terminate(proc):
                _check_protected(proc)
                proc.terminate()
                try: proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)

            # Target by PID
            if pid is not None:
                try:
                    p = psutil.Process(pid)
                    p_name = p.name()
                    _terminate(p)
                    return f"Successfully terminated '{p_name}' (PID: {pid})."
                except psutil.NoSuchProcess: raise ValueError(f"No active PID: {pid}")
                except psutil.AccessDenied: raise PermissionError(f"Access denied for PID: {pid}.")
            
            # Target by Name (Guarded against mass-termination)
            if process_name:
                matches = []
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        if proc.info['name'] and proc.info['name'].lower() == process_name.lower():
                            matches.append(proc)
                    except Exception: pass
                
                if not matches:
                    return f"No active processes named '{process_name}'."
                if len(matches) > 1:
                    raise PermissionError(
                        f"Safety Halt: Found {len(matches)} processes named '{process_name}'. "
                        "Bulk termination is disabled. Please use 'process_reader' to find the exact PID."
                    )
                
                target = matches[0]
                target_pid = target.pid
                _terminate(target)
                return f"Successfully terminated single instance of '{process_name}' (PID: {target_pid})."

        try:
            return await asyncio.to_thread(_kill_procs)
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            raise RuntimeError(f"Process termination failed: {str(e)}")