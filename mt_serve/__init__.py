"""
NCAT MT serving layer.

Run via:
    python -m mt_serve.server  --model hy-mt-7b
    python -m mt_serve.benchmark --model hy-mt-7b

Hardware target: NVIDIA RTX 5070 Ti 16 GB (or any modern GPU with >=16 GB).

Backends:
  - llama_cpp : GGUF (Q4/Q5/Q6/Q8). Works on all GPUs, no FP16 needed.
  - vllm      : HF safetensors (FP8 / BF16). Fastest on modern GPUs.
"""
__version__ = '0.1.0'
