#!/usr/bin/env python2
"""
    @package
    Generate a unified BOM and XYRS CSV file.

    Invocation from Kicad:
    $  python2 "/path/to/kicad_unified_bom_xyrs.py" "%I"

    Look for output in the pcb output directory (same location as gerbers)

    Example fields:
         ['Reference',
          'Value',
          'Description',
          'Footprint',
          'PosX',
          'PosY',
          'Rotation',
          'Side',
          'MFR',
          'MPN',
          'Datasheet',
          ]
"""
#
# Generate a unified bill of materials that reads MFR and MPN fields from
# schematic components (via netlist) and puts them on the same line as XYRS
# data read from pcb file.
#
# How to use:
# 1. Place MFN (manufacturer) and MPN (manufacturer part number) in each and 
#    every schematic part.
# 2. Generate a netlist and layout board.
# 3. Add a BOM plugin specifying the path to this file
# 4. Automagically get back a unified BOM + XYRS

from __future__ import print_function

import csv
import re
import os
import string
import sys
import argparse

# Import the KiCad python helper module, if not in same directory
#sys.path.append('/usr/share/doc/kicad/scripts/bom-in-python')
import kicad_netlist_reader

# pcbnew currently forces this to use python2 :(
import pcbnew

#
# Footprint and reference patterns to prune from BOM + XYRS
#
prune = {
        'footprint':['.*NetTie.*'],
        'ref':['TP.*', 'MECH.*']
        }


# Module attribute definitions from `enum MODULE_ATTR_T`
# https://github.com/KiCad/kicad-source-mirror/blob/d6097cf1aa0adc00e56cd971e427a116c503fd89/pcbnew/class_module.h#L73-L80
#
class MODULE_ATTR_T:
    MOD_DEFAULT = 0
    MOD_CMS = 1
    MOD_VIRTUAL = 2

#
# Dictoinary to hold all the magic
#
db = {}

#
# Print warnings and errors to stderr
#
def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

parser = argparse.ArgumentParser(description='Generate unified XYRS files')
parser.add_argument('netlist', help='netlist generated by kicad')
parser.add_argument('--output-file', help='output filename')
parser.add_argument('--pcb-file', help='pcb filename')
parser.add_argument('--output-format', help='macrofab|pcbng')
parser.add_argument('--metric', action='store_true', help='Pass this flag if your kicad is configured in metric')

args = parser.parse_args()
netlist_filename = args.netlist
csv_filename = args.output_file
pcb_filename = args.pcb_file

# Generate an instance of a generic netlist, and load the netlist tree from
# the command line option. If the file doesn't exist, execution will stop
net = kicad_netlist_reader.netlist(netlist_filename)

components = net.getInterestingComponents()

compfields = net.gatherComponentFieldUnion(components)
partfields = net.gatherLibPartFieldUnion()

eprint("[Info] compfields: {}".format(compfields))
eprint("[Info] partfields: {}".format(partfields))

columnset = compfields | partfields     # union
columnset -= set(['Reference', 'Value', 'Description'])

# Ordered list with most important fields first
columns = ['Reference', 'Value', 'Description'] + sorted(columnset)

for c in components:
    ref = c.getRef()
   
    data = {
            'Reference':ref,
            'Value':c.getValue(),
            'Datasheet':c.getDatasheet(),
            'Description':c.getField('Description'),
            'Config':c.getField('Config'),
           }

    for field in columns[7:]:
        data[field] = c.getField(field)
    
    # There are footprints in the part (i.e. class) fields that are wrong or
    # blank when we only care about the footprints in the component (i.e.
    # object) fields.  Override here.
    data['Footprint'] = c.getFootprint()

    db[ref] = data
    
    #eprint('Added: {}'.format(data))

#
# Read and parse Kicad XYRS data from board file
#

proj_dir = os.path.dirname(os.path.abspath(netlist_filename))
proj_prefix = os.path.splitext(netlist_filename)[0]
proj = os.path.basename(proj_prefix)

if not pcb_filename:
    # Guess the pcb file name based on net list
    pcb_filename = os.path.join(proj_dir, proj+'.kicad_pcb')

board = pcbnew.LoadBoard(pcb_filename)
output_dir = board.GetPlotOptions().GetOutputDirectory()
if not csv_filename:
    csv_filename = os.path.join(proj_dir,output_dir, proj+'-bom-xyrs.csv')
