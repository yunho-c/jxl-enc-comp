from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    elapsed_seconds: float
    stdout: str
    stderr: str

    @property
    def command_text(self) -> str:
        return " ".join(shlex.quote(part) for part in self.command)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def tool_path(command: str) -> str | None:
    if "/" in command:
        return command if Path(command).exists() else None
    return shutil.which(command)


def encode(
    encoder: str,
    command: str,
    input_path: Path,
    output_path: Path,
    mode: str,
    effort: int,
    distance: float | None,
) -> CommandResult:
    if encoder == "libjxl":
        args = [command, str(input_path), str(output_path), "--quiet", "-e", str(effort)]
        if mode == "lossless":
            args.extend(["-d", "0.0"])
        else:
            args.extend(["-d", str(distance)])
    elif encoder == "jxl-encoder":
        args = [command, str(input_path), str(output_path), "-e", str(effort)]
        if mode == "lossless":
            args.append("--lossless")
        else:
            args.extend(["-d", str(distance)])
    else:
        raise ValueError(f"unknown encoder: {encoder}")
    return run_command(args)


def decode(djxl: str, input_path: Path, output_path: Path) -> CommandResult:
    return run_command([djxl, str(input_path), str(output_path)])


def run_command(args: list[str]) -> CommandResult:
    start = time.perf_counter()
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - start
    return CommandResult(
        command=args,
        returncode=completed.returncode,
        elapsed_seconds=elapsed,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )

