"""Reproduce the notebook's model exactly and measure its properties."""
import numpy as np
from scipy.optimize import brentq

# ---- verbatim parameters from notebook cell 2 ----
load = 40000
inflation_pressure = 827e3
speed = 40 * 1000 / 3600
contact_patch_length = 0.132
peak_strain = 2500
inflated_radius = 0.53
tire_width = 0.27
d33 = 550e-12
youngs_modulus = 24.4e9
patch_w = 10e-3
patch_l = 132e-3
patch_area_piezo = patch_w * patch_l


def strain(x, y, peak_strain, L, W):
    sx_t = L / 4.0
    sx_c = L / 1.5
    K = 0.4
    sy = W / 2.5
    fx = (np.exp(-(x**2) / (2 * sx_t**2)) - K * np.exp(-(x**2) / (2 * sx_c**2))) / (1 - K)
    fy = np.exp(-(y**2) / (2 * sy**2))
    return peak_strain * fx * fy


print("=" * 70)
print("1. CONTACT PATCH GEOMETRY CONSISTENCY")
print("=" * 70)
A_req = load / inflation_pressure
print(f"Load/pressure implied contact area   = {A_req*1e6:,.0f} mm^2")
print(f"Notebook patch (132mm x 270mm)       = {contact_patch_length*tire_width*1e6:,.0f} mm^2")
print(f"-> implied pressure over that area   = {load/(contact_patch_length*tire_width)/1e3:,.0f} kPa "
      f"({load/(contact_patch_length*tire_width)/inflation_pressure:.2f}x inflation)")
for w_mm in (270, 210):
    print(f"   L needed for equilibrium @ w={w_mm}mm : {A_req/(w_mm/1000)*1000:.0f} mm")

print()
print("=" * 70)
print("2. SHAPE OF f_x : where are the compression zones?")
print("=" * 70)
sx_t, sx_c, K = contact_patch_length/4, contact_patch_length/1.5, 0.4
fx = lambda x: (np.exp(-(x**2)/(2*sx_t**2)) - K*np.exp(-(x**2)/(2*sx_c**2)))/(1-K)
zc = brentq(fx, 0.001, 0.15)
xs = np.linspace(0, 0.4, 400000)
v = fx(xs)
xmin = xs[np.argmin(v)]
print(f"contact patch half-length            = {contact_patch_length/2*1000:.1f} mm")
print(f"tension->compression zero crossing   = {zc*1000:.1f} mm  "
      f"({'INSIDE' if zc < contact_patch_length/2 else 'outside'} the contact patch)")
print(f"peak compression at x                = {xmin*1000:.1f} mm, value {v.min()*peak_strain:.0f} ue "
      f"({abs(v.min())*100:.0f}% of peak tension)")
print(f"fraction of patch length in tension  = {2*zc/contact_patch_length*100:.0f}%")
print(f"fraction of patch length in COMPRESSION (should be ~0) = {(1-2*zc/contact_patch_length)*100:.0f}%")
print(f"strain at contact edge x=+-L/2       = {fx(contact_patch_length/2)*peak_strain:.0f} ue")
# FWHM of tensile peak
half = brentq(lambda x: fx(x)-0.5, 1e-4, zc)
print(f"tensile peak FWHM                    = {2*half*1000:.1f} mm vs patch {contact_patch_length*1000:.0f} mm")

print()
print("=" * 70)
print("3. TREAD-WIDTH PROFILE f_y  (docstring claims ~37% at y=W/2.5)")
print("=" * 70)
sy = tire_width/2.5
fy = lambda y: np.exp(-(y**2)/(2*sy**2))
print(f"sigma_y = W/2.5                      = {sy*1000:.0f} mm")
print(f"f_y at y = W/2.5 = {sy*1000:.0f} mm      = {fy(sy)*100:.1f}%   <-- docstring says 37%")
print(f"f_y at shoulder y = W/2 = {tire_width/2*1000:.0f} mm  = {fy(tire_width/2)*100:.1f}%  "
      f"(strain still {fy(tire_width/2)*peak_strain:.0f} ue at the very edge)")
print(f"y where f_y = 0.368 (1/e)            = {np.sqrt(2)*sy*1000:.0f} mm")

print()
print("=" * 70)
print("4. STRAIN-HISTORY HARD ZEROING (requirement 6: no discontinuities)")
print("=" * 70)
circ = 2*np.pi*inflated_radius
n = 500
dists = np.linspace(0, circ, n)
ctr = circ/2
half_inf = contact_patch_length*2.5
xrel = dists - ctr
raw = strain(xrel.reshape(1, -1), np.array([0.0]).reshape(-1, 1),
             peak_strain, contact_patch_length, tire_width)[0]
