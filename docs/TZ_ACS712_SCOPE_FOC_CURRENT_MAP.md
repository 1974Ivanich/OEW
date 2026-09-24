# TZ: ACS712 20A × TAO3104A → FOC current map (PK-3, bench session B)

**Branch:** `ai2/acs712-scope-foc-map`
**Base:** `origin/main` @ `11ad97d`
**Date:** 2026-09-23
**Author:** ai2 (Hermes), PK-2

## 1. ZACHEM ETOT ETAP NUZHEN

Karta tokov `OewCurrentMap` (6 sektorov × 2 okna = 12 zapisej `CurrentReconEntry`) soderzhit
koefficienty rekonstruktsii `[iu_ma, iv_ma]` iz DC-link shuntov ADC:

```
iu_ma = (m00 * idc1 + m01 * idc2) / 1000
iv_ma = (m10 * idc1 + m11 * idc2) / 1000
iw_ma = -(iu_ma + iv_ma)          <- KCL
```

Koefficienty `m00...m11` dolzhny byt' **otkalibrovany po nezavisimomu referensu**.
Etot referens - osstsillograf TAO3104A (PK-3) + datchiki toka ACS712 20A na fazakh U i V.

Shtatnyj shunt-ADC trakt (DC-link, `calc_dc_shunt_ma`) rabotaet v diapazone **0...10 A** i ispol'zuet
`ADC_DC_SHUNT_UV_PER_A = 66 mV/A` (shunt `current_sense_resistance_ohms × Vcc`). Na tokakh **< 1 A**
u nego SNR <= 1 (shum 100 mV pp, poleznyj signal 13...91 mV pri 0.13...0.91 A).
Poetomu dlya FOC na malykh tokakh (FOC_START, V/f) masshtab shunt-ADC kvalifitsiruetsya
**otdel'nym traktom** (TZ-REF-01, DRAFT): osstsillograf + ACS712.

## 2. KONTEXT I OGRANICHENIYA

| Fakt | Znachenie |
|---|---|
| Datchiki toka | **ACS712 20A** (Allegro Microsystems), chuvstvitel'nost' **100 mV/A** (typ), Vcc=5 V, nul' pri Vcc/2 = 2.5 V |
| Opornoe napryazhenie ACS712 | Vcc stenda primerno 5020 mV (iz `acs712_nohv_20260904T040619Z.zip`, acceptance record) |
| Izmerennyj nul' ACS712 U | 2680 mV (offset = Vcc/2 - 60 mV) |
| Izmerennyj nul' ACS712 V | 2661 mV (offset = Vcc/2 - 19 mV) |
| Podtverzhdyonnyj diapazon tokov stenda | 0.13...0.91 A (STEP_A_ACCEPTANCE.md) |
| Shum ACS712 U / V | 100 mV pp / 90 mV pp (v predelakh <=110 mV pp guideline) |
| Chuvstvitel'nost' ACS712 | 100.0 mV/A (oba kanala, podtverzhdena) |
| Dopuskaemyj SNR | >= 10 (TZ-REF-01 R2) - **NE DOSTIGNUT** (tekushchij SNR = 0.13...0.91 / 0.1 <= 9.1) |
| Rezhim stenda | **DC-link otklyuchen** (< 1 V), bez motora, bez HV |

**Vazhno:** na tekushchej konfiguratsii stenda (DC-link otklyuchen) minimal'nyj tok ~0.13 A,
shum ACS712 ~100 mV pp -> SNR ~ 1.3. Eto **nizhe** trebovaniya TZ-REF-01 R2 (>= 10).
Karta tokov, postroennaya na etikh dannykh, budet imet' **neopredelennost' masshtaba >= 10 %**.
Eto dopustimo kak **kvalifikatsiya tajminga** (tssep' sinkhronizatsii uzhe podtverzhdena), no
**masshtab FOC pridetsya utochnyat'** otdel'nym kalibrovochnym seansom pri DC-link >= 10 A.

## 3. TSEL'

1. Zakhvatit' waveforms tokov U i V s TAO3104A pri rabotayushchem PWM (profil' SYNT).
2. Sokhranit' syrye CSV s `time_s`, `ch_u_mv`, `ch_v_mv` i calibration metadata.
3. Sgenerirovat' **kalibrovochnyj JSON** `acs712_calibration.json` dlya `map_scope_ingest.py`.
4. Peredat' paket na PK-2 dlya ingest v `tools/map_scope_ingest.py --calib`.
5. Ubedit'sya, chto `m00...m11` koefficienty v `CurrentReconEntry` soglasovany s ACS712.

## 4. OBORUDOVANIE

| Komponent | Parametr |
|---|---|
| Ossillograf | OWON TAO3104A, SN 2306027, V3.0.0, USB libusb0, VID 0x5345 PID 0x1234 |
| PK-3 | Windows, USB k TAO3104A |
| Datchik U | ACS712 20A, shunt na faze U (vykhod OUT k osstsillografu) |
| Datchik V | ACS712 20A, shunt na faze V (analogichno) |
| Marker sinkhronizatsii | PB6 -> CH1 (TIM1 TRGO, PWM, 50 % duty), dlya vremennoj privyazki |
| Dopolnitel'nyj kanal | CH3 = DC-link voltage (dlya reference, optional) |

