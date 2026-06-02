import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import Arc, FancyArrowPatch
import matplotlib.gridspec as gridspec

fig = plt.figure(figsize=(20, 22), facecolor='#1a1a2e')

gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.35, wspace=0.3)

COLORS = {
    'bg':         '#1a1a2e',
    'panel':      '#16213e',
    'front_out':  '#e94560',
    'front_in':   '#f5a623',
    'rear_out':   '#0f3460',
    'rear_in':    '#00b4d8',
    'truck':      '#90e0ef',
    'center':     '#ffffff',
    'offtrack':   '#ff6b6b',
    'grid':       '#2a2a4a',
    'text':       '#e0e0e0',
    'subtitle':   '#aaaacc',
}

def draw_truck(ax, cx, cy, angle_deg, L, T, color='#90e0ef', alpha=0.9):
    angle = np.radians(angle_deg)
    # truck body rectangle centered between axles
    w, h = T * 1.2, L
    corners = np.array([[-w/2, -h/2], [w/2, -h/2],
                         [w/2,  h/2], [-w/2,  h/2]])
    R = np.array([[np.cos(angle), -np.sin(angle)],
                  [np.sin(angle),  np.cos(angle)]])
    corners = (R @ corners.T).T + np.array([cx, cy])
    poly = plt.Polygon(corners, closed=True, facecolor=color,
                       edgecolor='white', alpha=alpha, linewidth=1.5, zorder=5)
    ax.add_patch(poly)


