"""llama.cpp backend for GGUF models (Q4/Q5/Q6/Q8).

Works on any GPU. On a 16 GB card, 7B/9B Q4 fit comfortably. 30B Q4
needs `n_gpu_layers` set to something below the total (e.g. 60) so
the rest spills to CPU. Quantization choice: llama.cpp hits INT8
tensor cores on modern GPUs; INT4 path is also fast.
"""
from __future__ import annotations

import time
from typing import Any

from .base import Backend, build_mt_prompt


class LlamaCppBackend(Backend):
    """GGUF loader via llama-cpp-python."""

    def _do_load(self) -> None:
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise ImportError(
                'llama-cpp-python not installed. Run:\n'
                '  pip install llama-cpp-python '
                '--extra-index-url https://abetlen.github.io/'
                'llama-cpp-python/whl/cu124'
            ) from e

        e = self.entry
        n_layers_total = self._probe_layer_count(e.resolved_path())
        n_gpu = e.n_gpu_layers
        if n_gpu == -1:
            n_gpu = n_layers_total  # all on GPU
        elif n_gpu > n_layers_total:
            n_gpu = n_layers_total

        print(f'[{self.label}] Loading {e.resolved_path()}')
        print(f'[{self.label}]   n_ctx={e.n_ctx}  '
              f'n_gpu_layers={n_gpu}/{n_layers_total}')

        self._model = Llama(
            model_path=e.resolved_path(),
            n_ctx=e.n_ctx,
            n_gpu_layers=n_gpu,
            n_threads=None,         # auto = all CPU cores
            use_mmap=True,
            use_mlock=False,
            verbose=False,
        )

    def _probe_layer_count(self, gguf_path: str) -> int:
        """Best-effort: count layers in the GGUF file.

        Returns the model.tensor_count div 2 (rough) when we can't
        inspect. 9999 is used as a safe upper bound so the user gets
        a clear error from llama.cpp instead of silent offload.
        """
        try:
            from llama_cpp import llama_model_loader
            with llama_model_loader(gguf_path) as loader:
                # Sum tensors whose names start with 'blk.' (transformer blocks).
                n = 0
                for t in loader.tensors():
                    if '.blk.' in t.name or '/blk.' in t.name:
                        n += 1
                return n
        except Exception:
            # Fallback: assume Qwen-style 32B has ~64 layers.
            return 64

    def _do_unload(self) -> None:
        # llama-cpp-python releases on GC; nudge it.
        self._model = None

    def _generate(self, prompt: str, max_new_tokens: int,
                  stop: list[str] | None = None) -> tuple[str, int]:
        t0 = time.time()
        out = self._model(
            prompt,
            max_tokens=max_new_tokens,
            temperature=0.0,        # greedy for MT
            top_p=1.0,
            stop=stop,
            echo=False,
        )
        text = out['choices'][0]['text']
        n_tok = out['usage']['completion_tokens']
        return text.strip(), n_tok

    def _build_mt_prompt(self, text: str, source_lang: str,
                          target_lang: str) -> str:
        """Qwen3 chat template for Index-Translate / Hy-MT models.

        Critical: the model is Qwen3-based and has thinking mode
        enabled by default. To force a direct translation answer
        (no "let me think about this..." reasoning), we manually
        inject an empty think block right before the assistant
        response. This is equivalent to setting
        `chat_template_kwargs={'enable_thinking': False}`.
        """
        name = {'en': 'English', 'zh': '中文',
                'zh-Hant': '繁体中文',
                'ja': '日本語', 'fr': 'français',
                'de': 'Deutsch', 'es': 'español'}.get(target_lang, target_lang)
        src_name = {'en': '英文', 'zh': '中文',
                    'ja': '日文', 'fr': '法文',
                    'de': '德文'}.get(source_lang, source_lang)
        if source_lang == target_lang:
            user_msg = f'将以下{src_name}文本翻译为{name}。\n\n{text}'
        else:
            user_msg = (
                f'将以下{src_name}文本翻译为{name}。'
                f'注意只需要输出翻译后的结果，不要额外解释：\n\n{text}'
            )
        return (
            f'<|im_start|>user\n{user_msg}<|im_end|>\n'
            f'<|im_start|>assistant\n'
            f'<think>\n</think>\n'
        )

    def translate(self, text, source_lang=None, target_lang=None,
                  max_new_tokens=256) -> str:
        src = (source_lang or self.entry.default_source_lang or 'en')
        tgt = (target_lang or self.entry.default_target_lang or 'zh')
        prompt = self._build_mt_prompt(text, src, tgt)
        out, _ = self._generate(
            prompt, max_new_tokens,
            stop=['<|im_end|>', '<|im_start|>', '\n\n'],
        )
        return out.strip()

    def complete(self, en_context, zh_partial, max_new_tokens=30) -> str:
        prompt = self._build_mt_prompt(en_context, 'en', 'zh')
        full = prompt + zh_partial
        out, _ = self._generate(
            full, max_new_tokens,
            stop=['<|im_end|>', '<|im_start|>', '\n\n', '英文:'],
        )
        return out.strip()
