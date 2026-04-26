"""Workflow registry.

Imports all workflow implementations and defines ALL_WORKFLOWS in priority order.
To add a new workflow: create a file in workflows/implementations/, define your
class there, import it here, and add an instance to ALL_WORKFLOWS.
"""

from workflows.base import Workflow
from workflows.implementations.deep_disassembly import DeepDisassembly
from workflows.implementations.analyze_and_write import AnalyzeAndWrite
from workflows.implementations.read_modify_write import ReadModifyWrite
from workflows.implementations.hash_and_report import HashAndReport

# Priority order — first match wins.
# More specific workflows must come before broader ones.
ALL_WORKFLOWS: list[Workflow] = [
    DeepDisassembly(),      # before AnalyzeAndWrite — more specific
    AnalyzeAndWrite(),
    ReadModifyWrite(),
    HashAndReport(),
]
