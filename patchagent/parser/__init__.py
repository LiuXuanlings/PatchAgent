from typing import Any, Dict, Optional
from patchagent.logger import logger 

from patchagent.parser.address import AddressSanitizerReport
from patchagent.parser.java_native import JavaNativeReport
from patchagent.parser.jazzer import JazzerReport
from patchagent.parser.leak import LeakAddressSanitizerReport
from patchagent.parser.libfuzzer import LibFuzzerReport
from patchagent.parser.memory import MemorySanitizerReport
from patchagent.parser.sanitizer import Sanitizer, SanitizerReport
from patchagent.parser.thread import ThreadSanitizerReport
from patchagent.parser.undefined import UndefinedBehaviorSanitizerReport


def parse_sanitizer_report(content: str, sanitizer: Sanitizer, *args: Any, **kwargs: Any) -> Optional[SanitizerReport]:
    __sanitizer_report_classes_map__: Dict[Sanitizer, type[SanitizerReport]] = {
        Sanitizer.AddressSanitizer: AddressSanitizerReport,
        Sanitizer.LeakAddressSanitizer: LeakAddressSanitizerReport,
        Sanitizer.UndefinedBehaviorSanitizer: UndefinedBehaviorSanitizerReport,
        Sanitizer.MemorySanitizer: MemorySanitizerReport,
        Sanitizer.JazzerSanitizer: JazzerReport,
        Sanitizer.JavaNativeSanitizer: JavaNativeReport,
        Sanitizer.LibFuzzer: LibFuzzerReport,
        Sanitizer.ThreadSanitizer: ThreadSanitizerReport,
    }
    if sanitizer not in __sanitizer_report_classes_map__:
        return None
    
    run_command = kwargs.get("run_command", "")

    # 净化前的日志 (RAW) 
    logger.info(f"\n{'='*20} RAW REPORT START ({sanitizer}) {'='*20}")
    logger.info(content)
    logger.info(f"{'='*20} RAW REPORT END {'='*20}\n")

    report = __sanitizer_report_classes_map__[sanitizer].parse(content, *args, **kwargs)

    # 净化后的日志 (PURIFIED)
    if report:
        # 检查对象是否有 purified_content 属性 (UnknownReport 可能没有)
        if run_command and hasattr(report, "purified_content"):
            report.purified_content = run_command + report.purified_content

        logger.info(f"\n{'='*20} PURIFIED REPORT START ({sanitizer}) {'='*20}")
        # 大部分 Report 类都有 purified_content 属性，Unknown 类型可能没有
        if hasattr(report, "purified_content"):
            logger.info(report.purified_content)
        else:
            logger.info(report.summary)
        logger.info(f"{'='*20} PURIFIED REPORT END {'='*20}\n")
    else:
        logger.warning(f"Failed to parse report for {sanitizer}")

    return report