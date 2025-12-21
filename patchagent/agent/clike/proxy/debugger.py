import os
import shlex
import pexpect
from typing import List, Optional, Tuple

from patchagent.logger import logger


class DebuggerSession:
    def __init__(self, project_path: str):
        self.project_path = project_path
        self.child: Optional[pexpect.spawn] = None
        self.debugger: Optional[str] = None
        self.prompt_pattern: Optional[str] = None

    def _detect_available_debugger(self) -> Optional[str]:
        import shutil
        if shutil.which("gdb"):
            return "gdb"
        if shutil.which("lldb"):
            return "lldb"
        return None

    def start(self, program_path: str, program_args: List[str], debugger: Optional[str] = None) -> str:
        if self.child is not None and self.child.isalive():
            self.stop()

        if debugger is None:
            debugger = self._detect_available_debugger()
            if debugger is None:
                return "No debugger available. Please install GDB or LLDB."
        
        self.debugger = debugger.lower()
        if self.debugger not in ['gdb', 'lldb']:
            return f"Unknown debugger: {self.debugger}. Supported: gdb, lldb"

        if os.path.isabs(program_path):
            abs_program_path = program_path
        else:
            abs_program_path = os.path.join(self.project_path, program_path)

        if self.debugger == 'gdb':
            debugger_cmd_parts = [
                "gdb", "-q",
                "-ex", "set confirm off",
                "-ex", "set style enabled off",
                # Disable LSAN to prevent conflicts with ptrace
                # Enable abort_on_error to make GDB stop at the error site instead of the program exiting
                "-ex", "set env ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:symbolize=1",
                abs_program_path
            ]
            self.prompt_pattern = r"\(gdb\) "
        else:  # lldb
            debugger_cmd_parts = [
                "lldb", "-X",
                "-o", "settings set auto-confirm true",
                "-o", "settings set target.env-vars ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:symbolize=1",
                abs_program_path
            ]
            self.prompt_pattern = r"\(lldb\) "

        debugger_command = " ".join(shlex.quote(part) for part in debugger_cmd_parts)
        
        try:
            # Use latin-1 encoding which can handle any byte sequence
            self.child = pexpect.spawn(debugger_command, cwd=self.project_path, timeout=30, encoding='latin-1')
            
            self.child.expect(self.prompt_pattern)
            initial_messages = self.child.before.strip() if self.child.before else ""
            
            # If args are provided, we might want to set them now or rely on 'run <args>' later.
            # The original snippet handled 'run' specially.
            # But GDB/LLDB can set args before run.
            if program_args:
                if self.debugger == 'gdb':
                    self.run_command(f"set args {' '.join(program_args)}")
                else:
                    self.run_command(f"settings set target.run-args {' '.join(program_args)}")

            return f"Debugger started successfully.\n{initial_messages}"
        except (pexpect.TIMEOUT, pexpect.EOF) as e:
            self.stop()
            return f"Failed to start local {self.debugger.upper()}: {e}"

    def run_command(self, command: str, timeout: int = 30) -> str:
        if self.child is None or not self.child.isalive():
            return "Debugger is not running. Please start it first."

        clean_cmd = command.strip()
        if not clean_cmd:
            return ""

        # Handle quit
        if clean_cmd.lower() in ("q", "quit", "exit"):
            self.stop()
            return "Debugger session ended."

        try:
            self.child.sendline(clean_cmd)
            self.child.expect(self.prompt_pattern, timeout=timeout)
            output = self.child.before.strip()
            # Remove the command echo if present (pexpect usually captures it in 'before')
            # It depends on terminal settings, but usually the first line is the command.
            lines = output.splitlines()
            if lines and clean_cmd in lines[0]:
                output = "\n".join(lines[1:]).strip()
            
            return output
        except pexpect.TIMEOUT:
            return f"Command '{clean_cmd}' timed out."
        except pexpect.EOF:
            self.stop()
            return f"Debugger session ended unexpectedly (EOF)."

    def stop(self):
        if self.child is not None:
            if self.child.isalive():
                try:
                    self.child.sendline("quit")
                    self.child.close(force=True)
                except Exception:
                    pass
            self.child = None

    def set_source_map(self, remote_path: str, develop_path: str) -> str:
        """
        Map source paths from the OSS-Fuzz environment to the Develop (Agent) environment.
        """
        if not self.child or not self.child.isalive():
            return "Debugger not running."

        logger.info(f"[🐞] Setting source map: {remote_path} -> {develop_path}")

        if self.debugger == 'gdb':
            # GDB uses set substitute-path
            return self.run_command(f"set substitute-path {remote_path} {develop_path}")
        else:
            # LLDB uses settings set target.source-map
            return self.run_command(f"settings set target.source-map {remote_path} {develop_path}")
