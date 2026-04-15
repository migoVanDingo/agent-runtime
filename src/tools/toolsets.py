from tools.toolset import Toolset
from tools.implementations.read_file import ReadFileTool
from tools.implementations.write_file import WriteFileTool
from tools.implementations.list_files import ListFilesTool
from tools.implementations.walk_directory import WalkDirectoryTool
from tools.implementations.copy_file import CopyFileTool
from tools.implementations.move_file import MoveFileTool
from tools.implementations.delete_file import DeleteFileTool
from tools.implementations.make_directory import MakeDirectoryTool
from tools.implementations.read_file_lines import ReadFileLinesTool
from tools.implementations.get_working_directory import GetWorkingDirectoryTool
from tools.implementations.environment_info import EnvironmentInfoTool
from tools.implementations.download_file import DownloadFileTool
from tools.implementations.bash_exec import BashExecTool
from tools.implementations.search_files import SearchFilesTool
from tools.implementations.strings_tool import StringsTool
from tools.implementations.objdump_tool import ObjdumpTool
from tools.implementations.file_info import FileInfoTool
from tools.implementations.hexdump_tool import HexdumpTool
from tools.implementations.nm_tool import NmTool
from tools.implementations.ltrace_tool import LtraceTool
from tools.implementations.strace_tool import StraceTool
from tools.implementations.readelf_tool import ReadElfTool
from tools.implementations.checksec_tool import ChecksecTool
from tools.implementations.grep_binary import GrepBinaryTool
from tools.implementations.hash_file import HashFileTool
from tools.implementations.base64_tool import Base64EncodeTool, Base64DecodeTool
from tools.implementations.xor_decode import XorDecodeTool
from shared_types import RoutingRule
from routing.conditions import has_file_path, has_extension, any_keyword, last_tools_were, all_of


FILE_IO = Toolset(
    name="file_io",
    description="File system read/write/navigation tools",
    tools=[
        ReadFileTool(),
        WriteFileTool(),
        ListFilesTool(),
        WalkDirectoryTool(),
        CopyFileTool(),
        MoveFileTool(),
        DeleteFileTool(),
        MakeDirectoryTool(),
        ReadFileLinesTool(),
        GetWorkingDirectoryTool(),
        EnvironmentInfoTool(),
        DownloadFileTool(),
    ],
    rules=[
        RoutingRule(toolset="file_io", condition=has_file_path()),
        RoutingRule(toolset="file_io", condition=has_extension(
            ".py", ".txt", ".md", ".json", ".yaml", ".yml",
            ".csv", ".log", ".sh", ".c", ".cpp", ".h", ".rs", ".go", ".js", ".ts",
        )),
        RoutingRule(toolset="file_io", condition=any_keyword(
            "read", "write", "file", "directory", "folder", "list", "ls",
            "copy", "move", "delete", "rename", "mkdir", "download", "save",
            "open", "create", "path", "cwd", "pwd",
        )),
    ],
)

SHELL = Toolset(
    name="shell",
    description="Shell command execution and file search",
    tools=[
        BashExecTool(),
        SearchFilesTool(),
    ],
    rules=[
        RoutingRule(toolset="shell", condition=any_keyword(
            "run", "execute", "bash", "shell", "command", "script",
            "terminal", "process", "grep", "find", "search", "pipe",
            "install", "build", "compile", "make",
        )),
    ],
)

ANALYSIS = Toolset(
    name="analysis",
    description="Binary analysis and reverse engineering tools",
    tools=[
        StringsTool(),
        ObjdumpTool(),
        FileInfoTool(),
        HexdumpTool(),
        NmTool(),
        LtraceTool(),
        StraceTool(),
        ReadElfTool(),
        ChecksecTool(),
        GrepBinaryTool(),
    ],
    rules=[
        RoutingRule(toolset="analysis", condition=has_extension(
            ".o", ".so", ".a", ".dylib", ".elf", ".bin", ".exe", ".out",
        )),
        RoutingRule(toolset="analysis", condition=any_keyword(
            "binary", "disassemble", "disassembly", "objdump", "strings",
            "hexdump", "hex", "symbols", "nm", "readelf", "elf",
            "ltrace", "strace", "checksec", "reverse", "malware",
            "executable", "segment", "section", "header",
        )),
        RoutingRule(toolset="analysis", condition=last_tools_were(
            "strings", "objdump", "hexdump", "nm", "readelf",
            "ltrace", "strace", "checksec", "grep_binary", "file_info",
        )),
    ],
)

CRYPTO = Toolset(
    name="crypto",
    description="Hashing, encoding, and basic cryptanalysis tools",
    tools=[
        HashFileTool(),
        Base64EncodeTool(),
        Base64DecodeTool(),
        XorDecodeTool(),
    ],
    rules=[
        RoutingRule(toolset="crypto", condition=any_keyword(
            "hash", "md5", "sha", "sha256", "checksum",
            "base64", "encode", "decode", "xor", "encrypt", "decrypt",
            "hex", "bytes", "cipher", "crypto", "cryptography",
        )),
        RoutingRule(toolset="crypto", condition=last_tools_were(
            "hash_file", "base64_encode", "base64_decode", "xor_decode",
        )),
    ],
)


ALL_TOOLSETS = [FILE_IO, SHELL, ANALYSIS, CRYPTO]
