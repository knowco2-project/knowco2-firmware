# Public, insecure OTA test fixture

`ota_test_private.pem` is deliberately published RSA test data. It is **not a
production secret**, and anyone can sign packages with it. It is used only by
`tools/sign_ota.py`, host tests and explicitly marked isolated device-test
builds. Normal source checkouts trust no key. Never ship this key's public
half in a production firmware image. Do not use customer devices or connect
test-key devices to untrusted networks.

The firmware archive never includes this private fixture. Production signing
key generation, custody, access, rotation and provisioning are outside this
branch and require separate authorization and review.
