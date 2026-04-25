import os
import logging
from dataclasses import dataclass
from pathlib import Path

from vidsearch.config import SUPPORTED_EXTENSIONS, SKIPPED_EXTENSIONS

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    supported: list[Path]
    skipped_unsupported: list[Path]
    skipped_no_extension: list[Path]
    failed_stat: list[Path]

    @property
    def total_seen(self) -> int:
        return len(self.supported) + len(self.skipped_unsupported) + len(self.skipped_no_extension) + len(self.failed_stat)


def scan_corpus(root: str | Path) -> ScanResult:
    root = Path(root)
    supported = []
    skipped_unsupported = []
    skipped_no_extension = []
    failed_stat = []

    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for fname in filenames:
            fpath = Path(dirpath) / fname
            try:
                if not fpath.exists():
                    failed_stat.append(fpath)
                    continue
            except OSError:
                failed_stat.append(fpath)
                continue

            ext = fpath.suffix.lower()
            if not ext:
                skipped_no_extension.append(fpath)
                logger.debug("skipped (no extension): %s", fpath)
            elif ext in SUPPORTED_EXTENSIONS:
                supported.append(fpath)
            elif ext in SKIPPED_EXTENSIONS:
                skipped_unsupported.append(fpath)
                logger.debug("skipped (unsupported extension %s): %s", ext, fpath)
            else:
                skipped_unsupported.append(fpath)
                logger.debug("skipped (unknown extension %s): %s", ext, fpath)

    logger.info(
        "scan complete: %d supported, %d skipped (unsupported), %d skipped (no ext), %d failed",
        len(supported), len(skipped_unsupported), len(skipped_no_extension), len(failed_stat),
    )
    return ScanResult(
        supported=supported,
        skipped_unsupported=skipped_unsupported,
        skipped_no_extension=skipped_no_extension,
        failed_stat=failed_stat,
    )
