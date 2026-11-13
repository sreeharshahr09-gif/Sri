# Sidewall deflection analysis

Compares an inflated and a loaded 2D laser scan of a tire sidewall, taken in one
record: the stage scans down (inflated), reverses, and scans back up (loaded).

```bash
pip install -r requirements.txt
python -m sidewall data/<scan>.tsv --out output/<run> [--rim-end top|bottom] [--h-mm 5] [--load-kN 35]
```

Input: the two Excel rows pasted as tab-separated text (row 1 = laser reading x,
row 2 = stage height z), or a two-column x,z file. Readings >= 250 (the 300.15
out-of-range code) are treated as dropouts.

Outputs: `metrics.json`, `zones.csv`, `matched_profile.csv`, `overview.png`,
`data_quality.png`.

## Method

1. **Clean** - split at the stage turnaround, drop dropouts and sample-and-hold
   repeats (the laser updates slower than z is logged).
2. **Rigid band and deflection** - the textured band at the top of the scan
   moves vertically with no axial change. Its shift between scans, found by
   correlating its surface texture, is the vertical deflection.
3. **Smooth** - u (outward offset) is lightly smoothed; slope and curvature
   use a 45 mm Savitzky-Golay window. Shorter windows pick up lettering texture
   rather than bending.
4. **Match material points** - cords make the sidewall nearly inextensible, so
   equal arc length from the band edge ~ the same material in both scans.
   Displacement, rotation and curvature change are compared at equal arc length,
   not at equal stage height.
5. **Zones** - thirds of the matched arc: share of bulge, share of bending
   energy (integral of curvature change squared, uniform stiffness assumed),
   rotation, and surface strain = h x curvature change.

## Assumptions to check per test

- Which end is the rim (`--rim-end`); it only changes zone labels.
- h, the ply-to-surface distance used for strain (`--h-mm`).
- Arc-length matching is anchored at the rim band. Material that leaves the
  field of view at the other end shows up as `arc_length.deficit_mm`.
