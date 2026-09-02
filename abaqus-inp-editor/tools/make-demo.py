#!/usr/bin/env python3
"""Generate the demo model shipped with the editor.

Writes samples/demo-bracket.inp and src/demo.js (the same text embedded as a
JS string so the "Load a demo model" button works with no file present).

Run from the project root:  python3 tools/make-demo.py
"""

NX, NY, NZ = 8, 4, 3
LX, LY, LZ = 80.0, 20.0, 15.0


def build():
    nodes, nid, n = [], {}, 0
    for k in range(NZ + 1):
        for j in range(NY + 1):
            for i in range(NX + 1):
                n += 1
                nid[(i, j, k)] = n
                nodes.append((n, i * LX / NX, j * LY / NY, k * LZ / NZ))

    elems, e = [], 0
    for k in range(NZ):
        for j in range(NY):
            for i in range(NX):
                e += 1
                elems.append((e, [
                    nid[(i, j, k)], nid[(i + 1, j, k)], nid[(i + 1, j + 1, k)], nid[(i, j + 1, k)],
                    nid[(i, j, k + 1)], nid[(i + 1, j, k + 1)], nid[(i + 1, j + 1, k + 1)],
                    nid[(i, j + 1, k + 1)],
                ]))

    fixed = sorted(nid[(0, j, k)] for j in range(NY + 1) for k in range(NZ + 1))
    tip = sorted(nid[(NX, j, k)] for j in range(NY + 1) for k in range(NZ + 1))
    top = [ee[0] for ee in elems][-NX * NY:]

    snodes, snid, m = [], {}, 0
    for j in range(3):
        for i in range(5):
            m += 1
            snid[(i, j)] = m
            snodes.append((m, i * 20.0, j * 10.0, 0.0))
    selems, se = [], 0
    for j in range(2):
        for i in range(4):
            se += 1
            selems.append((se, [snid[(i, j)], snid[(i + 1, j)],
                                snid[(i + 1, j + 1)], snid[(i, j + 1)]]))

    return nodes, elems, fixed, tip, top, snodes, selems


def setlines(ids, per=16):
    out = []
    for i in range(0, len(ids), per):
        chunk = ids[i:i + per]
        out.append(", ".join("%6d" % v for v in chunk) + ("," if i + per < len(ids) else ""))
    return out


