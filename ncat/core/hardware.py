"""Host hardware and runtime sampling for the Health panel.

Only the standard library is used: this module must stay importable without
torch/psutil/GPU bindings. Every probe is best-effort — a missing tool or file
yields an empty field, never an exception, so the panel can degrade gracefully.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

_CPUINFO = Path("/proc/cpuinfo")
_MEMINFO = Path("/proc/meminfo")
_STAT = Path("/proc/stat")
_UPTIME = Path("/proc/uptime")
_OS_RELEASE = Path("/etc/os-release")

_GPU_FIELDS = (
    "index,name,memory.total,memory.used,memory.free,utilization.gpu,"
    "utilization.memory,temperature.gpu,power.draw,power.limit,fan.speed,"
    "driver_version"
)
_GPU_FALLBACK_FIELDS = (
    "name,memory.used,memory.total,utilization.gpu,temperature.gpu"
)

_SAMPLE_LOCK = threading.Lock()
_LAST_SAMPLE: dict[str, list[tuple[int, int]]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text.upper() == "N/A":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text.upper() == "N/A":
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ------------------------------------------------------------------ host/cpu

def os_pretty_name() -> str:
    for line in _read(_OS_RELEASE).splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return platform.platform()


def host_info() -> dict:
    uptime = None
    fields = _read(_UPTIME).split()
    if fields:
        uptime = _float(fields[0])
    return {
        "hostname": platform.node(),
        "os": os_pretty_name(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "uptime_s": int(uptime) if uptime is not None else None,
    }


def cpu_info() -> dict:
    model = ""
    physical = None
    logical = os.cpu_count() or 0
    mhz: list[float] = []
    blocks = [block for block in _read(_CPUINFO).split("\n\n") if block.strip()]
    for block in blocks:
        fields = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
        if not model:
            model = fields.get("model name") or fields.get("Model") or ""
        if physical is None:
            cores = _int(fields.get("cpu cores"))
            if cores:
                physical = cores
        frequency = _float(fields.get("cpu MHz"))
        if frequency:
            mhz.append(frequency)
    try:
        load = list(os.getloadavg())
    except OSError:
        load = [None, None, None]
    return {
        "model": model or platform.processor() or "CPU",
        "logical": logical,
        "physical": physical or logical,
        "freq_mhz": round(sum(mhz) / len(mhz)) if mhz else None,
        "freq_max_mhz": round(max(mhz)) if mhz else None,
        "load": load,
    }


def _cpu_times() -> list[tuple[int, int]]:
    """Total and idle jiffies per core (index 0 is the aggregate line)."""
    rows: list[tuple[int, int]] = []
    for line in _read(_STAT).splitlines():
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        if not parts[0][3:].isdigit() and parts[0] != "cpu":
            continue
        values = [int(value) for value in parts[1:] if value.isdigit()]
        if len(values) < 4:
            continue
        total = sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        rows.append((total, idle))
    return rows


def cpu_usage(key: str = "system") -> dict:
    """Usage since the previous sample, so repeated polls show real load."""
    with _SAMPLE_LOCK:
        current = _cpu_times()
        previous = _LAST_SAMPLE.get(key)
        if previous is None or len(previous) != len(current):
            # First poll: take a short second sample so the panel is populated.
            import time
            time.sleep(0.12)
            previous = current
            current = _cpu_times()
        total_delta = [max(0, now[0] - old[0])
                       for now, old in zip(current, previous)]
        idle_delta = [max(0, now[1] - old[1])
                      for now, old in zip(current, previous)]
        _LAST_SAMPLE[key] = current
    usage: list[float | None] = []
    for total, idle in zip(total_delta, idle_delta):
        usage.append(round(100 * (1 - idle / total), 1) if total else None)
    return {
        "usage_pct": usage[0] if usage else None,
        "per_core": usage[1:] if len(usage) > 1 else [],
    }


# --------------------------------------------------------------------- memory

def memory_info() -> dict:
    values: dict[str, int] = {}
    for line in _read(_MEMINFO).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        field = value.strip().split()[0]
        if field.isdigit():
            values[key.strip()] = int(field)
    total = values.get("MemTotal", 0) // 1024
    available = values.get("MemAvailable", 0) // 1024
    cached = (values.get("Cached", 0) + values.get("SReclaimable", 0)) // 1024
    return {
        "total_mb": total,
        "available_mb": available,
        "used_mb": max(0, total - available),
        "cached_mb": cached,
        "swap_total_mb": values.get("SwapTotal", 0) // 1024,
        "swap_free_mb": values.get("SwapFree", 0) // 1024,
    }


# ------------------------------------------------------------------------ gpu

def _run(argv: list[str], timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def parse_gpu_rows(output: str) -> list[dict]:
    """Parse ``nvidia-smi --query-gpu`` CSV rows into device dicts."""
    devices: list[dict] = []
    for index, line in enumerate(output.splitlines()):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 12:
            devices.append({
                "index": _int(parts[0]) if parts[0].isdigit() else index,
                "name": parts[1],
                "memory_total_mb": _int(parts[2]),
                "memory_used_mb": _int(parts[3]),
                "memory_free_mb": _int(parts[4]),
                "utilization": _int(parts[5]),
                "memory_utilization": _int(parts[6]),
                "temperature_c": _int(parts[7]),
                "power_w": _float(parts[8]),
                "power_limit_w": _float(parts[9]),
                "fan_pct": _int(parts[10]),
                "driver": parts[11],
            })
        elif len(parts) >= 5:
            devices.append({
                "index": index,
                "name": parts[0],
                "memory_used_mb": _int(parts[1]),
                "memory_total_mb": _int(parts[2]),
                "utilization": _int(parts[3]),
                "temperature_c": _int(parts[4]),
            })
    return devices


def parse_gpu_processes(output: str) -> list[dict]:
    processes: list[dict] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        pid = _int(parts[0])
        if pid is None:
            continue
        processes.append({
            "pid": pid,
            "name": parts[1],
            "memory_mb": _int(parts[2]),
        })
    return processes


def gpu_info() -> list[dict]:
    output = _run(["nvidia-smi", "--query-gpu=" + _GPU_FIELDS,
                   "--format=csv,noheader,nounits"])
    if not output:
        output = _run(["nvidia-smi", "--query-gpu=" + _GPU_FALLBACK_FIELDS,
                       "--format=csv,noheader,nounits"])
    devices = parse_gpu_rows(output)
    if not devices:
        return []
    processes = parse_gpu_processes(_run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
         "--format=csv,noheader,nounits"]))
    for device in devices:
        device["processes"] = [item for item in processes
                               if item["memory_mb"]]
    return devices


# ---------------------------------------------------------------------- disks

def disk_info(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for path in paths:
        try:
            resolved = path.expanduser()
            if not resolved.exists():
                continue
            usage = shutil.disk_usage(resolved)
        except OSError:
            continue
        key = (usage.total, usage.free)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "label": str(resolved),
            "total_gb": round(usage.total / 1e9, 1),
            "used_gb": round(usage.used / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1),
            "used_pct": round(100 * usage.used / usage.total, 1)
            if usage.total else None,
        })
    return rows


def service_snapshot(key: str = "service") -> dict:
    """Hardware snapshot published by an ASR/MT service: CPU, GPU, memory only.

    The service owns its own sample, so each panel reflects the process that
    actually runs the model (and its own CUDA context), not the web server.
    """
    cpu = cpu_info()
    cpu.update(cpu_usage(key=key))
    gpus = gpu_info()
    return {
        "collected_at": now_iso(),
        "cpu": cpu,
        "gpu": gpus[0] if gpus else {},
        "memory": memory_info(),
    }


# ------------------------------------------------------------------- collect

def collect(*, workspace: Path | None = None, model_root: Path | None = None,
            extra_paths: list[Path] | None = None) -> dict:
    """One hardware snapshot. Never raises; missing probes are null."""
    cpu = cpu_info()
    cpu.update(cpu_usage())
    paths = []
    if workspace is not None:
        paths.append(workspace)
    if model_root is not None:
        paths.append(model_root)
    paths.extend(extra_paths or [])
    if not paths:
        paths.append(Path.cwd())
    gpus = gpu_info()
    disks = disk_info(paths)
    memory = memory_info()
    return {
        "collected_at": now_iso(),
        "host": host_info(),
        "cpu": cpu,
        "memory": memory,
        "gpus": gpus,
        # Back-compat: the original payload exposed a single gpu/disk object.
        "gpu": gpus[0] if gpus else {},
        "disks": disks,
        "disk": disks[0] if disks else {},
    }
