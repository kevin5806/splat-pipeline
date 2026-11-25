#!/bin/bash
# Patch opzionale per GPU Pascal (CUDA capability 6.x: Tesla P4/P40, GTX 10xx).
#
# Problema: i kernel di gsplat nell'immagine di Nerfstudio non sono compilati per sm_61 e il sorgente
# usa cooperative_groups::labeled_partition, che esiste solo da Volta (sm_70) in su.
# Soluzione: installa nvcc, sostituisce quelle chiamate con un gruppo "vuoto" (ogni thread fa il proprio
# atomicAdd: risultato identico, backward appena più lento) e ricompila gsplat per sm_61.
# Nel runtime serve anche TORCHDYNAMO_DISABLE=1 (Triton non supporta CUDA < 7.0): lo imposta l'entrypoint.
set -euo pipefail

CUDA_VER=11-8
apt-get update
apt-get install -y --no-install-recommends \
  cuda-nvcc-$CUDA_VER cuda-cudart-dev-$CUDA_VER cuda-nvrtc-dev-$CUDA_VER \
  libcusparse-dev-$CUDA_VER libcublas-dev-$CUDA_VER libcusolver-dev-$CUDA_VER libcurand-dev-$CUDA_VER ninja-build
rm -rf /var/lib/apt/lists/*

export CUDA_HOME=/usr/local/cuda-11.8
export PATH=$CUDA_HOME/bin:$PATH
export TORCH_CUDA_ARCH_LIST=6.1
export MAX_JOBS=${MAX_JOBS:-8}   # parallelismo di nvcc: abbassalo se la RAM è poca

G=$(python -c "import gsplat,os;print(os.path.dirname(gsplat.__file__))")
mv "$G/csrc.so" "$G/csrc.so.disabled"          # il binario precompilato non include sm_61
cd "$G/cuda/csrc"

# gs_reduce: reduce di cooperative_groups per i gruppi normali, identità per il gruppo vuoto
sed -i 's|cg::reduce(warp, |gs_reduce(warp, |g' helpers.cuh
sed -i '/^namespace cg = cooperative_groups;/a \
struct NoGroup { __device__ uint32_t thread_rank() const { return 0; } };\
template <class W, class S, class Op> inline __device__ S gs_reduce(W \&w, S v, Op op) { return cg::reduce(w, v, op); }\
template <class S, class Op> inline __device__ S gs_reduce(NoGroup \&, S v, Op) { return v; }' helpers.cuh
sed -i -E 's/cg::labeled_partition\(warp, [a-z]+\)/NoGroup()/' \
  fully_fused_projection_2dgs_bwd.cu fully_fused_projection_bwd.cu \
  fully_fused_projection_packed_2dgs_bwd.cu fully_fused_projection_packed_bwd.cu world_to_cam_bwd.cu

if grep -q labeled_partition ./*.cu; then echo "ERRORE: labeled_partition ancora presente" >&2; exit 1; fi

# compila ora (10-20 minuti) invece che al primo training
python -c "from gsplat.cuda._backend import _C; print('gsplat compilato per sm_61:', _C)"
