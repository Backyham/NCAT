"""vLLM backend for HF safetensors (BF16 / FP8 / quantized HF).

Fastest on modern GPUs (uses FP16/BF16 tensor cores). 30B BF16/FP8
will not fit on 16 GB -- use `enable_cpu_offload=True` to squeeze
the rest onto CPU (very slow but runs).
"""
from __future__ import annotations

import time

from .base import Backend, build_mt_prompt


class VLLMBackend(Backend):
    """HF safetensors loader via vllm."""

    def _do_load(self) -> None:
        try:
            from vllm import LLM, SamplingParams
        except ImportError as e:
            raise ImportError(
                'vllm not installed. Run:\n'
                '  pip install vllm'
            ) from e

        e = self.entry
        kwargs = dict(
            model=e.resolved_path(),
            dtype='auto',                # let vllm decide (bf16/fp16/fp8)
            max_model_len=e.n_ctx,
            gpu_memory_utilization=e.gpu_memory_utilization,
            trust_remote_code=True,
            enforce_eager=e.enforce_eager,
        )
        if e.enable_cpu_offload:
            # vllm 0.4+: offload optimizer weights to CPU.
            # Slower but lets big models run in small VRAM.
            kwargs['cpu_offloading'] = True
        print(f'[{self.label}] Loading {e.resolved_path()}')
        print(f'[{self.label}]   {kwargs}')
        self._model = LLM(**kwargs)

    def _do_unload(self) -> None:
        # vllm holds GPU memory until process exit. Forcing GC helps
        # before the next load.
        import gc
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
        self._model = None

    def _generate(self, prompt: str, max_new_tokens: int):
        from vllm import SamplingParams
        sp = SamplingParams(
            temperature=0.0,
            max_tokens=max_new_tokens,
            stop=None,
        )
        t0 = time.time()
        outs = self._model.generate([prompt], sp)
        text = outs[0].outputs[0].text
        n_tok = len(outs[0].outputs[0].token_ids)
        return text.strip(), n_tok

    def translate(self, text, source_lang=None, target_lang=None,
                  max_new_tokens=256) -> str:
        prompt = build_mt_prompt(
            text, source_lang, target_lang, self.entry
        )
        out, _ = self._generate(prompt, max_new_tokens)
        return out

    def complete(self, en_context, zh_partial, max_new_tokens=30) -> str:
        prompt = build_mt_prompt(en_context, self.entry) + ' ' + zh_partial
        out, _ = self._generate(prompt, max_new_tokens)
        return out