hist = raw.copy()
mask = ~((ctr-half_inf <= dists) & (dists <= ctr+half_inf))
hist[mask] = 0.0
print(f"revolution circumference             = {circ*1000:.0f} mm")
print(f"samples per revolution               = {n}  -> spacing {circ/n*1000:.2f} mm")
print(f"samples inside the contact patch     = {int(((np.abs(xrel) <= contact_patch_length/2)).sum())}  <-- resolution of the whole event")
print(f"discontinuity size at truncation     = {abs(raw[mask].max()):.2f} ue "
      f"(small, but a true C0 break)")
print(f"peak of sampled history              = {hist.max():.0f} ue (target {peak_strain})  "
      f"-> undersampling error {100*(peak_strain-hist.max())/peak_strain:.1f}%")
print(f"min of sampled history               = {hist.min():.0f} ue")

print()
print("=" * 70)
print("5. TIME / SPEED  (parameter `speed` is defined but never used)")
print("=" * 70)
omega = speed/inflated_radius
print(f"speed                                = {speed:.2f} m/s")
print(f"rotational frequency                 = {omega/(2*np.pi):.2f} Hz -> period {2*np.pi/omega*1000:.0f} ms")
print(f"contact patch residence time         = {contact_patch_length/speed*1e3:.1f} ms")
print(f"=> harvester pulse bandwidth ~        {1/(contact_patch_length/speed):.0f} Hz")

print()
print("=" * 70)
print("6. PIEZO CHARGE: point-strain vs strain averaged over the patch")
print("=" * 70)
Qpk = d33*youngs_modulus*patch_area_piezo*(peak_strain/1e6)
print(f"Q_peak as coded (point strain)       = {Qpk*1e6:.2f} uC")
# patch travels with the liner; it senses the mean strain over its own footprint
xf = np.linspace(-patch_l/2, patch_l/2, 2001)
yf = np.linspace(-patch_w/2, patch_w/2, 51)
XF, YF = np.meshgrid(xf, yf)
shift = np.linspace(-0.35, 0.35, 1401)
mean_eps = np.array([strain(XF+s, YF, peak_strain, contact_patch_length, tire_width).mean()
                     for s in shift])
print(f"Q_peak with 132x10mm area-averaging  = {d33*youngs_modulus*patch_area_piezo*mean_eps.max()/1e6*1e6:.2f} uC")
print(f"-> point-strain model overestimates by {peak_strain/mean_eps.max():.1f}x "
      f"(patch length {patch_l*1000:.0f}mm == contact patch length {contact_patch_length*1000:.0f}mm)")
print(f"   averaged peak strain seen by patch = {mean_eps.max():.0f} ue (vs {peak_strain} point)")

print()
print("=" * 70)
print("7. CAPACITANCE / VOLTAGE / ENERGY")
print("=" * 70)
C = 1700*8.854e-12*patch_area_piezo/0.2e-3
print(f"C as coded (parallel-plate, t=0.2mm) = {C*1e9:.1f} nF")
print(f"V_peak = Q/C                         = {Qpk/C:.0f} V")
print(f"E/rev  = Q^2/2C                      = {Qpk**2/(2*C)*1e3:.2f} mJ")
print(f"P      = E * {omega/(2*np.pi):.2f} Hz                  = {Qpk**2/(2*C)*omega/(2*np.pi)*1e3:.1f} mW")
print("   (published MFC-in-tire harvesters report ~10s of uW to a few mW)")

print()
print("=" * 70)
print("8. LOAD/PRESSURE SCALING LAWS IN CELL 9")
print("=" * 70)
for F in (30000, 40000, 50000):
    Ls = contact_patch_length*(F/load)**0.5
    Ps = peak_strain*(F/load)
    Lphys = (F/inflation_pressure)/tire_width
    print(f"  F={F/1000:>2.0f} kN -> notebook L={Ls*1000:6.1f} mm, peak={Ps:6.0f} ue | "
          f"fixed-width physics L={Lphys*1000:6.1f} mm")
print("  Curvature argument: peak liner strain ~ h*(1/R) is set by FLATTENING, not load.")
for h in (1.0, 1.5, 2.0, 3.0):
    print(f"    neutral-axis offset h={h:.1f} mm -> eps = h/R = {h/1000/inflated_radius*1e6:.0f} ue")
