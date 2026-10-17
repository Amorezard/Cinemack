#!/usr/bin/env python3
"""
v4 (14 Oct): train the movie-buff LoRA adapter for the nightly rebuild (rebuild_adapter.sh), from a
chat-format file made by adapter_data.py build.

Same base, loading pattern and LoRA settings as ~/movie-buff/train_lora_unsloth.py (the original
persona adapter: unsloth/Qwen3.8-27B-unsloth-bnb-4bit, text_only, r 16, alpha 32, all seven
projections), so a rebuilt adapter is the persona LoRA trained on more data, not a different kind of
model. Runs in the training venv (~/venvs/train-lora); rebuild_adapter.sh picks it via TRAIN_VENV.

    ~/venvs/train-lora/bin/python train_adapter.py --data builds/a20261015/train.jsonl --out builds/a20261015/lora

What differs from the original script, and why:
  max_seq 4096 (was 2048)  RAG prompts with tool data run to ~3k tokens; 2048 would cut them
  batch 1 x accum 8        same 8 rows per step as 2 x 4, half the memory at the longer length
  epochs 2 (was 10)        10 epochs suited 16 rows; with hundreds of rows they would overfit
  lr 1e-4 (was 2e-4)       gentler for the larger, mixed set
  loss on answers only     the long RAG prompts are not learned, only the replies (train_on_responses_only);
                           if the chat markers are not found it falls back to the original whole-text loss
All of these are settings (ADAPTER_*), so the original recipe is one adapter.env away.
"""
import argparse
import json
import os
import sys
import time

E = lambda k, d: type(d)(os.environ.get(k, d))  # noqa: E731


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default=os.environ.get("ADAPTER_BASE_HF", "unsloth/Qwen3.8-27B-unsloth-bnb-4bit"))
    a = ap.parse_args()
    r, alpha = E("ADAPTER_R", 16), E("ADAPTER_ALPHA", 32)
    epochs, lr = E("ADAPTER_EPOCHS", 2.0), E("ADAPTER_LR", 1e-4)
    max_seq, bs, accum = E("ADAPTER_MAX_SEQ", 4096), E("ADAPTER_BATCH", 1), E("ADAPTER_ACCUM", 8)
    user_tag = os.environ.get("ADAPTER_USER_TAG", "<|im_start|>user\n")
    asst_tag = os.environ.get("ADAPTER_ASSISTANT_TAG", "<|im_start|>assistant\n")
    mask = os.environ.get("ADAPTER_MASK", "1") == "1"

    from unsloth import FastLanguageModel
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    rows = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
    print(f"{len(rows)} training rows from {a.data}; base {a.base}")
    model, tok = FastLanguageModel.from_pretrained(      # the original script's "fixed loading pattern"
        model_name=a.base, max_seq_length=max_seq, load_in_4bit=True, dtype=None,
        device_map={"": 0}, offload_embedding=False, text_only=True)
    model = FastLanguageModel.get_peft_model(
        model, r=r, lora_alpha=alpha, lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=3407)
    model.print_trainable_parameters()

    texts = [tok.apply_chat_template(x["messages"], tokenize=False, add_generation_prompt=False) for x in rows]
    long = sum(1 for t in texts if len(tok(t)["input_ids"]) > max_seq)
    if long:
        print(f"note: {long} of {len(texts)} rows are longer than {max_seq} tokens and will be cut")
    ds = Dataset.from_list([{"text": t} for t in texts])
    trainer = SFTTrainer(                                # keyword layout as in train_lora_unsloth.py
        model=model, tokenizer=tok, train_dataset=ds, dataset_text_field="text", max_seq_length=max_seq,
        args=SFTConfig(output_dir=os.path.join(a.out, "_ckpt"), per_device_train_batch_size=bs,
                       gradient_accumulation_steps=accum, num_train_epochs=epochs, learning_rate=lr,
                       lr_scheduler_type="cosine", warmup_ratio=0.05, logging_steps=5, save_strategy="no",
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
    json.dump({"rows": len(rows), "base": a.base, "r": r, "alpha": alpha, "epochs": epochs, "lr": lr,
               "max_seq": max_seq, "batch": bs, "accum": accum, "answer_only_loss": masked,
               "train_loss": getattr(stats, "training_loss", None), "minutes": round((time.time() - t0) / 60, 1)},
              open(os.path.join(a.out, "train_log.json"), "w"), indent=1)
    print(f"adapter saved to {a.out} ({(time.time() - t0) / 60:.0f} min, loss {getattr(stats, 'training_loss', '?')})")


if __name__ == "__main__":
    main()
