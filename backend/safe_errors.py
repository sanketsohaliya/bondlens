import subprocess


def error_summary(exc: Exception) -> str:
    name = type(exc).__name__
    if isinstance(exc, subprocess.CalledProcessError):
        return f"{name} (exit code {exc.returncode})"
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"{name} (operation timed out)"
    if isinstance(exc, OSError) and isinstance(exc.errno, int):
        return f"{name} (errno {exc.errno})"
    return name
