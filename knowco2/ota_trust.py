"""No OTA key is trusted by a normal source checkout.

Provision through the physical service-volume / local flasher path. The
security-test builder replaces this file ONLY inside its output directory.
Never copy a public test-fixture key into a production build.
"""

PUBLIC_KEY_N_HEX = ""
KEY_ID = "unprovisioned"
TEST_ONLY = False
MIN_SEQUENCE = 0

BUILD_ID = ""
LIBRARY_SET_ID = ""
MPY_ABI = ""
