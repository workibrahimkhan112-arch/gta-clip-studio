"""GTA Clip Studio: local-only media signal processing."""
import os

# Set before NumPy/CTranslate2 imports; do not oversubscribe the dual-core target.
for _name in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

# Avoid MKL's large packed-allocation pool on low-memory Windows/Intel hosts.
# CTranslate2's alternative CPU backend keeps tiny.en INT8 inference bounded;
# users can explicitly opt back into MKL when benchmarking a roomier system.
os.environ.setdefault("CT2_USE_MKL", "0")
os.environ.setdefault("MKL_DISABLE_FAST_MM", "1")
