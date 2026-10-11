"""Independent resource policies for handoff analysis and output."""

from underwrite.instrument.evidence.goppi_p7_handoff import MAX_FILES

MAX_SOURCE_FILES = MAX_FILES

PROFILE = "goppi.p7_regrade_consumer_handoff.v1"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
