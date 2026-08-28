import platform
import logging
import asyncio
from typing import Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.SystemInfo")

class SystemInfoInput(BaseModel):
    category: Literal["all", "cpu", "memory", "disk", "battery", "os"] = Field(
        default="all", description="The specific hardware or OS subsystem metric to inspect."
    )

class SystemInfoTool(BaseTool):
    name = "system_info"
    description = "Retrieves live host system telemetry including CPU, RAM, disk space, and OS specifications."
    category = "system"
    args_schema = SystemInfoInput
    risk_level = "low"
    requires_approval = False

    async def _run(self, category: str = "all") -> str:
        def _collect():
            try: import psutil
            except ImportError: raise RuntimeError("The 'psutil' library is required. Run 'pip install psutil'.")

            telemetry = []
            if category in ["all", "os"]:
                telemetry.extend([
                    "--- OS & Architecture ---",
                    f"Operating System : {platform.system()} {platform.release()} (Version: {platform.version()})",
                    f"Machine / Arch   : {platform.machine()} ({platform.architecture()[0]})",
                    f"Processor        : {platform.processor() or 'Unknown'}\n"
                ])
            if category in ["all", "cpu"]:
                telemetry.extend([
                    "--- CPU Telemetry ---",
                    f"Physical Cores   : {psutil.cpu_count(logical=False)}",
                    f"Current Usage    : {psutil.cpu_percent(interval=0.1)}%",
                    f"Per-Core Usage   : {psutil.cpu_percent(interval=0.1, percpu=True)}\n"
                ])
            if category in ["all", "memory"]:
                mem = psutil.virtual_memory()
                telemetry.extend([
                    "--- Memory Telemetry ---",
                    f"Total RAM        : {mem.total / (1024 ** 3):.2f} GB",
                    f"Used RAM         : {mem.used / (1024 ** 3):.2f} GB ({mem.percent}%)\n"
                ])
            if category in ["all", "disk"]:
                disk_path = "/" if platform.system() != "Windows" else "C:\\"
                disk = psutil.disk_usage(disk_path)
                telemetry.extend([
                    "--- Disk Storage Telemetry ---",
                    f"Total Disk Space : {disk.total / (1024 ** 3):.2f} GB",
                    f"Used Disk Space  : {disk.used / (1024 ** 3):.2f} GB ({disk.percent}%)\n"
                ])
            if category in ["all", "battery"]:
                sensors = psutil.sensors_battery()
                if sensors:
                    telemetry.extend([
                        "--- Battery Telemetry ---",
                        f"Charge Level     : {sensors.percent}%",
                        f"Power State      : {'Plugged In' if sensors.power_plugged else 'Battery'}\n"
                    ])
            return "\n".join(telemetry).strip()

        try:
            return await asyncio.to_thread(_collect)
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            raise RuntimeError(f"Diagnostic error: {str(e)}")