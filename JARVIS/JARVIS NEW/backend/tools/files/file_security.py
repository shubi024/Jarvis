import os
from pathlib import Path

def _get_allowed_roots() -> list[Path]:
    """
    Resolves the LOCKED filesystem allowlist (Security Architecture §6):
      Allowed:   the user's Downloads folder + Local Disk E: (+ explicitly
                 authorized extras via FILESYSTEM_EXTRA_ALLOWED_PATHS).
      Denied:    everything else. No L4 grant can override this boundary.

    Legacy compatibility: JARVIS_WORKSPACE (if set) is honored as an extra root.
    Fail-closed: if no roots can be resolved, the list is empty and every
    operation is denied.
    """
    from backend.infrastructure.config import settings

    roots: list[Path] = []
    for raw in settings.allowed_filesystem_roots:
        try:
            roots.append(Path(raw).resolve())
        except Exception:
            continue
    return roots


def get_default_output_dir(subdir: str = "output") -> str:
    """
    Returns a writable default directory INSIDE the allowed boundary for
    system-generated artifacts (audio, reports, etc.).

    Preference order:
      1. E:\\JARVIS\\<subdir>          (locked spec's permitted drive)
      2. <Downloads>\\JARVIS\\<subdir>  (locked spec's permitted folder)
    The directory is created if missing.
    """
    candidates = [
        Path("E:/JARVIS") / subdir,
        Path.home() / "Downloads" / "JARVIS" / subdir,
    ]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            # Confirm the candidate is inside an allowed root before creating it
            secure_path_resolve(str(resolved))
            os.makedirs(resolved, exist_ok=True)
            return str(resolved)
        except Exception:
            continue

    raise PermissionError(
        "CRITICAL SECURITY CONFIGURATION: No allowed filesystem root is available "
        "(Downloads / E:). All output operations are hard-locked."
    )


def secure_path_resolve(requested_path: str) -> str:
    """
    Resolves and validates a requested path against the LOCKED JARVIS security policies.
    Raises PermissionError if the path is restricted or outside the allowed boundary.
    """
    target = Path(requested_path).resolve()
    
    # 1. Deny List: Block sensitive filenames and extensions
    restricted_names = {".env", "credentials.json", "secrets.yml", "id_rsa", "sam", "system"}
    if any(part.lower() in restricted_names for part in target.parts):
        raise PermissionError(f"Security Violation: Access to sensitive file or directory is forbidden ({target.name}).")
        
    # 2. Deny List: Block known OS system directories (defense-in-depth on top of allowlist)
    restricted_prefixes = [
        Path("C:/Windows").resolve(),
        Path("C:/Program Files").resolve(),
        Path("C:/Program Files (x86)").resolve(),
        Path("/etc").resolve(),
        Path("/var").resolve(),
        Path("/usr").resolve(),
        Path("/bin").resolve(),
        Path("/sbin").resolve(),
        Path("/sys").resolve(),
        Path("/proc").resolve(),
    ]
    
    for restricted in restricted_prefixes:
        # Check if the target is the restricted folder or inside it
        if restricted in target.parents or target == restricted:
            raise PermissionError(f"Security Violation: Access to OS system path is forbidden ({restricted}).")

    # 3. LOCKED Allowlist Boundary (Security Architecture §6):
    #    Only Downloads, Local Disk E:, and explicitly authorized extra roots are permitted.
    allowed_roots = _get_allowed_roots()

    for root in allowed_roots:
        if target == root or root in target.parents:
            return str(target)

    allowed_display = ", ".join(str(r) for r in allowed_roots) or "<none configured>"
    raise PermissionError(
        f"Security Violation: Path '{target}' is outside the locked J.A.R.V.I.S. filesystem "
        f"boundary. Allowed locations: {allowed_display}."
    )