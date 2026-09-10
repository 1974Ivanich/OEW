# BOAR Amplitude Zones Adjustment

**Date:** 2026-09-10
**Campaign:** BOAR 60V (boar_60v_g4_20260907T172418Z)

## Current BOAR Campaign Data Analysis

Actual modulation ranges from campaign:

| Sector | Window 0 (mod Q15) | Window 1 (mod Q15) |
|--------|-------------------|-------------------|
| 0      | 7930..8454         | 8978..9503         |
| 1      | 8192..8454         | 9241..9503         |
| 2      | 8192..8454         | 9241..9503         |
| 3      | 7930..8454         | 8978..9503         |
| 4      | 7930..8454         | 8978..9503         |
| 5      | 8192..8454         | 9241..9503         |

**Summary:**
- Window 0: 7930..8454 Q15 (all sectors)
- Window 1: 8978..9503 Q15 (all sectors)

## Proposed PR #12 Zones (Not Suitable)

```text
W0: 6000 <= mod < 10000
W1: 10000 <= mod <= 14000
```

**Problem:** Window 1 data (8978..9503) is entirely below W1.min=10000. Current BOAR campaign would require rebuilding with higher modulation points to fit these zones.

## Adjusted Zones for BOAR Data

### Option A: Conservative Fit (Recommended)

```text
W0: 7500 <= mod < 9000
W1: 9000 <= mod <= 9600
```

**Rationale:**
- W0: 7500..9000 covers actual W0 range (7930..8454) with ~5% margin below and ~6% margin above
- W1: 9000..9600 covers actual W1 range (8978..9503) with ~0.2% margin below and ~1% margin above
- No overlap: W0.max (9000) < W1.min (9000) — strict inequality for W0, W1 inclusive
- Maintains reasonable margin for campaign variation

### Option B: Tight Fit

```text
W0: 7800 <= mod < 8500
W1: 8500 <= mod <= 9600
```

**Rationale:**
- W0: 7800..8500 covers actual W0 range (7930..8454) with ~1.6% margin below and ~0.5% margin above
- W1: 8500..9600 covers actual W1 range (8978..9503) with ~5% margin below, ~1% margin above
- Tighter fit reduces interpolation error (smaller region)
- May be too restrictive for future campaigns

### Option C: Original PR Zones with Campaign Rebuild

```text
W0: 6000 <= mod < 10000
W1: 10000 <= mod <= 14000
```

**Rationale:**
- Use PR #12 zones as-is
- Requires BOAR campaign rebuild with higher modulation points
- More expensive but provides wider coverage for future use

## Recommendation

**Option A (Conservative Fit)** is recommended because:
1. Fits current BOAR campaign data without rebuild
2. Provides reasonable margin for campaign variation
3. Maintains no-overlap property
4. Reduces interpolation error compared to very wide zones

## Implementation

To implement Option A, modify the qualification parameters in the campaign dataset:

```
regionq min=4 guard=0 margin=110 use_geometry=1 w0_mod_min=7500 w0_mod_max=9000 w1_mod_min=9000 w1_mod_max=9600
```

This should be applied in the dataset.txt or in the campaign manifest before running the geometry pipeline.
