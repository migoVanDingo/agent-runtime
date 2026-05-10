from skills.base import Skill
from skills.implementations.analyze_and_write import AnalyzeAndWrite
from skills.implementations.audit_binary import AuditBinary
from skills.implementations.deep_disassembly import DeepDisassembly
from skills.implementations.dynamic_analysis import DynamicAnalysis
from skills.implementations.function_map import FunctionMap
from skills.implementations.hash_and_report import HashAndReport
from skills.implementations.quick_recon import QuickRecon
from skills.implementations.read_modify_write import ReadModifyWrite
from skills.implementations.solve_crackme import SolveCrackme
from skills.implementations.test_reconstruction import TestReconstruction

ALL_SKILLS: list[Skill] = [
    SolveCrackme(),
    AuditBinary(),
    TestReconstruction(),
    DynamicAnalysis(),
    DeepDisassembly(),
    FunctionMap(),
    QuickRecon(),
    AnalyzeAndWrite(),
    ReadModifyWrite(),
    HashAndReport(),
]
