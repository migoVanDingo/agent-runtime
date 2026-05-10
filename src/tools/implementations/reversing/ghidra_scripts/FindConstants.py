# Ghidra post-analysis script: find defined data, strings, and magic constants
# Args: <output_path>
import json

args = getScriptArgs()
output_path = args[0]

listing = currentProgram.getListing()
items = []

# Defined data (strings, scalars, pointers)
for d in listing.getDefinedData(True):
    dt_name = d.getDataType().getName()
    try:
        val = str(d.getValue())
    except Exception:
        val = None
    items.append({
        "address": str(d.getAddress()),
        "type": dt_name,
        "value": val,
        "label": str(d.getLabel()) if d.getLabel() else None,
    })

with open(output_path, "w") as f:
    json.dump(items, f)

print("FindConstants: wrote {} data items to {}".format(len(items), output_path))
