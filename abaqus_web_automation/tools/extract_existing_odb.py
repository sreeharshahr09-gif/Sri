# Run with: abaqus python extract_existing_odb.py Job.odb [output_folder]
from odbAccess import openOdb
import csv, os, sys

if len(sys.argv) < 2:
    raise SystemExit('Usage: abaqus python extract_existing_odb.py Job.odb [output_folder]')

odb_path = os.path.abspath(sys.argv[1])
out_dir = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(os.path.dirname(odb_path), os.path.splitext(os.path.basename(odb_path))[0] + '_extracted')
if not os.path.isdir(out_dir):
    os.makedirs(out_dir)

odb = openOdb(odb_path, readOnly=True)

# Inventory of steps, frames, history regions and field variables.
with open(os.path.join(out_dir, 'ODB_Inventory.csv'), 'w') as f:
    w = csv.writer(f); w.writerow(['Step','Frames','LastStepTime','HistoryRegions','LastFrameFieldVariables'])
    for sn, st in odb.steps.items():
        last = st.frames[-1] if st.frames else None
        w.writerow([sn, len(st.frames), getattr(last, 'frameValue', ''), len(st.historyRegions), ';'.join(sorted(last.fieldOutputs.keys())) if last else ''])

# All history outputs.
with open(os.path.join(out_dir, 'History_All_Steps.csv'), 'w') as f:
    w=csv.writer(f);w.writerow(['Step','Region','Variable','StepTime','Value'])
    for sn, st in odb.steps.items():
        for rn, reg in st.historyRegions.items():
            for vn, ho in reg.historyOutputs.items():
                for t, val in ho.data:
                    w.writerow([sn,rn,vn,t,val])

rp_vars=['U1','U2','U3','UR1','UR2','UR3','RF1','RF2','RF3','RM1','RM2','RM3']
with open(os.path.join(out_dir, 'RP_History_Wide.csv'), 'w') as f:
    w=csv.writer(f);w.writerow(['Step','Region','StepTime']+rp_vars)
    for sn, st in odb.steps.items():
        for rn, reg in st.historyRegions.items():
            if not any(v in reg.historyOutputs for v in rp_vars):
                continue
            maps={};times=set()
            for v in rp_vars:
                if v in reg.historyOutputs:
                    maps[v]={float(t):val for t,val in reg.historyOutputs[v].data}; times.update(maps[v].keys())
            for t in sorted(times):
                w.writerow([sn,rn,t]+[maps.get(v,{}).get(t,'') for v in rp_vars])

energy_vars=['ALLAE','ALLCD','ALLDMD','ALLFD','ALLIE','ALLKE','ALLPD','ALLSE','ALLVD','ALLWK','ETOTAL']
with open(os.path.join(out_dir, 'Energy_History.csv'), 'w') as f:
    w=csv.writer(f);w.writerow(['Step','Region','Variable','StepTime','Value'])
    for sn, st in odb.steps.items():
        for rn, reg in st.historyRegions.items():
            for v in energy_vars:
                if v in reg.historyOutputs:
                    for t,val in reg.historyOutputs[v].data:
                        w.writerow([sn,rn,v,t,val])

odb.close()
print('Extraction complete:', out_dir)