def plot_turning(ax, L, T, delta_deg, title, short=True):
    ax.set_facecolor(COLORS['panel'])
    for spine in ax.spines.values():
        spine.set_edgecolor(COLORS['grid'])

    delta = np.radians(delta_deg)
    R = L / np.tan(delta)          # radius to rear axle center

    R_ri = R - T / 2               # rear inner
    R_ro = R + T / 2               # rear outer
    R_fi = np.sqrt((R - T/2)**2 + L**2)   # front inner
    R_fo = np.sqrt((R + T/2)**2 + L**2)   # front outer
    off_track = R - np.sqrt(max(R**2 - L**2, 0))

    # Turning center O at (0, 0), truck starts with rear axle along x-axis
    O = np.array([0, 0])
    # rear axle center at (R, 0), truck faces +y direction
    rear_center = np.array([R, 0])
    front_center = np.array([R, L])

    # Draw arc paths
    theta_max = np.radians(120)
    theta = np.linspace(0, theta_max, 400)

    def arc(r, c=O):
        return c[0] + r * np.cos(theta), c[1] + r * np.sin(theta)

    # Wheel paths
    ax.plot(*arc(R_ri), color=COLORS['rear_in'],   lw=2.5, label=f'Rear Inner  = {R_ri:.1f}m', zorder=3)
    ax.plot(*arc(R_ro), color='#5e60ce',            lw=2.5, label=f'Rear Outer  = {R_ro:.1f}m', zorder=3)
    ax.plot(*arc(R_fi), color=COLORS['front_in'],   lw=2.5, label=f'Front Inner = {R_fi:.1f}m', zorder=3)
    ax.plot(*arc(R_fo), color=COLORS['front_out'],  lw=2.5, label=f'Front Outer = {R_fo:.1f}m', zorder=3)

    # Filled sweep band (front outer to rear inner = total swept width)
    theta_band = np.linspace(0, theta_max, 400)
    x_fo = R_fo * np.cos(theta_band)
    y_fo = R_fo * np.sin(theta_band)
    x_ri = R_ri * np.cos(theta_band[::-1])
    y_ri = R_ri * np.sin(theta_band[::-1])
    sweep_x = np.concatenate([x_fo, x_ri])
    sweep_y = np.concatenate([y_fo, y_ri])
    ax.fill(sweep_x, sweep_y, alpha=0.08, color='white', zorder=1)

    # Off-tracking arrow at 90 deg position
    ang90 = np.radians(90)
    x_fo90 = R_fo * np.cos(ang90)
    y_fo90 = R_fo * np.sin(ang90)
    x_ri90 = R_ri * np.cos(ang90)
    y_ri90 = R_ri * np.sin(ang90)
    # show off-tracking (front inner vs rear inner at same angular position)
    x_fi90 = R_fi * np.cos(ang90)
    y_fi90 = R_fi * np.sin(ang90)
    ax.annotate('', xy=(x_ri90, y_ri90), xytext=(x_fi90, y_fi90),
                arrowprops=dict(arrowstyle='<->', color=COLORS['offtrack'],
                                lw=2, mutation_scale=15))
    ax.text((x_ri90 + x_fi90)/2 + 0.5, (y_ri90 + y_fi90)/2,
            f'Off-track\n{off_track:.2f}m',
            color=COLORS['offtrack'], fontsize=9, fontweight='bold',
            va='center', ha='left')

    # Draw truck at start position (angle=0, rear at angle=0 on arc)
    start_angle = 0
    truck_x = R + 0
    truck_y = L / 2
    draw_truck(ax, truck_x, truck_y, 90, L, T, color=COLORS['truck'])

    # Draw truck at 60 deg arc position
    arc60 = np.radians(60)
    tx = R * np.cos(arc60)
    ty = R * np.sin(arc60)
    draw_truck(ax, tx, ty + L/2 * np.cos(arc60),
               90 - 60, L, T, color='#48cae4', alpha=0.6)

    # Turning center marker
    ax.plot(*O, 'o', color='white', markersize=8, zorder=10)
    ax.text(O[0] + 0.3, O[1] + 0.3, 'O\n(Turn\nCenter)',
            color='white', fontsize=8, fontweight='bold', va='bottom')

    # Wheelbase dimension line
    ax.annotate('', xy=(R + T/2 + 1.5, L), xytext=(R + T/2 + 1.5, 0),
                arrowprops=dict(arrowstyle='<->', color='yellow',
                                lw=1.5, mutation_scale=12))
    ax.text(R + T/2 + 2.2, L/2, f'L={L}m\n(WB)',
            color='yellow', fontsize=9, ha='left', va='center', fontweight='bold')

    # Radius dimension line for R
    ax.plot([O[0], rear_center[0]], [O[1], rear_center[1]],
            '--', color='white', alpha=0.4, lw=1, zorder=2)
    ax.text(R/2, -1.5, f'R={R:.1f}m', color='white', fontsize=9,
            ha='center', va='top', style='italic')

    # Grid
    ax.grid(True, color=COLORS['grid'], linewidth=0.5, alpha=0.5, zorder=0)

    # Axis
    margin = 3
    ax.set_xlim(-margin, R_fo + margin + 4)
    ax.set_ylim(-margin, R_fo + margin)
    ax.set_aspect('equal')

    # Legend
    leg = ax.legend(loc='upper left', fontsize=9,
                    facecolor='#0d0d1a', edgecolor='#444466',
                    labelcolor=COLORS['text'], framealpha=0.85)

    ax.set_title(title, color='white', fontsize=13, fontweight='bold', pad=10)
    ax.set_xlabel('meters', color=COLORS['subtitle'], fontsize=9)
    ax.set_ylabel('meters', color=COLORS['subtitle'], fontsize=9)
    ax.tick_params(colors=COLORS['subtitle'])

    # Stats box
    stats = (f"δ = {delta_deg}°  |  R = {R:.1f}m\n"
             f"Ri_rear={R_ri:.1f}m  Ro_rear={R_ro:.1f}m\n"
             f"Ri_front={R_fi:.1f}m  Ro_front={R_fo:.1f}m\n"
             f"Swept width = {R_fo - R_ri:.1f}m  |  Off-track = {off_track:.2f}m")
    ax.text(0.01, 0.01, stats, transform=ax.transAxes,
            fontsize=8.5, color='#ccccff',
            verticalalignment='bottom',
            bbox=dict(boxstyle='round,pad=0.4', facecolor='#0d0d1a',
                      edgecolor='#333355', alpha=0.85))

    return R_ri, R_ro, R_fi, R_fo, off_track


# ── Panel 1: Short wheelbase (L=6m) ─────────────────────────────────────────
ax1 = fig.add_subplot(gs[0, 0])
r1 = plot_turning(ax1, L=6, T=2, delta_deg=30,
                  title='Short Wheelbase  L = 6 m  (δ = 30°)')

# ── Panel 2: Long wheelbase (L=12m) ─────────────────────────────────────────
ax2 = fig.add_subplot(gs[0, 1])
r2 = plot_turning(ax2, L=12, T=2, delta_deg=30,
                  title='Long Wheelbase  L = 12 m  (δ = 30°)')

# ── Panel 3: Bar comparison ──────────────────────────────────────────────────
ax3 = fig.add_subplot(gs[1, 0])
ax3.set_facecolor(COLORS['panel'])

labels = ['Rear\nInner', 'Rear\nOuter', 'Front\nInner', 'Front\nOuter', 'Off-\nTracking']
vals6  = list(r1[:4]) + [r1[4]]
vals12 = list(r2[:4]) + [r2[4]]

