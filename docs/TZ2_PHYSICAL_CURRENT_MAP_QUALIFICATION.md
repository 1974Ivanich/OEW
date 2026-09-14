# TZ-02 — минимальный контракт физической квалификации OEW current map

**Статус:** draft / ready for review
**База:** `2069d6728473c3d039f58fb078bfefb1d2baf3ea` (`origin/main` на 2026-09-14)
**Зависимость:** TZ-01 CLOSED / PASS; software admission и geometry certification уже существуют.
**Цель:** получить физически обоснованный verdict для текущей OEW current map без подмены physical qualification геометрической или численной self-consistency проверкой.

---

## 1. Главный инвариант

Физическая квалификация обязана измерять независимый физический токовый reference и только после этого идентифицировать observation matrix:

```text
y_ADC = H * i_phase + b + n
```

где:

- `y_ADC` — реальные ADC observations firmware (`idc1`, `idc2`, raw codes и derived values);
- `i_phase` — независимые физические фазные токи;
- `H` — идентифицируемая observation/reconstruction matrix;
- `b` — offset;
- `n` — noise.

Запрещён qualification loop:

```text
ADC -> reconstruction -> reference -> OLS -> PASS
```

`ref_u/ref_v/ref_w`, полученные из тех же ADC channels, не считаются `independent_reference`.

---

## 2. Независимый reference — обязательный admission gate

Кампания физической квалификации **не принимается**, если отсутствует полностью трассируемый блок `independent_reference`.

Минимально он должен содержать:

```text
independent_reference:
  qualified: true
  measurement_principle: <physical current measurement path>
  channels: <U/V/W mapping>
  instrument_id: <scope/probe/shunt chain identifier>
  calibration_id: <calibration certificate/revision>
  bandwidth_hz: <known measurement bandwidth>
  scale_u_ma_per_unit: ...
  scale_v_ma_per_unit: ...
  scale_w_ma_per_unit: ...
  timestamp_alignment: <method>
  electrical_independence: true
```

`qualified=true` само по себе недостаточно. Validator должен отвергать campaign, если отсутствует любой обязательный provenance field или невозможно доказать, что reference path не использует firmware ADC values.

Reference должен измерять физический ток в фазных проводниках независимо от PA0/PA1 ADC reconstruction path. Для каждого sample должна быть возможна однозначная привязка reference U/V/W к тому же PWM vector и моменту ADC sampling.

---

## 3. Разделение гейтов

TZ-02 не переопределяет существующие G0/G1/G2, а добавляет физический G3:

| Gate | Смысл | Verdict |
|---|---|---|
| G0 | artifact integrity / identity / CRC / provenance | должен быть PASS |
| G1 | semantic SVPWM sector/window geometry | должен быть PASS |
| G2 | sampling aperture / ADC settled / timing / margin | должен быть PASS для квалифицируемых rows |
| G3 | physical current-map qualification against independent reference | цель TZ-02 |

Ключевое правило:

```text
G1 PASS != G2 PASS != G3 PASS
```

`MAP_READY` существующего firmware loader не является автоматически G3 PASS.

---

## 4. P0 — instrumentation and provenance

До захвата samples зафиксировать:

- flashed firmware SHA256;
- source commit;
- board revision;
- PWM frequency и TIM1 ARR;
- ADC trigger identity/configuration;
- ADC calibration revision/signature;
- PWM vector capture (`CCR1/2/3`, ARR);
- ADC raw channels и timestamp;
- ADC settled / aperture / margin evidence;
- independent-reference instrument/calibration identity;
- channel-to-phase mapping U/V/W;
- reference sampling/trigger alignment;
- DC-link voltage и operating condition;
- campaign/profile/qualification revision.

Любая неопределённость provenance → **BLOCKED**, не PASS.

---

## 5. P1 — физическая campaign matrix

Кампания должна покрывать все `6 × 2 = 12` sector/window rows, которые реально используются map selector.

Для каждой row обязательны:

1. несколько interior samples;
2. lower-window/region edge evidence;
3. upper-window/region edge evidence;
4. samples с отличающимися токовыми возбуждениями, достаточными для идентификации двух независимых входов;
5. raw ADC + PWM + independent reference в одном sample record.

Если row присутствует только структурно, но не имеет физического independent-reference evidence, она считается **NOT QUALIFIED**.

Старый synthetic `samples.jsonl` не может быть использован для G3 PASS.

---

## 6. P2 — идентификация физической модели

Для каждой `(sector, window)` определить:

```text
x = [idc1, idc2]
y = [i_phase_a, i_phase_b]
```

и оценить:

```text
y_hat = H * x + b
```

Отчёт обязан содержать минимум:

