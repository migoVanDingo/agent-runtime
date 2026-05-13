"""FuzzTargetTool — auto-generate test cases and diff oracle vs candidate."""
from __future__ import annotations

import json

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.container.diff_behavior import DiffBehaviorTool


class FuzzTargetTool(BaseTool):
    name = "fuzz_target"
    description = (
        "Automatically generate test cases and diff oracle vs candidate behavior. "
        "strategy='boundary' generates inputs at block-size boundaries (1,7,8,9,15,16,17 bytes). "
        "strategy='random' generates random printable strings. "
        "strategy='mutation' mutates provided seed_cases. "
        "Returns the same DiffReport as diff_behavior plus the generated test cases."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "oracle_path": ToolProperty(type="string", description="Path to the original binary"),
                "oracle_type": ToolProperty(type="string", description="Oracle type: native_binary"),
                "candidate_path": ToolProperty(type="string", description="Path to candidate binary or source"),
                "candidate_type": ToolProperty(type="string", description="Candidate type"),
                "strategy": ToolProperty(
                    type="string",
                    description="Case generation strategy: boundary | random | mutation",
                ),
                "arg_template": ToolProperty(
                    type="array",
                    description='Arg template with {data} and {passphrase} placeholders. E.g. ["-e", "{passphrase}", "{data}"]',
                    items={"type": "string"},
                ),
                "n_cases": ToolProperty(type="integer", description="Number of cases to generate (default 20)"),
                "seed_cases": ToolProperty(type="array", description="Seed cases for mutation strategy", items={"type": "object"}),
                "block_size": ToolProperty(type="integer", description="Block size for boundary strategy (default 8)"),
                "candidate_build_flags": ToolProperty(type="array", description="Compiler flags for candidate", items={"type": "string"}),
                "timeout_seconds": ToolProperty(type="number", description="Container timeout (default 120)"),
            },
            required=["oracle_path", "oracle_type", "candidate_path", "candidate_type", "strategy", "arg_template"],
        )

    def execute(self, tool_input: dict) -> str:
        import random
        import string

        strategy = tool_input.get("strategy", "boundary")
        arg_template = tool_input.get("arg_template", [])
        n_cases = int(tool_input.get("n_cases") or 20)
        block_size = int(tool_input.get("block_size") or 8)
        seed_cases = tool_input.get("seed_cases") or []

        passphrase = "testpass"

        def make_case(case_id: str, data: str) -> dict:
            args = [
                a.replace("{data}", data).replace("{passphrase}", passphrase)
                for a in arg_template
            ]
            return {"id": case_id, "args": args}

        generated: list[dict] = []

        if strategy == "boundary":
            for size in [1, block_size - 1, block_size, block_size + 1,
                         2 * block_size - 1, 2 * block_size, 2 * block_size + 1]:
                if size <= 0:
                    continue
                data = ("A" * size)[:size]
                generated.append(make_case(f"boundary_{size}b", data))
                if len(generated) >= n_cases:
                    break

        elif strategy == "random":
            chars = string.ascii_letters + string.digits
            for i in range(n_cases):
                size = random.randint(1, 64)
                data = "".join(random.choices(chars, k=size))
                generated.append(make_case(f"rand_{i}", data))

        elif strategy == "mutation":
            base_cases = seed_cases or [{"id": "seed", "args": arg_template}]
            for i, seed in enumerate(base_cases[:n_cases]):
                args = seed.get("args", [])
                mutated = list(args)
                if mutated:
                    idx = random.randint(0, len(mutated) - 1)
                    mutated[idx] = mutated[idx] + random.choice(string.printable[:32])
                generated.append({"id": f"mut_{i}", "args": mutated})

        # Delegate to diff_behavior
        diff_input = {
            "oracle_path": tool_input["oracle_path"],
            "oracle_type": tool_input["oracle_type"],
            "candidate_path": tool_input["candidate_path"],
            "candidate_type": tool_input["candidate_type"],
            "test_cases": generated,
            "candidate_build_flags": tool_input.get("candidate_build_flags"),
            "timeout_seconds": tool_input.get("timeout_seconds", 120),
        }
        diff_result = json.loads(DiffBehaviorTool().execute(diff_input))
        diff_result["generated_cases"] = generated
        return json.dumps(diff_result, indent=2)
