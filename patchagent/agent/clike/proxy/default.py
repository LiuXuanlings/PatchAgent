import json
import re
from pathlib import Path
from typing import Any, List, Optional

from langchain_core.tools import StructuredTool

from patchagent.agent.base import AgentStopException, PatchFoundException
from patchagent.agent.clike.proxy import internal
from patchagent.agent.clike.proxy.debugger import DebuggerSession
from patchagent.agent.clike.prompt import (
    INITIAL_DEBUGGING_PROMPT,
    INITIAL_DEBUGGING_PROMPT_LLDB,
    ITERATIVE_DEBUGGING_PROMPT,
    ITERATIVE_DEBUGGING_PROMPT_LLDB,
    STACK_TRACE_SUMMARY_PROMPT,
    DEBUGGER_OUTPUT_SUMMARY_PROMPT,
)
from patchagent.logger import logger
from patchagent.task import PatchTask
from patchagent.parser.utils import guess_relpath


def create_viewcode_tool(task: PatchTask, auto_hint: bool = False) -> StructuredTool:
    def viewcode(path: str, start_line: int, end_line: int) -> str:
        """
        Returns the code snippet, the line number is attached to the head of each line.

        :param path: The path of the file.
        :param start_line: The start line of the code snippet.
        :param end_line: The end line of the code snippet.
        """

        logger.info(f"[📞] viewcode(path={path}, start_line={start_line}, end_line={end_line})")
        args, result = internal.viewcode(task, path, start_line, end_line, auto_hint=auto_hint)
        task.current_context.add_tool_call("viewcode", args, result)
        return result

    return StructuredTool.from_function(viewcode)


def create_locate_tool(task: PatchTask, auto_hint: bool = False) -> StructuredTool:
    def locate(symbol: str) -> str:
        """
        Returns the location of the symbol.

        :param symbol: The symbol to be located.
        """

        logger.info(f"[📞] locate(symbol={symbol})")
        args, result = internal.locate(task, symbol, auto_hint=auto_hint)
        task.current_context.add_tool_call("locate", args, result)
        return result

    return StructuredTool.from_function(locate)


def create_validate_tool(task: PatchTask, auto_hint: bool = False) -> StructuredTool:
    def validate(patch: str) -> str:
        """
        Returns the validation result of the patch. The patch should be a multi-hunk patch, here is a example:
        ```diff
        --- a/src/OT/Layout/GDEF/GDEF.hh
        +++ b/src/OT/Layout/GDEF/GDEF.hh
        @@ -869,7 +869,7 @@ struct GDEF
                return v;

            v = table->get_glyph_props (glyph);
        -      if (likely (table)) // Don't try setting if we are the null instance!
        +      if (likely (table.get_blob ())) // Don't try setting if we are the null instance!
            glyph_props_cache.set (glyph, v);

            return v;
        ```

        :param patch: The patch to be validated.
        """

        logger.info(f"[📞] validate(patch={patch})")
        try:
            args, result = internal.validate(task, patch, auto_hint=auto_hint)
        except PatchFoundException as e:
            task.current_context.add_tool_call("validate", {"patch": str(e)}, "patch found")
            raise
        except AgentStopException:
            task.current_context.add_tool_call("validate", {"patch": patch}, "agent stop")
            raise

        task.current_context.add_tool_call("validate", args, result)
        return result

    return StructuredTool.from_function(validate)


def create_debugger_tool(task: PatchTask, llm: Any) -> StructuredTool:
    def _parse_json_response(content: str) -> dict:
        match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            json_str = content
            
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse JSON from LLM response: {content}")
            return {"hypothesis": "Failed to parse strategy", "commands": [], "next_action": "quit"}

    def debugger(program: str, args: Optional[List[str]] = None, **kwargs) -> str:
        """
        Automatically diagnose the crash using GDB/LLDB.

        :param program: The path to the executable to debug.
        :param args: The arguments for the executable.
        """

        if args is None:
            args = kwargs.get("v__args") or kwargs.get("arguments") or kwargs.get("argv") or []
        args = args or []

        if kwargs:
            logger.warning(f"[⚠️] Debugger tool received unexpected args: {kwargs}. Mapped to args={args}")

        logger.info(f"[📞] debugger(program={program}, args={args})")

        # === 1. Path Resolution (Map OSS-Fuzz paths to Develop paths) ===
        try:
            debug_paths = task.builder.get_develop_debug_paths()
        except NotImplementedError:
            return f"Debugger is not supported for the current builder type: {type(task.builder).__name__}"
        
        # A. Resolve Binary Path (/out/...)
        # We map `/out` -> `[Workspace]/[Hash]/oss-fuzz/build/out/[ProjectName]`
        oss_fuzz_out_prefix, develop_out_root = debug_paths["out_root_map"]
        
        if program.startswith(oss_fuzz_out_prefix):
            # Example: /out/fuzzer -> .../build/out/project/fuzzer
            develop_program = program.replace(oss_fuzz_out_prefix, develop_out_root, 1)
        else:
            develop_program = program

        # B. Resolve PoC Args (/testcase)
        # We map `/testcase` -> `task.pocs[0].path` (e.g., /tmp/poc.bin)
        develop_args = [task.builder.resolve_poc_path(arg, task.pocs) for arg in args]

        # === 2. Start Session ===
        session = DebuggerSession(str(task.builder.source_path))
        start_msg = session.start(develop_program, develop_args)
        
        if "Failed" in start_msg or "No debugger" in start_msg:
            return start_msg

        # === 3. Apply Source Mapping ===
        # Map `/src/[project]` (OSS-Fuzz) -> `[Workspace]/[Hash]/[project]` (Develop)
        oss_fuzz_src, develop_src = debug_paths["source_map"]
        map_msg = session.set_source_map(oss_fuzz_src, develop_src)
        start_msg += f"\nSource Mapping: {map_msg}"

        # Context for Path Guessing
        develop_source_root_path = debug_paths["develop_source_path_obj"]

        debugger_type = session.debugger
        sanitizer_report = task.report.summary
        source_code_context = ""

        for tool_call in task.current_context.tool_calls:
            if tool_call["name"] == "viewcode":
                source_code_context += f"Code snippet from {tool_call['args']['path']}:\n{tool_call['result']}\n\n" 
    
        # 1. Initial Strategy
        if debugger_type == 'lldb':
            prompt = INITIAL_DEBUGGING_PROMPT_LLDB.format(sanitizer_report=sanitizer_report, source_code_context=source_code_context)
        else:
            prompt = INITIAL_DEBUGGING_PROMPT.format(sanitizer_report=sanitizer_report, source_code_context=source_code_context)
            
        response = llm.invoke(prompt)
        strategy = _parse_json_response(response.content)
        
        session_history = f"Initialization:\n{start_msg}\n"
        max_steps = 10
        step = 0

        # 定义路径修正函数
        def run_cmd_with_path_fix(raw_cmd):
            # 拦截 run 命令修复参数 
            # LLM 经常错误地执行 `run /out/binary /testcase`
            # 我们需要：1. 移除二进制路径参数 2. 将 /testcase 映射为真实 PoC 路径
            tokens = raw_cmd.strip().split()
            if tokens and tokens[0] in ["r", "run"]:
                new_args = []
                args_start_idx = 1
                
                # 检查第一个参数是否像二进制路径 (无论是 OSS-Fuzz 路径还是 Develop 路径)
                if len(tokens) > 1:
                    first_arg = tokens[1]
                    # 启发式检查: 完全匹配或文件名匹配
                    if (first_arg == program or 
                        first_arg == develop_program or 
                        first_arg.endswith(f"/{Path(program).name}")):
                        args_start_idx = 2
                        logger.info(f"[🐞] Auto-removed binary path from GDB run command: {first_arg}")

                # 处理剩余参数 (主要是 /testcase)
                for token in tokens[args_start_idx:]:
                    # 复用 builder 的解析逻辑: /testcase -> /tmp/poc.bin
                    new_args.append(task.builder.resolve_poc_path(token, task.pocs))
                
                # 重组命令
                raw_cmd = f"{tokens[0]} {' '.join(new_args)}"
            # Intercept any command containing "path:line" pattern (e.g. break foo.c:10, list bar.c:5)
            # and replace the path with the guessed relative path in develop environment.
            def replace_path(match):
                path_str = match.group(1)
                line_str = match.group(2)
                guessed = guess_relpath(develop_source_root_path, Path(path_str))
                if guessed:
                    logger.info(f"[🐞] Path corrected in command: {path_str} -> {guessed}")
                    return f"{guessed}:{line_str}"
                return match.group(0)

            final_cmd = re.sub(r"(\S+):(\d+)", replace_path, raw_cmd)
            output = session.run_command(final_cmd)
            return final_cmd, output
        
        while step < max_steps:
            step += 1
            commands = strategy.get("commands", [])
            next_action = strategy.get("next_action", "continue")
            
            # Execute commands
            for cmd in commands:
                final_cmd, output = run_cmd_with_path_fix(cmd)  # 使用修正函数
                session_history += f"(gdb) {final_cmd}\n{output}\n"
                print(f"Step {step}, Command: {final_cmd}\nOutput:\n{output}\n")  # Live log
            
            if next_action == "quit":
                break
                
            # Execute next action
            final_action, output = run_cmd_with_path_fix(next_action) # 使用修正函数
            session_history += f"(gdb) {final_action}\n{output}\n"
            print(f"Step {step}, Next Action: {final_action}\nOutput:\n{output}\n")  # Live log
            
            # Get next strategy
            if debugger_type == 'lldb':
                prompt = ITERATIVE_DEBUGGING_PROMPT_LLDB.format(
                    sanitizer_report=sanitizer_report,
                    gdb_session_history=session_history,
                    source_code_context=source_code_context
                )
            else:
                prompt = ITERATIVE_DEBUGGING_PROMPT.format(
                    sanitizer_report=sanitizer_report,
                    gdb_session_history=session_history,
                    source_code_context=source_code_context
                )
        
            response = llm.invoke(prompt)
            strategy = _parse_json_response(response.content)
            
        session.stop()
        
        # Summarize
        # First summarize stack trace
        prompt = STACK_TRACE_SUMMARY_PROMPT.format(stack_trace=sanitizer_report)
        response = llm.invoke(prompt)
        stack_trace_summary = response.content
        
        # Then summarize session
        prompt = DEBUGGER_OUTPUT_SUMMARY_PROMPT.format(
            stack_trace=stack_trace_summary,
            gdb_session=session_history
        )
        response = llm.invoke(prompt)
        summary = response.content
        
        task.current_context.add_tool_call("debugger", {"program": program, "args": args}, summary)
        return summary

    return StructuredTool.from_function(debugger)