- число training samples;
- число independent holdout samples;
- `H` для каждой row;
- offset/bias;
- RMS residual;
- maximum absolute residual;
- holdout RMS/max;
- KCL residual независимого reference;
- determinant / rank metric;
- condition metric;
- residual distribution;
- ADC saturation/outlier count;
- coverage по sector/window/modulation amplitude.

Использование существующего `MapMeasurement_SolveM()` допустимо как численного solver-а **только после прохождения independent-reference admission gate**. Сам solver не доказывает независимость reference.

---

## 7. P3 — сравнение с firmware map

После идентификации `H` сравнить физически полученную модель с coefficients, которые реально войдут в firmware map:

```text
H_identified  <->  M_firmware
```

Сравнение выполняется по тем же phase mappings, units/scaling и coefficient convention.

Нельзя объявлять PASS только по хорошему fit `H_identified`; необходимо доказать, что firmware coefficients соответствуют физически идентифицированной системе в пределах **заранее утверждённых qualification limits**.

Если qualification limits отсутствуют, неоднозначны или меняются после просмотра результатов → **BLOCKED**.

---

## 8. P4 — geometry и interpolation

G3 не заменяет geometry certification.

Для каждой из 12 rows необходимо одновременно иметь:

```text
geometry membership
+ sampling feasibility
+ independent physical reference
+ identification evidence
+ held-out edge evidence
```

Наличие только tolerance box/rectangle не является доказательством физической пригодности региона.

Boundary samples должны следовать принятой geometry ownership policy. Если boundary policy требует `BOUNDARY_UNTESTED`, отсутствие отдельной boundary evidence остаётся BLOCKED/UNTESTED, а не PASS.

---

## 9. Verdict rules

### PASS
Только если одновременно:

- G0 PASS;
- G1 PASS;
- G2 PASS для всех required rows;
- `independent_reference` полностью provenance-qualified;
- все 12 rows имеют физическую evidence;
- идентификация `H` имеет достаточную rank/conditioning;
- fit и holdout проходят утверждённые limits;
- independent reference KCL и quality gates проходят;
- `H_identified` согласуется с firmware `M` в пределах утверждённых limits;
- geometry/interpolation edge evidence не имеет BLOCKED условий;
- firmware SHA и map artifact SHA полностью трассируемы.

### FAIL
Физическое противоречие, выход за qualification limit, плохая идентификация или подтверждённое несоответствие `H` ↔ `M`.

### BLOCKED
Недостаёт independent reference, provenance, edge/holdout evidence, calibration identity, qualification limits или необходимой физической coverage.

### NOT QUALIFIED
Campaign может быть математически/геометрически корректной, но не имеет права использоваться как физическое доказательство карты.

---

## 10. Что НЕ является G3 доказательством

Следующие результаты сами по себе не дают physical PASS:

- CRC / wire decode PASS;
- identity/provenance PASS;
- `MAP_READY`;
- geometry certifier PASS;
- 12/12 rows;
- ADC settled/scope qualified;
- хороший OLS residual;
- KCL, если KCL построен из тех же ADC-derived values;
- synthetic `ref_u/ref_v/ref_w`;
- совпадение с заранее заданными coefficients;
- один успешный `FOC_Start()`.

---

## 11. Минимальный выходной пакет TZ-02

```text
TZ2_<campaign>/
  manifest.json
  samples.jsonl
  independent_reference.json
  firmware_manifest.json
  map_artifact.bin
  map_artifact.sha256
  physical_model.json
  qualification_report.md
  qualification_report.json
```

`firmware_manifest.json` обязан содержать commit и SHA256 реально прошитого образа.

`qualification_report.json` обязан иметь machine-readable:

```text
status = PASS | FAIL | BLOCKED | NOT_QUALIFIED
```

и отдельные статусы G0/G1/G2/G3.

---

## 12. Safety boundary

TZ-02 не разрешает обход существующего `MapCommissioning_LoadMeasured()`, `CurrentMap_LoadMeasured()`, protection или PWM gates.

Физическая квалификация выполняется на commissioning path. Production enablement остаётся отдельным решением после получения G3 PASS.

Отдельно от TZ-02 должна быть закрыта обнаруженная в audit atomicity-проблема replacement path: невалидная замена не должна очищать ранее действующую карту, **identity которой всё ещё совпадает с живой**; при дрейфе identity карта инвалидируется (fail-closed). Закрыто: `2b2efdd`, принято в `main` (`c494338`).

---

## 13. Минимальный следующий эксперимент

Первый реальный run не должен пытаться сразу доказать всю карту. Сначала выполнить **P0 + P1 на одной-двух representative rows** и проверить, что pipeline действительно получает независимый U/V/W reference, синхронизированный с ADC sample.

Только после успешной проверки provenance и measurement chain расширять campaign до всех 12 rows.

**Итоговый принцип:** сначала доказать независимость измерительного reference, затем идентифицировать `H`, затем сравнивать `H` с firmware map `M`. Не наоборот.