## 5. PODKLYUCHENIE

### 5.1. Skhema podklyucheniya ACS712 -> TAO3104A

```
                    +-----------------+
  Faznaya liniya U  | ACS712 20A      |
  ------------------+ IP+   OUT   IP- +--+---- Klemma U motora
                    |      (5.0V)      |  |
                    +-----------------+  |
                                         CH2 (TAO3104A): shunt U
                                         1X shchup, DC coupling
                                         masshtab: 50 mV/div

                    +-----------------+
  Faznaya liniya V  | ACS712 20A      |
  ------------------+ IP+   OUT   IP- +--+---- Klemma V motora
                    |      (5.0V)      |
                    +-----------------+

                                         CH3 (TAO3104A): shunt V
                                         1X shchup, DC coupling
```

### 5.2. VYBOR COUPLING

- **DC coupling** (rekomendatsiya): sokhranyaet DC-uroven', нужен для absolyutnogo znacheniya toka.
- Formula: I = (Vout - Vcc/2) / (100 mV/A).

### 5.3. PB6 -> CH1 (sinkhronizatsiya)

PB6 (TIM1 TRGO) formiruet impuls sinkhronizatsii:
- CHasto = PWM frequency = 5000 Hz (profil' SYNT)
- Dlitelnost' impulsa ≈ deadtime = 68 tikov × (1/170 MHz) ≈ 0.4 mks

Eto pozvolyaet:
- Opredelit' moment zakhvata waveform otnositel'no PWM-perioda.
- Vychislit' real'nuyu tochku vyborki ADC.

## 6. PARAMETRY OSSILLOSKOPA

| Parametr | Znachenie |
|---|---|
| Rezhim | DEPMEM (10K pts) ili SCREEN (1520 pts) |
| Timebase | 500 mks/div (dlya 5 kHz PWM - 5 periodov na ekrane) |
| Trigger | EDGE / CH1 (PB6) / RISE / Level = 50 % (2.5 V) / SINGLE |
| Acquire | Mode = SAMPLE |
| Kanaly | CH1=PB6 sync, CH2=U current, CH3=V current |

## 7. SKRIPT DLYA ZAKHVATA

Skript `scope_acs712_capture.py` - adaptatsiya `tools/tao3104a_cap.py` pod ACS712.

**Klyuchevye otlichiya:**
1. Chtenie **CH2** (U) i **CH3** (V), ne CH1.
2. Ispol'zovanie calibration JSON dlya preobrazovaniya mV -> mA.
3. Sokhranenie: `time_s`, `ch_u_mv`, `ch_v_mv`, `ch_sync_mv` (CH1).
4. Detektsiya bulk-IN truncation (V3.0.0 bug) i power-cycle rekomendatsiya.
5. Podderzhka DEPMEM i SCREEN rezhimov.

### 7.1. Algoritm preobrazovaniya mV -> mA

```python
def mv_to_ma(v_mv, v0_mv, sens_mv_per_a):
    """ACS712: Vout = Vcc/2 + (100 mV/A) * I -> I = (Vout - Vcc/2) / 0.100"""
    return (v_mv - v0_mv) / sens_mv_per_a   # mA
```

Iz acceptance record:
- Vcc = 5020 mV
- v0_U = 2680 mV -> sens_U = 100.0 mV/A
- v0_V = 2661 mV -> sens_V = 100.0 mV/A

### 7.2. Calibration JSON (dlya --calib)

```json
{
  "vcc_mv": 5020,
  "sensors": {
    "U": {
      "v0_mv": 2680,
      "sens_mv_per_a": 100.0
    },
    "V": {
      "v0_mv": 2661,
      "sens_mv_per_a": 100.0
    }
  }
}
```

### 7.3. CSV-vykhod (dlya --scope-waiver)

```
pulse,time_s,ch_sync_mv,ch_u_mv,ch_v_mv,ref_u_ma,ref_v_ma,scope_qualified
1,0.000000,2500.0,2680.0,2661.0,0.0,0.0,1
2,0.000200,2500.0,2685.0,2665.0,50.0,40.0,1
...
```

## 8. SINKHRONIZATSIYA S PWM

PB6 (TIM1 TRGO) formiruet impuls sinkhronizatsii:
- CHasto = PWM frequency = 5000 Hz
- Dlitelnost' impulsa ≈ deadtime = 68 tikov × (1/170 MHz) ≈ 0.4 mks

**Algoritm privyazki:**
1. Po waveform CH1 (sync) opredelit' moment perekhoda RISE -> fall.
2. Vychislit' fazovuyu zaderzhku: `phase = t_rise / T_pwm`.
3. Schitat' `TIM1 ARR = 999`, `ADC Trigger Offset = 12 tikov` (iz SYNTHETIC_PROFILE).
4. Tochka vyborki ADC: `t_adc = t_rise + (trigger_offset_ticks / TIM1_FREQ)`.
5. Vremya vyborki ADC = `t_adc` - eto **obshchij znamenatel'** dlya waveform scope i ADC-frejma.

## 9. REZHIM STENDA

| Parametr | Znachenie |
|---|---|
| Rezhim | MAP_CAPTURE SYNTHETIC_PROFILE (SYNT) |
| PWM frequency | 5000 Hz |
| TIM1 ARR | 999 |
| TIM1_freq (APB2) | 170 MHz |
| PWM frequency (tochno) | F_PWM = 170 MHz / (ARR + 1) / PSC, gde PSC - preddelitel' |
| CCR U / V / W | 500 / 500 / 500 (50 % duty) |
| Deadtime | 68 tikov ≈ 0.4 mks |
| ADC Trigger Offset | 12 tikov |
| Pulse count | 16 |
| Timeout | 20 s |
| max_abs_shunt_ma | 10 000 mA (10 A) |

**Primechanie:** proverit' real'nuyu chastotu PWM. Iz `MAP_CAPTURE_SYNTHETIC_PWM_HZ = 5000` =>
real'naya chastota PWM: **5 kHz** (preddelitel' 34: 170 MHz / 34 = 5 MHz, ARR = 999 -> 5 MHz / 1000 = 5 kHz).
UTOCHNIT' u pol'zovatelya.

