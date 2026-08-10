#!/usr/bin/env python3
"""Прогон рассуждений через deepseek-r1: анализ OEW-коммутации."""
import json, urllib.request, time

def ollama_generate(model, prompt, system=None):
    payload = {"model": model, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.6, "num_ctx": 16384}}
    if system:
        payload["system"] = system
    req = urllib.request.Request("http://localhost:11434/api/generate",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.load(r)
    dt = time.time() - t0
    return d.get("response", ""), d.get("eval_count", 0), dt

CONTEXT = """Проект: OEW (Open-End Winding) привод на STM32G474RE, асинхронный двигатель.
Два инвертора STEVAL-IPM20B питают обмотки с двух концов, общий DC-link.
TIM1 (инвертор 1) в PWM mode 1 (CNT<CCR активен), TIM8 (инвертор 2) в PWM mode 2 (CNT>CCR активен), одинаковый CCR:
  d1u = d2u = 50 + vu*49/32768  (оба инвертора одинаково!)
  HIN_U1=1 (TIM1) <=> LIN_U2=1 (TIM8) -> ток по обмотке
  V_U = (2*CCR-ARR)*Vbus/ARR = (2d/100-1)*Vbus — линейно по duty
PWM 5 кГц center-aligned, ARR=999, PSC=16 (10 МГц таймер), dead-time 1500 нс (DTG=0xC0).
FOC 5 кГц: Clarke (2 датчика iu=i1, iv=i2, iw=-iu-iv), Park, PI dq (модульный оптимум), 
VoltageManager Q15 (Vmax_Q15=29490, 90% от 32767), InvPark -> Vu,Vv,Vw -> OEW: TIM1=50%+V/2, TIM8=50%-V/2, clamp 1..98%.
Асинхронный двигатель: observer использует Lσ (не Ls), компенсация перекрёстных связей vd+=ω*Lσ*Iq, vq-=ω*Lσ*Id.
Известные баги в прошлом: mode 1 на обоих = нет тока; mode 2 + разные CCR = V_U≈0 (оба плеча в одну сторону);
«не инверсные» LIN = сквозной ток = КЗ."""

QUESTION = """Проведи критический инженерный анализ OEW-коммутации. Ответь на русском:
1) Какие физические риски есть в схеме с общим DC-link и двумя инверторами при таком способе модуляции?
2) Почему dead-time 1500 нс и clamp duty 1..98% могут быть недостаточны, и какие аварийные сценарии возможны?
3) Что может пойти не так с zero-sequence током (iz) при 2-датчиковой Clarke (iw=-iu-iv), если iz≠0?
4) Какие 3 конкретные доработки прошивки ты бы предложил в первую очередь для защиты железа?
Будь конкретен и прагматичен, без воды."""

if __name__ == "__main__":
    model = "deepseek-r1:7b"
    print(f"[*] {model}: анализ OEW-коммутации...", flush=True)
    resp, ntok, dt = ollama_generate(model, f"{CONTEXT}\n\n{QUESTION}")
    print(f"[*] Готово: {ntok} токенов за {dt:.1f}с\n")
    print("=" * 70)
    print(resp)
    print("=" * 70)
