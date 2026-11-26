#!/bin/bash
set -e
if [ "${PASCAL_PATCH:-0}" = "1" ]; then
  export CUDA_HOME=/usr/local/cuda-11.8
  export PATH=$CUDA_HOME/bin:$PATH
  export TORCH_CUDA_ARCH_LIST=6.1
  export TORCHDYNAMO_DISABLE=1          # Triton non supporta le GPU con CUDA capability < 7.0
  export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}
fi
mkdir -p "${DATA_DIR:-/data}/projects"
cd /app
if [ "${1:-}" = "worker" ]; then
  exec python -m app.worker                     # esegue i progetti (serve la GPU)
fi
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
