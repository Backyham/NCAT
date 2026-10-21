"""Benchmark an MT model registered in mt_serve.config.

Loads the model, runs a fixed set of translation prompts, reports
per-prompt latency, aggregate tok/s, and peak VRAM.

Run:
  python -m mt_serve.benchmark --model hy-mt-7b
  python -m mt_serve.benchmark --model hy-mt-30b-q4-gguf --prompts short
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Optional

from . import config
from .backends.base import human_bytes, probe_vram_bytes
from .backends.llama_cpp_backend import LlamaCppBackend
from .backends.vllm_backend import VLLMBackend


# Three lengths of prompts to test prompt-processing vs decode speed.
SAMPLE_PROMPTS = {
    'short': [
        "I'm running late.",
        "Where's the bathroom?",
        "Can I have some water, please?",
        "This looks great!",
        "I don't understand.",
    ],
    'medium': [
        "Today we're going to talk about how to give better gifts to coffee people.",
        "I'll explain how to choose the right grinder for someone at any stage in their coffee journey.",
        "Beginners might want something simple and affordable, while enthusiasts usually prefer better burr quality.",
        "For this video I'm going to focus on electric grinders because they tend to be more consistent.",
        "The main difference between blade and burr grinders is the uniformity of the particle size distribution.",
    ],
    'long': [
        "In this video I'll be walking you through the entire process of making pour-over coffee, from selecting your beans to the final cup, and I'll also share some tips I've learned from professional baristas over the past ten years of running my own small roasting business.",
        "When we're talking about sub-ohm vaping devices we need to consider several different factors including the battery capacity, the resistance of the coil, the maximum wattage output of the mod, and most importantly the safety features that protect the user from potential thermal runaway events.",
        "I want to start today's discussion by addressing some of the common misconceptions people have about deep learning neural networks, because there's a lot of marketing hype in this space and I think it's important that we as engineers understand the actual mathematical foundations rather than just repeating buzzwords from conference talks.",
    ],
}


def _make_backend(entry: config.ModelEntry):
    if entry.backend == 'llama_cpp':
        return LlamaCppBackend(entry)
    if entry.backend == 'vllm':
        return VLLMBackend(entry)
    raise ValueError(entry.backend)


def _peak_vram_bytes() -> int:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.max_memory_allocated()
    except Exception:
        pass
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model', required=True,
                   help='model name from mt_serve.config.REGISTRY')
    p.add_argument('--prompts', default='short,medium,long',
                   help='comma-separated list of prompt sets to run')
    p.add_argument('--max-new-tokens', type=int, default=200)
    p.add_argument('--output', default=None,
                   help='optional path to write JSON results')
    args = p.parse_args()

    entry = config.get(args.model)
    if not entry.exists():
        env_name = ('NCAT_MT_' + args.model.upper().replace('-', '_') + '_PATH')
        raise SystemExit(
            f'model {entry.name!r} not found at {entry.resolved_path() or "(unset)"}\n'
            f'set {env_name}, or drop {args.model}.gguf beside mt_serve/config.py'
        )

    sets = args.prompts.split(',')
    prompts: list[tuple[str, str]] = []  # (bucket, text)
    for s in sets:
        s = s.strip()
        if s in SAMPLE_PROMPTS:
            prompts.extend((s, t) for t in SAMPLE_PROMPTS[s])
        else:
            raise SystemExit(f'unknown prompt set: {s}')

    print(f'Loading {entry.name} from {entry.resolved_path()} ...')
    backend = _make_backend(entry)
    backend.load()
    if torch_cuda_reset_peak():
        pass  # nop
    try:
        import torch
        torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass

    results: list[dict] = []
    print(f'\nRunning {len(prompts)} prompts ...\n')
    print(f'{"bucket":<8} {"tokens":>7} {"latency_ms":>11} {"tok/s":>8}  text')
    print('-' * 80)
    for bucket, text in prompts:
        t0 = time.time()
        out = backend.translate(text, max_new_tokens=args.max_new_tokens)
        dt = time.time() - t0
        # Approximate tokens by character count for whitespace-less
        # languages (CJK); for English/space-separated this is similar
        # to word count which is close to BPE token count for short text.
        n_tok = max(len(out), 1)
        tps = n_tok / dt if dt > 0 else 0
        results.append({
            'bucket': bucket,
            'input': text,
            'output': out,
            'tokens': n_tok,
            'latency_ms': dt * 1000,
            'tok_per_s': tps,
        })
        snippet = (out[:50] + '...') if len(out) > 53 else out
        print(f'{bucket:<8} {n_tok:>7} {dt*1000:>10.1f} {tps:>8.2f}  {snippet}')

    peak = _peak_vram_bytes()
    print('\n' + '=' * 60)
    print(f'Model        : {entry.name}')
    print(f'Backend      : {entry.backend}')
    print(f'Format       : {entry.format}')
    print(f'Prompts run  : {len(results)}')
    total_tok = sum(r['tokens'] for r in results)
    total_t = sum(r['latency_ms'] for r in results) / 1000
    print(f'Total output tokens : {total_tok}')
    print(f'Total wall time     : {total_t:.2f}s')
    print(f'Aggregate tok/s     : {total_tok / total_t if total_t else 0:.2f}')
    if peak:
        print(f'Peak VRAM            : {human_bytes(peak)}')
    print('=' * 60)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump({
                'model': entry.name,
                'results': results,
                'summary': {
                    'total_tokens': total_tok,
                    'total_seconds': total_t,
                    'tok_per_s': total_tok / total_t if total_t else 0,
                    'peak_vram_bytes': peak,
                },
            }, f, ensure_ascii=False, indent=2)
        print(f'Wrote {args.output}')


def torch_cuda_reset_peak() -> bool:
    try:
        import torch
        torch.cuda.reset_peak_memory_stats()
        return True
    except Exception:
        return False


if __name__ == '__main__':
    main()