eprint('[Info] Writing output to {}'.format(csv_filename))
for module in board.GetModules():
    # Only read modules marked as NORMAL+INSERT
    if (module.GetAttributes() != MODULE_ATTR_T.MOD_CMS):
        continue

    (pos_x, pos_y) = module.GetCenter()
    rot = module.GetOrientation()/10.0,
    
    # HACK: GetFootprintRect gives us the footprint size once rotated
    # Macrofab needs the dimension of the package "Measured By the Pad
    # Footprint", whatever that means.  
    # The closest I've been able to get this to work is to use the lowest
    # dimension for YSize. 
    # Even with that every now and then, parts will have the wrong orientation.
    # I believe this will not cause an issue with manufacturing.
    x_size = module.GetFootprintRect().GetHeight()
    y_size = module.GetFootprintRect().GetWidth()
    if x_size < y_size:
        tmp = y_size
        y_size = x_size
        x_size = tmp

    if args.output_format == 'macrofab':
        side = 2 if module.IsFlipped() else 1
    else:
        side = 'bottom' if module.IsFlipped() else 'top'

    fpid = module.GetFPID()
    fp = '{}:{}'.format(fpid.GetLibNickname(), fpid.GetFootprintName())

    scaling_factor = 1000000.0
    origin_offset = (0, 0)
    coord_polarity = (1, -1)

    # if your kicad settings are in metric, you need to convert to mils (at least for macrofab)
    if args.output_format == 'macrofab':
        origin_offset = (3937.1, -3937.1) # (100mm, -100mm)
        rot = int(rot[0])
        if args.metric:
            scaling_factor *= 0.0254

    data = {
            'Reference': module.GetReference(),
            'PosX': coord_polarity[0]*pos_x/scaling_factor - origin_offset[0],
            'PosY': coord_polarity[1]*pos_y/scaling_factor - origin_offset[1],
            'Rotation': rot,
            'Side': side,
            'Type': 1,   # SMT:1, THRU: 2, but that info is not available
            'XSize': x_size/scaling_factor,
            'YSize': y_size/scaling_factor,
            'Populate': 1,  # We are only reading NORMAL+INSERT modules
            'Footprint': fp,
            }

    ref = data['Reference']
    if ref not in db:
        eprint('[Warn] PCB Skipping "{}"'.format(ref))
        continue

    for key, value in data.items():
        if key in db[ref] and value != db[ref][key]:
            eprint('[Warn] PCB overriding {} {}, "{}" != "{}"'.format(ref, key, db[ref][key], value))
        db[ref][key] = value

    #eprint('Updated: {}'.format(db[ref]))


#
# Clean-up output header
#
pcbng_columns = ['Reference',
               'Value',
               'Description',
               'Footprint',
               'PosX',
               'PosY',
               'Rotation',
               'Side',
               'MFR',
               'MPN',
               'OctopartID',
               'Datasheet',
              ]

mcfab_columns = ['Reference',
               'PosX',
               'PosY',
               'Rotation',
               'Side',
               'Type',
               'XSize',
               'YSize',
               'Value',
               'Footprint',
               'Populate',
               'DISTPN2'
              ]

#
# Prune fields
#
ref_combined = "(" + ")|(".join(prune['ref']) + ")"
footprint_combined = "(" + ")|(".join(prune['footprint']) + ")"

for key,value in list(db.items()):
    # print("value:::: {}".format(value))
    if 'Config' in value and 'DNF' in str(value['Config']):
        del db[key]
    elif re.match(ref_combined, key):
        del db[key]
    elif 'Footprint' in value and re.match(footprint_combined, value['Footprint']):
        del db[key]

#
# Write clean output to csv based on key -> column dictionary
#
fout = sys.stdout if not csv_filename else open(csv_filename, 'w')
if args.output_format == 'macrofab':
    columns_out = mcfab_columns
    delimiter = "	"
else:
    columns_out = pcbng_columns
    delimiter = ","
out = csv.DictWriter(fout, delimiter=delimiter, fieldnames=columns_out, extrasaction='ignore')

out.writeheader()

for key, value in sorted(db.items()):
    # print("entry: {}:{}".format(key, value))
    if value['DISTPN2'] == "":
        if 'DISTPN' in value:
            value['DISTPN2'] = value['DISTPN']
        else:
            value['DISTPN2'] = value['MPN']
    out.writerow(value)
