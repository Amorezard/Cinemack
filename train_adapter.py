#!/usr/bin/env python3
"""
v4 (14 Oct): train the movie-buff LoRA adapter for the nightly rebuild (rebuild_adapter.sh), from a
chat-format file made by adapter_data.py build.

Same base, loading pattern and LoRA settings as ~/movie-buff/train_lora_unsloth.py (the original
persona adapter: unsloth/Qwen3.8-27B-unsloth-bnb-4bit, text_only, r 16, alpha 32, all seven
projections), so a rebuilt adapter is the persona LoRA trained on more data, not a different kind of
model. Runs in the training venv (~/venvs/train-lora); rebuild_adapter.sh picks it via TRAIN_VENV.

    ~/venvs/train-lora/bin/python train_adapter.py --data builds/<name>/train.jsonl --out builds/<name>/lora
    ~/venvs/train-lora/bin/python train_adapter.py --probe      # GPU memory per length, no training (~5 min)

What differs from the original script, and why:
  max_seq (ADAPTER_MAX_SEQ)  the original trained 16 short rows at 2048. On the 22 GB A10, 4096 ran out of
                             memory on step 1 (16 Oct) and 2048 on step 2 (18 Oct). --probe measures the limit.
  long rows are TRIMMED      (19 Oct) a row longer than max_seq loses the middle of its longest non-answer
                             message (the retrieved passages), marked "[...]"; instructions, question and the
                             answer stay. Rows that cannot be trimmed enough are left out. ADAPTER_TRIM=0 drops
                             all long rows instead (18 Oct behaviour: 77 of 219 rows dropped at 2048).
  embeddings offloaded       (19 Oct) ADAPTER_OFFLOAD_EMBED=1 keeps the input embedding table in CPU memory
                             (~1.5 GB of GPU memory back); 0 = the original loading pattern
  adamw_8bit                 (18 Oct) 8-bit optimizer states (ADAPTER_OPTIM)
  batch 1 x accum 8          same 8 rows per step as 2 x 4, half the memory at the longer length
  epochs 2 (was 10)          10 epochs suited 16 rows; with hundreds of rows they would overfit
  lr 1e-4 (was 2e-4)         gentler for the larger, mixed set
  loss on answers only       the long RAG prompts are not learned, only the replies (train_on_responses_only);
                             if the chat markers are not found it falls back to the original whole-text loss
All of these are settings (ADAPTER_*), so the original recipe is one adapter.env away.
"""
import argparse
import json
import os
import sys
import time

E = lambda k, d: type(d)(os.environ.get(k, d))  # noqa: E731
MARK = "\n[...]\n"


def load(base, max_seq):
    from unsloth import FastLanguageModel
    offload = os.environ.get("ADAPTER_OFFLOAD_EMBED", "1") == "1"
    model, tok = FastLanguageModel.from_pretrained(      # the original script's "fixed loading pattern"
        model_name=base, max_seq_length=max_seq, load_in_4bit=True, dtype=None,
        device_map={"": 0}, offload_embedding=offload, text_only=True)
    model = FastLanguageModel.get_peft_model(
        model, r=E("ADAPTER_R", 16), lora_alpha=E("ADAPTER_ALPHA", 32), lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=3407)
    print(f"embeddings offloaded to CPU: {offload}; compute dtype {compute_dtype(model)}")
    return model, tok


def compute_dtype(model):
    """The half-precision type the model computes in (from the unquantized output layer): torch.float16 or
    torch.bfloat16. The trainer and the probe both autocast to it (19 Oct: the probe without autocast hit
    'expected mat1 and mat2 to have the same dtype, BFloat16 != Half')."""
    import torch
    try:
        dt = model.get_output_embeddings().weight.dtype
    except Exception:  # noqa: BLE001
        dt = None
    if dt not in (torch.float16, torch.bfloat16):
        dt = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    return dt