## 10. CHTO NE VKHODIT V ETOT TZ

1. Izmerenie pri DC-link >= 10 V i tokakh > 1 A (eto otdel'noe TZ, TZ-REF-01).
2. Kvalifikatsiya masshtaba toka dlya FOC (SNR < 10, sm. §2).
3. Zapusk dvigatelya ili FOC (tol'ko zakhvat waveform).
4. Izmerenie fazy W (W = -U - V, KCL).

## 11. KRITERII PRIYEMKI

| Kriterij | Kak proverit' |
|---|---|
| TAO3104A otdayot polnyj payload (> 1500 pts) | power-cycle, `scope_acs712_capture.py --probe`, ubedit'sya `body=3040` |
| CH2 i CH3 chitayutsya bez oshibok | `scope_acs712_capture.py --capture` -> CSV bez NaN |
| Calibration JSON korrekten | `map_scope_ingest.py --calib acs712_calibration.json --scope-waiver capture.csv` -> 0 fault |
| PB6 sinkhronizatsiya vidna na CH1 | osstsillogramma: meand 5 kHz, 50 % duty |
| 16 zapisej | CSV soderzhit 16 strok (pulse 1...16) |
| Bulk-IN truncation bug V3.0.0 obrabotan | sm. §7 (power-cycle rekomendatsiya) |

## 12. PLAN VYPOLNENIYA

```
PK-3: TAO3104A
  |
  +-- 1. Podklyuchit' ACS712 U -> CH2, V -> CH3, PB6 -> CH1
  +-- 2. Ustanovit' Zadig + driver libusb0 (esli eshche net)
  +-- 3. Skopirovat' scope_acs712_capture.py i requirements-tao3104a.txt
  +-- 4. pip install -r requirements-tao3104a.txt
  +-- 5. python scope_acs712_capture.py --list
  +-- 6. python scope_acs712_capture.py --probe        (ubedit'sya body=3040)
  +-- 7. python scope_acs712_capture.py --capture      (waveform CSV)
  +-- 8. Zapisat' acs712_calibration.json
  +-- 9. Proverit': python map_scope_ingest.py --calib acs712_calibration.json --scope-waiver capture.csv

PK-2: priyemka
  |
  +-- 10. git add / commit / push -> branch ai2/acs712-scope-foc-map
  +-- 11. PR -> review + merge v main
```

## 13. VYKHODNYE ARTEFAKTY

| Artefakt | Format | Naznachenie |
|---|---|---|
| `scope_capture.csv` | CSV | ref_u_mv, ref_v_mv, time_s dlya ingest |
| `acs712_calibration.json` | JSON | vcc_mv, sensors.U/V nul' i chuvstvitel'nost' |
| `scope_capture.preamble.txt` | tekst | SAMPLERATE, TIMEBASE, RUNSTATUS, Trig, FREQ |
| `campaign_raw/acs712_scope_<timestamp>.zip` | ZIP | arkhiv dlya peredachi na PK-2 |

## 14. FORMAT CSV DLYA MAP_SCOPE_INGEST.PY

```
pulse,time_s,ch_sync_mv,ch_u_mv,ch_v_mv,ref_u_ma,ref_v_ma,scope_qualified
1,0.000000000,2500.0,2680.0,2661.0,0.0,0.0,1
2,0.000200000,2500.0,2685.0,2665.0,50.0,40.0,1
...
```

`ref_u_ma = (ch_u_mv - 2680) / 100.0`
`ref_v_ma = (ch_v_mv - 2661) / 100.0`
`scope_qualified = 1` (sync viden, waveform stabilen)
