#!/usr/bin/env python3
"""Прогон кода проекта через локальную Ollama: код-ревью main.c."""
import json, urllib.request, sys, time

def ollama_generate(model, prompt, system=None):
    payload = {"model": model, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.2, "num_ctx": 16384}}
    if system:
        payload["system"] = system
    req = urllib.request.Request("http://localhost:11434/api/generate",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    dt = time.time() - t0
    return d.get("response", ""), d.get("eval_count", 0), dt

if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5-coder:7b"
    path = sys.argv[2] if len(sys.argv) > 2 else "main.c"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        code = f.read()
    system = ("Ты — старший инженер по прошивкам STM32 (Cortex-M4, CMSIS без HAL). "
              "Отвечай на русском. Находи реальные баги: гонки данных, неправильные "
              "регистры, выход за границы массивов, проблемы с прерываниями, dead-time, "
              "ADC-синхронизацию. Формат: список проблем по важности, каждая с указанием "
              "функции и строки, потом краткий вердикт. Не выдумывай проблемы.")
    prompt = (f"Проведи код-ревью этого файла прошивки STM32G474 (FOC, OEW-коммутация, "
              f"CMSIS-регистры, без HAL):\n\n```c\n{code}\n```")
    print(f"[*] {model}: ревью {path} ({len(code)} символов)...", flush=True)
    resp, ntok, dt = ollama_generate(model, prompt, system)
    print(f"[*] Готово: {ntok} токенов за {dt:.1f}с\n")
    print("=" * 70)
    print(resp)
    print("=" * 70)