x = np.arange(len(labels))
w = 0.35
b1 = ax3.bar(x - w/2, vals6,  w, label='L=6m (Short WB)',
             color=['#00b4d8','#5e60ce','#f5a623','#e94560','#ff6b6b'],
             alpha=0.85, edgecolor='white', linewidth=0.8)
b2 = ax3.bar(x + w/2, vals12, w, label='L=12m (Long WB)',
             color=['#0077b6','#3a0ca3','#e76f51','#b5179e','#d62828'],
             alpha=0.85, edgecolor='white', linewidth=0.8)

for bar in b1:
    ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
             f'{bar.get_height():.1f}', ha='center', va='bottom',
             color='white', fontsize=8, fontweight='bold')
for bar in b2:
    ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
             f'{bar.get_height():.1f}', ha='center', va='bottom',
             color='white', fontsize=8, fontweight='bold')

ax3.set_xticks(x)
ax3.set_xticklabels(labels, color=COLORS['text'], fontsize=10)
ax3.set_ylabel('Radius / Distance  (meters)', color=COLORS['subtitle'])
ax3.set_title('Side-by-Side Comparison: All Radii  (δ = 30°)',
              color='white', fontsize=12, fontweight='bold')
ax3.legend(facecolor='#0d0d1a', edgecolor='#444466',
           labelcolor=COLORS['text'], fontsize=9)
ax3.grid(axis='y', color=COLORS['grid'], alpha=0.5)
ax3.tick_params(colors=COLORS['subtitle'])
for spine in ax3.spines.values():
    spine.set_edgecolor(COLORS['grid'])

# ── Panel 4: Off-tracking vs Wheelbase curve ─────────────────────────────────
ax4 = fig.add_subplot(gs[1, 1])
ax4.set_facecolor(COLORS['panel'])

L_vals = np.linspace(2, 16, 300)
T = 2
deltas = [20, 30, 40]
palette = ['#00b4d8', '#f5a623', '#e94560']

for d, col in zip(deltas, palette):
    delta = np.radians(d)
    R_vals = L_vals / np.tan(delta)
    off = R_vals - np.sqrt(np.maximum(R_vals**2 - L_vals**2, 0))
    ax4.plot(L_vals, off, color=col, lw=2.5, label=f'δ = {d}°')

# Highlight the two example trucks
for L_ex, lab, mk in [(6, 'Short WB\n(6m)', 'o'), (12, 'Long WB\n(12m)', 's')]:
    for d, col in zip(deltas, palette):
        delta = np.radians(d)
        R_ex = L_ex / np.tan(delta)
        ot = R_ex - np.sqrt(max(R_ex**2 - L_ex**2, 0))
        ax4.plot(L_ex, ot, mk, color=col, markersize=9,
                 markeredgecolor='white', markeredgewidth=1.2, zorder=8)

ax4.axvline(6,  color='#90e0ef', linestyle='--', alpha=0.6, lw=1.5)
ax4.axvline(12, color='#48cae4', linestyle='--', alpha=0.6, lw=1.5)
ax4.text(6.15,  ax4.get_ylim()[1] if ax4.get_ylim()[1] > 0 else 8,
         'Short WB', color='#90e0ef', fontsize=8, va='top')
ax4.text(12.15, 1, 'Long WB', color='#48cae4', fontsize=8, va='bottom')

ax4.set_xlabel('Wheelbase L (meters)', color=COLORS['subtitle'])
ax4.set_ylabel('Off-Tracking Distance (meters)', color=COLORS['subtitle'])
ax4.set_title('Off-Tracking vs Wheelbase\n(Rear wheel cuts inward by this much)',
              color='white', fontsize=12, fontweight='bold')
ax4.legend(facecolor='#0d0d1a', edgecolor='#444466',
           labelcolor=COLORS['text'], fontsize=9)
ax4.grid(color=COLORS['grid'], alpha=0.5)
ax4.tick_params(colors=COLORS['subtitle'])
for spine in ax4.spines.values():
    spine.set_edgecolor(COLORS['grid'])

# ── Main title ────────────────────────────────────────────────────────────────
fig.suptitle('Turning Radius: Front vs Rear Wheels — Long Wheelbase Trucks',
             color='white', fontsize=17, fontweight='bold', y=0.98)

plt.savefig('/home/user/Sri/turning_radius.png',
            dpi=150, bbox_inches='tight',
            facecolor=COLORS['bg'], edgecolor='none')
print("Saved.")
