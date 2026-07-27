import numpy as np, json, hashlib

load, inflation_pressure = 40000, 827e3
speed = 40*1000/3600
contact_patch_length, peak_strain = 0.132, 2500
inflated_radius, tire_width = 0.53, 0.27

def strain(x, y, ps, L, W):
    sx_t, sx_c, K, sy = L/4.0, L/1.5, 0.4, W/2.5
    fx = (np.exp(-(x**2)/(2*sx_t**2)) - K*np.exp(-(x**2)/(2*sx_c**2)))/(1-K)
    return ps*fx*np.exp(-(y**2)/(2*sy**2))

print("=== 4b. TRUE size of the truncation discontinuity ===")
circ = 2*np.pi*inflated_radius
dists = np.linspace(0, circ, 500); ctr = circ/2
xrel = dists-ctr
raw = strain(xrel, 0.0, peak_strain, contact_patch_length, tire_width)
half_inf = contact_patch_length*2.5
mask = ~((ctr-half_inf <= dists) & (dists <= ctr+half_inf))
print(f"truncation boundary at |x| = {half_inf*1000:.0f} mm")
print(f"analytic strain exactly at boundary = {strain(half_inf,0.0,peak_strain,contact_patch_length,tire_width):.3f} ue")
print(f"largest |strain| that gets zeroed    = {np.abs(raw[mask]).max():.3f} ue  <- size of the C0 jump")
print(f"=> jump is {np.abs(raw[mask]).max()/peak_strain*100:.3f}% of peak: numerically harmless,")
print("   but it is a real discontinuity in dQ/dt (current) which is what a harvester circuit sees.")
d = np.gradient(np.where(mask,0.0,raw), dists)
print(f"   max |d(strain)/dx| from the truncation step = {np.abs(np.diff(np.where(mask,0.0,raw))).max():.3f} ue/sample")

print()
print("=== 9. INTEGRAL / NET-CHARGE CHECK (does the waveform return to zero charge?) ===")
xs = np.linspace(-1.665, 1.665, 2000001)
f = strain(xs, 0.0, peak_strain, contact_patch_length, tire_width)
print(f"integral of strain over one revolution = {np.trapezoid(f, xs)*1e3:.2f} ue*mm")
sx_t, sx_c, K = contact_patch_length/4, contact_patch_length/1.5, 0.4
print(f"analytic: (sqrt(2pi)/(1-K))*(sigma_t - K*sigma_c) = "
      f"{np.sqrt(2*np.pi)/(1-K)*(sx_t-K*sx_c)*peak_strain*1e3:.2f} ue*mm")
print("  -> NOT zero. A rolling material point must return to its initial length each")
print("     revolution, so the strain history should integrate to ~0 over the revolution.")
print(f"     tension area / compression area = "
      f"{np.trapezoid(np.clip(f,0,None),xs)/abs(np.trapezoid(np.clip(f,None,0),xs)):.2f} (should be ~1.0)")

print()
print("=== 10. 2D MAP DOMAIN vs REQUIREMENTS 1 & 3 ===")
zc = 0.0482
print(f"2D map x-domain = [-66, +66] mm (exactly the contact patch)")
print(f"compression visible in map only for 48.2 < |x| < 66 mm -> INSIDE the patch")
print("requirements 1 & 3 ask for compression BEFORE ENTERING and AFTER EXITING;")
print("the map never shows x outside the patch, so the pre/post-entry zones are off-canvas.")

print()
print("=== 11. STALE OUTPUTS: are cell 5 and cell 10 images identical? ===")
nb = json.load(open('/root/.claude/uploads/c18e29b7-74aa-5885-afff-d5359261d344/5526c6b9-Piezo_artifact.ipynb'))
def img(c):
    for o in nb['cells'][c].get('outputs',[]):
        if o.get('output_type')=='display_data':
            return hashlib.md5(o['data']['image/png'].encode()).hexdigest()
print(f"cell 5  (exec 7) charge plot md5 = {img(5)}")
print(f"cell 10 (exec 4) charge plot md5 = {img(10)}")
print(f"identical? {img(5)==img(10)}  -> cell 10 displays output from an EARLIER model state")
print(f"cell execution order as saved: {[ (i,nb['cells'][i].get('execution_count')) for i in range(len(nb['cells'])) if nb['cells'][i]['cell_type']=='code']}")

print()
print("=== 12. UNUSED / DEAD CODE ===")
src = "\n".join("".join(c['source']) for c in nb['cells'] if c['cell_type']=='code')
for name in ['speed','Axes3D','inflated_radius','patch_area_piezo_width','sns','scale_contact_patch_length']:
    print(f"  {name:28s} occurrences = {src.count(name)}")
print("  NOTE: `speed` appears only in its own definition -> never influences any result.")
