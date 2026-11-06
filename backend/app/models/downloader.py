# SPDX-License-Identifier: AGPL-3.0-or-later
"""HuggingFace download jobs — preflight size gate, confined snapshot_download,
progress polling, cancel, and post-download registration (HANDOFF §5.1, #10).

Job lifecycle: ``submit()`` validates the repo id (confinement), preflights the
model size via ``HfApi().model_info`` against the RAM budget, then hands off to a
worker thread that runs ``snapshot_download(..., local_dir=models/<name>)``. Two
fake-tqdm shims handed to the library feed the byte bars back into the job so
``GET /api/models/downloads/<job_id>`` can report progress/speed/ETA.

Confinement: ``HF_HOME`` is pinned to ``<Omnidroids>/.cache/hf`` for the job, and
the resolved local_dir must sit inside ``Config.MODELS_DIR`` — nothing is ever
written outside the Omnidroids folder.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from huggingface_hub import HfApi, snapshot_download

from ..config import Config
from .store import ModelStore, sanitize_name
from ..utils.logger import get_logger

logger = get_logger("omnidroids.models.downloader")

_WEIGHT_SUFFIXES = (".safetensors", ".gguf", ".bin", ".pt")
_TERMINAL = ("done", "error", "cancelled")


class DownloadRejected(RuntimeError):
    """Actionable pre-download rejection (confinement / budget / not found)."""

    http_status = 400


class DownloadInProgress(DownloadRejected):
    """Same repo_id already has a queued/running job."""

    http_status = 409


def validate_repo_id(repo_id: str) -> str:
    """Confinement gate on the repo id shape. Returns the cleaned id."""
    rid = (repo_id or "").strip().strip("/")
    if not rid:
        raise DownloadRejected("repo_id is required (e.g. 'org/model-name')")
    if os.path.isabs(repo_id.strip()):
        raise DownloadRejected(f"repo_id must be a HF repo id, not a path: {repo_id!r}")
    parts = rid.split("/")
    if len(parts) > 2 or any(p in ("", ".", "..") for p in parts):
        raise DownloadRejected(f"invalid repo_id {repo_id!r} (expected 'org/name')")
    return rid


def _weights_size_bytes(siblings) -> int:
    total = 0
    for s in siblings or []:
        name = getattr(s, "rfilename", None) or getattr(s, "name", "")
        size = getattr(s, "size", None) or 0
        if str(name).lower().endswith(_WEIGHT_SUFFIXES):
            total += int(size)
    return total


@dataclass
class DownloadJob:
    job_id: str
    repo_id: str
    name: str
    local_dir: str
    role: Optional[str]
    status: str = "queued"          # queued|running|done|error|cancelled
    bytes_done: int = 0
    bytes_total: int = 0
    error: Optional[str] = None
    role_error: Optional[str] = None
    cancel_requested: bool = False
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    _t0: Optional[float] = None
    _n0: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- progress accounting (called from the shimmed tqdm bars) ------------
    def bar_created(self, bar: "_JobProgressBar") -> None:
        with self._lock:
            self.bytes_total = max(self.bytes_total, int(bar.total or 0))
            if self._t0 is None:
                self._t0 = time.time()

    def bar_updated(self, bar: "_JobProgressBar") -> None:
        now = time.time()
        with self._lock:
            self.bytes_total = max(self.bytes_total, int(bar.total or 0))
            self.bytes_done = max(self.bytes_done, int(bar.n))
            if self._t0 is None:
                self._t0 = now
            self._n0 = max(self._n0, 0)

    def speed_bps(self) -> float:
        with self._lock:
            if self._t0 is None or self.bytes_done <= 0:
                return 0.0
            dt = max(1e-6, time.time() - self._t0)
            return (self.bytes_done - self._n0) / dt

    def eta_s(self) -> Optional[float]:
        spd = self.speed_bps()
        remaining = self.bytes_total - self.bytes_done
        if spd > 0 and remaining > 0:
            return remaining / spd
        return None

    # -- state transitions ---------------------------------------------------
    def mark_running(self) -> None:
        self.status = "running"
        self.started_at = time.time()

    def request_cancel(self) -> bool:
        if self.status in ("queued", "running"):
            self.cancel_requested = True
            return True
        return False

    def finish(self, status: str, error: Optional[str] = None) -> None:
        self.status = status
        self.error = error
        self.finished_at = time.time()
        if status in _TERMINAL:
            logger.info("download %s %s (%s)", self.name, status, error or "ok")

    def to_dict(self) -> Dict[str, Any]:
        spd = self.speed_bps()
        return {
            "job_id": self.job_id,
            "repo_id": self.repo_id,
            "name": self.name,
            "role": self.role,
            "status": self.status,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "percent": (round(100.0 * self.bytes_done / self.bytes_total, 1)
                        if self.bytes_total else 0.0),
            "speed_mb_s": round(spd / (1024 * 1024), 2),
            "eta_s": self.eta_s(),
            "error": self.error,
            "role_error": self.role_error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class _JobProgressBar:
    """tqdm-class shim: snapshot_download instantiates this for its "Downloading
    bytes" + "Reconstructing" (unit='B') and "Fetching N files" (unit='it') bars.
    Byte bars feed the active job; anything else is accepted and ignored.

    The xet progress reporter additionally reads ``format_dict['rate']`` and a few
    tqdm attributes — provide a truthful minimal subset instead of a no-op stub.
    """

    _active_job: Optional[DownloadJob] = None  # class-level: bars are created in the worker thread

    def __init__(self, *, total=0, initial=0, desc="", unit="B", unit_scale=True,
                 bar_format=None, **kwargs):
        self.total = float(total or 0)
        self.n = float(initial or 0)
        self.pos = 0
        self.desc = desc or ""
        self._t0 = time.time()
        self._is_bytes = unit == "B"
        job = _JobProgressBar._active_job
        if job is not None and self._is_bytes:
            job.bar_created(self)

    @property
    def format_dict(self) -> Dict[str, Any]:
        dt = max(1e-6, time.time() - self._t0)
        return {
            "n": self.n,
            "total": self.total,
            "elapsed": dt,
            "rate": (self.n / dt) if self.n > 0 else None,
        }

    # -- tqdm-compatible surface used by huggingface_hub --------------------
    def update(self, n: float = 1) -> None:
        self.n += float(n or 0)
        job = _JobProgressBar._active_job
        if job is not None and self._is_bytes:
            job.bar_updated(self)

    def update_transfer(self, n: float = 1) -> None:
        # xet reports network bytes on the same aggregate; route like update()
        self.update(n)

    def clear(self, *a, **k) -> None:
        pass

    def refresh(self, *a, **k) -> None:
        pass

    def close(self) -> None:
        pass

    def set_description(self, *a, **k) -> None:
        pass

    def set_description_str(self, *a, **k) -> None:
        pass

    def set_postfix_str(self, *a, **k) -> None:
        pass

    def set_transfer_postfix_str(self, *a, **k) -> None:
        pass

    def reset(self, total=None, **k) -> None:
        if total is not None:
            self.total = float(total)
        self.n = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):  # any other tqdm-ism: tolerant no-op
        return lambda *a, **k: None


class DownloadRegistry:
    """In-memory job registry; one worker thread per download."""

    def __init__(self, store=None):
        from .manager import get_model_manager
        # Share the manager's store — the API reads models through the same
        # instance, so a finished download is visible immediately (no stale
        # second ModelStore snapshot).
        self._store = store or get_model_manager().store
        self._jobs: Dict[str, DownloadJob] = {}
        self._lock = threading.RLock()
        self._seq = 0

    # -- lifecycle -----------------------------------------------------------
    def submit(self, repo_id: str, *, role: Optional[str] = None) -> DownloadJob:
        rid = validate_repo_id(repo_id)

        with self._lock:
            for job in self._jobs.values():
                if job.repo_id == rid and job.status in ("queued", "running"):
                    raise DownloadInProgress(
                        f"download of {rid!r} is already in progress (job {job.job_id})")

        try:
            info = HfApi().model_info(rid, files_metadata=True)
        except Exception as exc:
            raise DownloadRejected(f"cannot inspect {rid!r}: {exc}") from exc

        weights = _weights_size_bytes(getattr(info, "siblings", None))
        budget_bytes = int(Config.MODEL_RAM_BUDGET_GB * 1024**3)
        if weights <= 0:
            raise DownloadRejected(
                f"{rid!r} exposes no downloadable model weights "
                "(no *.safetensors/*.gguf/*.bin/*.pt found)")
        if weights > budget_bytes:
            raise DownloadRejected(
                f"{rid!r} weights are {weights / 1024**3:.1f} GB, over the "
                f"{Config.MODEL_RAM_BUDGET_GB:g} GB RAM budget — pick a smaller quant")

        name = sanitize_name(rid)
        local_dir = os.path.join(Config.MODELS_DIR, name)
        # containment: the resolved path must stay under models/
        real_dir = os.path.realpath(local_dir)
        if os.path.commonpath([os.path.realpath(Config.MODELS_DIR), real_dir]) != \
                os.path.realpath(Config.MODELS_DIR):
            raise DownloadRejected(
                f"{rid!r} would download outside the models/ folder ({local_dir})")

        with self._lock:
            self._seq += 1
            job = DownloadJob(job_id=f"dl-{self._seq:04d}", repo_id=rid, name=name,
                              local_dir=local_dir, role=(role or None))
            self._jobs[job.job_id] = job
        t = threading.Thread(target=self._worker, args=(job,), name=f"dl-{job.job_id}", daemon=True)
        t.start()
        logger.info("download queued %s -> %s", rid, local_dir)
        return job

    def _worker(self, job: DownloadJob) -> None:
        job.mark_running()
        try:
            _confine_hf_cache()  # all HF artifacts stay inside the Omnidroids folder
            _JobProgressBar._active_job = job
            try:
                path = snapshot_download(repo_id=job.repo_id, local_dir=job.local_dir,
                                         tqdm_class=_JobProgressBar)
            finally:
                _JobProgressBar._active_job = None
        except Exception as exc:
            logger.warning("download %s failed: %s", job.name, exc)
            job.finish("error", str(exc))
            return

        if job.cancel_requested:
            job.finish("cancelled")
            return

        try:
            entry = self._store.add_model(
                name=job.name, repo_id=job.repo_id, path=path,
                backend=_detect_backend(path),
            )
            if job.role:
                try:
                    self._store.set_role(job.role, job.name)
                except ValueError as exc:
                    job.role_error = str(exc)
            logger.info("download %s complete: %s", job.name, entry["name"])
        except Exception as exc:
            logger.error("download %s registration failed: %s", job.name, exc)
            job.finish("error", f"download finished but registration failed: {exc}")
            return
        job.finish("done")

    # -- queries / control ----------------------------------------------------
    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in sorted(self._jobs.values(),
                                                key=lambda j: j.created_at)]

    def cancel(self, job_id: str) -> Optional[str]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        if not job.request_cancel():
            return "not-cancellable"
        if job.status == "queued":
            job.finish("cancelled")
        return "cancelling"



def _confine_hf_cache() -> None:
    """Pin every HF cache inside <Omnidroids>/.cache/hf (HANDOFF §5.1).

    huggingface_hub resolves HF_HOME/HF_HUB_CACHE at import time, so the launcher's
    export alone is not enough when the worker starts inside an already-imported
    process — rewrite the constants too (snapshot_download reads them at call time).
    """
    cache_root = Config.HF_CACHE_DIR
    os.environ["HF_HOME"] = cache_root
    os.environ["HF_HUB_CACHE"] = os.path.join(cache_root, "hub")
    os.environ["HF_XET_CACHE"] = os.path.join(cache_root, "xet")
    try:
        import huggingface_hub.constants as hf_constants
        hf_constants.HF_HOME = cache_root
        hf_constants.HF_HUB_CACHE = os.path.join(cache_root, "hub")
        if hasattr(hf_constants, "HF_XET_CACHE"):
            hf_constants.HF_XET_CACHE = os.path.join(cache_root, "xet")
    except Exception as exc:  # pragma: no cover - constants always importable
        logger.debug("hf cache constants patch skipped: %s", exc)


def _detect_backend(path: str) -> str:
    from .runtime import detect_backend
    return detect_backend(path)


_REGISTRY: Optional[DownloadRegistry] = None
_REGISTRY_LOCK = threading.Lock()


def get_download_registry() -> DownloadRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = DownloadRegistry()
        return _REGISTRY