def n_tokens(tok, messages):
    return len(tok(tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=False))["input_ids"])


def fit(tok, messages, max_seq, min_keep=200):
    """messages trimmed to max_seq tokens by cutting the middle of the longest non-assistant message;
    (messages, n_tokens, trimmed?) or (None, n, False) when it cannot be done."""
    n = n_tokens(tok, messages)
    if n <= max_seq:
        return messages, n, False
    msgs = [dict(m) for m in messages]
    mark_len = len(tok(MARK, add_special_tokens=False)["input_ids"])
    for _ in range(4):                                   # decode/re-encode can shift a few tokens; re-check
        cand = [i for i, m in enumerate(msgs) if m.get("role") != "assistant"]
        if not cand:
            return None, n, False
        i = max(cand, key=lambda j: len(msgs[j].get("content") or ""))
        ids = tok(msgs[i].get("content") or "", add_special_tokens=False)["input_ids"]
        keep = len(ids) - (n - max_seq) - 16 - mark_len
        if keep < min_keep:
            return None, n, False
        head = (keep * 2) // 3                            # instructions first, the question near the end
        msgs[i]["content"] = tok.decode(ids[:head]) + MARK + tok.decode(ids[len(ids) - (keep - head):])
        n = n_tokens(tok, msgs)
        if n <= max_seq:
            return msgs, n, True
    return None, n, False


