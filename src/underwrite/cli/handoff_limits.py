"""Resource policy for the composed handoff command, derived from record capacity."""

from underwrite.service.records import RECORD_MAX_FILES

PROFILE = "goppi.p7_regrade_consumer_handoff.v1"
GENERATED_NAMES = (
    "underwrite/context.json",
    "underwrite/evidence.json",
    "underwrite/review.json",
    "underwrite/findings.json",
    "underwrite/invocation.json",
    "underwrite/summary.json",
)
MAX_SOURCE_FILES = RECORD_MAX_FILES - len(GENERATED_NAMES)
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