def main():
    nodes, elems, fixed, tip, top, snodes, selems = build()
    L = []
    A = L.append

    A("*Heading")
    A("** Job name: Demo_Bracket  Model name: Demo")
    A("** A small demonstration model shipped with the INP editor.")
    A("*Preprint, echo=NO, model=NO, history=NO, contact=NO")
    A("**")
    A("** PARTS")
    A("**")
    A("*Part, name=Bracket")
    A("*Node")
    for (i, x, y, z) in nodes:
        A("%7d, %12.4f, %12.4f, %12.4f" % (i, x, y, z))
    A("*Element, type=C3D8R")
    for (i, c) in elems:
        A("%6d, " % i + ", ".join("%6d" % v for v in c))
    A("*Nset, nset=Set-All, generate")
    A("%7d, %7d, %7d" % (1, len(nodes), 1))
    A("*Elset, elset=Set-All, generate")
    A("%7d, %7d, %7d" % (1, len(elems), 1))
    A("*Nset, nset=Fixed_End")
    L.extend(setlines(fixed))
    A("*Nset, nset=Loaded_End")
    L.extend(setlines(tip))
    A("*Elset, elset=Top_Elements")
    L.extend(setlines(top))
    A("*Surface, type=ELEMENT, name=Top_Surface")
    A("Top_Elements, S6")
    A("** Section: Steel_Section")
    A("*Solid Section, elset=Set-All, material=Steel")
    A(",")
    A("*End Part")
    A("**  ")
    A("*Part, name=Cover_Plate")
    A("*Node")
    for (i, x, y, z) in snodes:
        A("%7d, %12.4f, %12.4f, %12.4f" % (i, x, y, z))
    A("*Element, type=S4R")
    for (i, c) in selems:
        A("%6d, " % i + ", ".join("%6d" % v for v in c))
    A("*Nset, nset=Plate_All, generate")
    A("%7d, %7d, %7d" % (1, len(snodes), 1))
    A("*Elset, elset=Plate_All, generate")
    A("%7d, %7d, %7d" % (1, len(selems), 1))
    A("** Section: Plate_Section")
    A("*Shell Section, elset=Plate_All, material=Aluminium")
    A("2., 5")
    A("*End Part")
    A("**")
    A("** ASSEMBLY")
    A("**")
    A("*Assembly, name=Assembly")
    A("**  ")
    A("*Instance, name=Bracket-1, part=Bracket")
    A("*End Instance")
    A("**  ")
    A("*Instance, name=Cover_Plate-1, part=Cover_Plate")
    A("          0.,          26.,           0.")
    A("          0.,          26.,           0.,          1.,          26.,           0.,         -12.")
    A("*End Instance")
    A("**  ")
    A("*Nset, nset=Fixed_End, instance=Bracket-1")
    L.extend(setlines(fixed))
    A("*Nset, nset=Loaded_End, instance=Bracket-1")
    L.extend(setlines(tip))
    A("*Elset, elset=Top_Elements, instance=Bracket-1")
    L.extend(setlines(top))
    A("*Surface, type=ELEMENT, name=Bracket_Top")
    A("Top_Elements, S6")
    A("*End Assembly")
    A("** ")
    A("** MATERIALS")
    A("** ")
    A("*Material, name=Steel")
    A("*Density")
    A("7.85e-09,")
    A("*Elastic")
    A("210000., 0.3")
    A("*Plastic")
    A(" 250., 0.")
    A(" 420., 0.15")
    A("*Material, name=Aluminium")
    A("*Density")
    A("2.7e-09,")
    A("*Elastic")
    A(" 70000., 0.33")
    A("** ")
    A("** BOUNDARY CONDITIONS")
    A("** ")
    A("** Name: BC_Encastre Type: Symmetry/Antisymmetry/Encastre")
    A("*Boundary")
    A("Fixed_End, ENCASTRE")
    A("** ----------------------------------------------------------------")
    A("** ")
    A("** STEP: Load_Step")
    A("** ")
    A("*Step, name=Load_Step, nlgeom=YES, inc=100")
    A("*Static")
    A("0.1, 1., 1e-05, 1.")
    A("** ")
    A("** LOADS")
    A("** ")
    A("** Name: Tip_Load   Type: Concentrated force")
    A("*Cload")
    A("Loaded_End, 2, -25.")
    A("** ")
    A("** OUTPUT REQUESTS")
    A("** ")
    A("*Restart, write, frequency=0")
    A("** ")
    A("** FIELD OUTPUT: F-Output-1")
    A("** ")
    A("*Output, field, variable=PRESELECT")
    A("** ")
    A("** HISTORY OUTPUT: H-Output-1")
    A("** ")
    A("*Output, history, variable=PRESELECT")
    A("*End Step")

    text = "\n".join(L) + "\n"
    with open("samples/demo-bracket.inp", "w") as fh:
        fh.write(text)

    esc = text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    with open("src/demo.js", "w") as fh:
        fh.write(
            "/*\n"
            " * demo.js -- a small model shipped with the editor so the interface can be\n"
            " * explored with no file at hand: a hex-meshed bracket, a rotated shell plate\n"
            " * instance, two materials, sets, a surface and one static step.\n"
            " *\n"
            " * Generated by tools/make-demo.py; edit that, not this.\n"
            " */\n"
            "window.DEMO_INP = `" + esc + "`;\n"
        )
    print("wrote samples/demo-bracket.inp (%d lines) and src/demo.js" % len(L))


if __name__ == "__main__":
    main()