def probe(a):
    """Forward + backward on random tokens at growing lengths; peak GPU memory for each. No training."""
    import torch
    lengths = [int(x) for x in os.environ.get("ADAPTER_PROBE", "1024,1536,2048,2560,3072").split(",")]
    model, tok = load(a.base, max(lengths))
    model.train()
    total = torch.cuda.get_device_properties(0).total_memory / 2**30
    print(f"GPU {torch.cuda.get_device_name(0)}, {total:.1f} GiB; after loading "
          f"{torch.cuda.memory_allocated() / 2**30:.1f} GiB allocated")
    hi = min(len(tok), 100000)
    dt = compute_dtype(model)
    for L in lengths:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        ids = torch.randint(100, hi, (1, L), device="cuda")
        oom = False
        try:
            with torch.autocast("cuda", dtype=dt):          # as the trainer runs each step
                loss = model(input_ids=ids, labels=ids).loss
            loss.backward()
            peak = torch.cuda.max_memory_allocated() / 2**30
            print(f"  length {L:5d}: OK   peak {peak:5.1f} GiB  (headroom {total - peak:4.1f} GiB)", flush=True)
        except torch.OutOfMemoryError:
            oom = True
            print(f"  length {L:5d}: OUT OF MEMORY", flush=True)
        except RuntimeError as e:
            oom = True
            print(f"  length {L:5d}: ERROR {str(e)[:160]}", flush=True)
        loss = None
        model.zero_grad(set_to_none=True)
        if oom:
            break
    print("Optimizer states and step-to-step variation need ~1.5 GiB more than these peaks: choose the longest "
          "length with headroom >= 1.5 GiB as ADAPTER_MAX_SEQ in adapter.env.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--out")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--base", default=os.environ.get("ADAPTER_BASE_HF", "unsloth/Qwen3.8-27B-unsloth-bnb-4bit"))
    a = ap.parse_args()
    if a.probe:
        return probe(a)
    if not (a.data and a.out):
        sys.exit("--data and --out are required (or --probe)")
    r, alpha = E("ADAPTER_R", 16), E("ADAPTER_ALPHA", 32)
    epochs, lr = E("ADAPTER_EPOCHS", 2.0), E("ADAPTER_LR", 1e-4)
    max_seq, bs, accum = E("ADAPTER_MAX_SEQ", 2048), E("ADAPTER_BATCH", 1), E("ADAPTER_ACCUM", 8)
    user_tag = os.environ.get("ADAPTER_USER_TAG", "<|im_start|>user\n")
    asst_tag = os.environ.get("ADAPTER_ASSISTANT_TAG", "<|im_start|>assistant\n")
    mask = os.environ.get("ADAPTER_MASK", "1") == "1"
    trim = os.environ.get("ADAPTER_TRIM", "1") == "1"

    # Unsloth BEFORE trl/datasets: it patches SFTTrainer (e.g. to accept tokenizer=). 19 Oct: importing trl first
    # left the trainer unpatched -> "SFTTrainer.__init__() got an unexpected keyword argument 'tokenizer'".
    import unsloth  # noqa: F401
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    rows = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
    print(f"{len(rows)} training rows from {a.data}; base {a.base}; max_seq {max_seq}")
    model, tok = load(a.base, max_seq)
    model.print_trainable_parameters()

    lens, texts, n_trim, n_drop = [], [], 0, 0
    for x in rows:
        orig = n_tokens(tok, x["messages"])
        lens.append(orig)
        if trim:
            msgs, n, was_trimmed = fit(tok, x["messages"], max_seq)
        else:
            msgs, n, was_trimmed = x["messages"], orig, False
        if msgs is None or n > max_seq:
            n_drop += 1
            continue
        n_trim += was_trimmed
        texts.append(tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False))
    srt = sorted(lens)
    pct = lambda p: srt[min(len(srt) - 1, int(p * len(srt)))]  # noqa: E731
    print(f"row lengths (tokens): median {pct(0.5)}, p90 {pct(0.9)}, max {srt[-1]}")
    print(f"rows: {len(texts)} kept ({n_trim} trimmed to fit {max_seq} tokens), {n_drop} left out")
    if not texts:
        sys.exit(f"no training rows fit in {max_seq} tokens")
    ds = Dataset.from_list([{"text": t} for t in texts])
    import torch
    dt = compute_dtype(model)
    trainer = SFTTrainer(                                # keyword layout as in train_lora_unsloth.py
        model=model, tokenizer=tok, train_dataset=ds, dataset_text_field="text", max_seq_length=max_seq,
        args=SFTConfig(output_dir=os.path.join(a.out, "_ckpt"), per_device_train_batch_size=bs,
                       gradient_accumulation_steps=accum, num_train_epochs=epochs, learning_rate=lr,
                       lr_scheduler_type="cosine", warmup_ratio=0.05, logging_steps=5, save_strategy="no",
                       optim=os.environ.get("ADAPTER_OPTIM", "adamw_8bit"),
                       fp16=dt == torch.float16, bf16=dt == torch.bfloat16,   # 19 Oct: explicit, = the model's
                       report_to=[], seed=3407))
    masked = False
    if mask:
        try:
            from unsloth.chat_templates import train_on_responses_only
            trainer = train_on_responses_only(trainer, instruction_part=user_tag, response_part=asst_tag)
            masked = True
        except Exception as e:  # noqa: BLE001
            print(f"note: answer-only loss not applied ({str(e)[:120]}); training on whole texts as the original did",
                  file=sys.stderr)
    t0 = time.time()
    stats = trainer.train()
    os.makedirs(a.out, exist_ok=True)
    model.save_pretrained(a.out)
    tok.save_pretrained(a.out)
    json.dump({"rows": len(rows), "rows_trained": len(texts), "rows_trimmed": n_trim, "rows_left_out": n_drop,
               "base": a.base, "r": r, "alpha": alpha, "epochs": epochs, "lr": lr, "max_seq": max_seq,
               "batch": bs, "accum": accum, "answer_only_loss": masked,
               "train_loss": getattr(stats, "training_loss", None), "minutes": round((time.time() - t0) / 60, 1)},
              open(os.path.join(a.out, "train_log.json"), "w"), indent=1)
    print(f"adapter saved to {a.out} ({(time.time() - t0) / 60:.0f} min, loss {getattr(stats, 'training_loss', '?')})")


if __name__ == "__main__":
    main()
