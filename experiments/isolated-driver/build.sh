#!/bin/sh
set -eu
cd "$(dirname "$0")"
bundle=build/MainStageMCP.plugin
sign_identity=${SIGN_IDENTITY:--}
mkdir -p "$bundle/Contents/MacOS"
cp Info.plist "$bundle/Contents/Info.plist"
xcrun clang -std=c11 -Wall -Wextra -Werror -fvisibility=hidden -O2 \
    -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 -bundle driver.c \
    -framework CoreFoundation -framework CoreMIDI -o "$bundle/Contents/MacOS/MainStageMCP"
codesign --force --sign "$sign_identity" "$bundle"
codesign --verify --strict "$bundle"
plutil -lint "$bundle/Contents/Info.plist"
xcrun clang -std=c11 -Wall -Wextra -Werror test_plugin.c \
    -framework CoreFoundation -framework CoreMIDI -o build/test_plugin
./build/test_plugin "$bundle"
xcrun clang -std=c11 -Wall -Wextra -Werror test_driver.c \
    -framework CoreFoundation -framework CoreMIDI -o build/test_driver
./build/test_driver
xcrun clang -std=c11 -Wall -Wextra -Werror remove_device.c \
    -framework CoreFoundation -framework CoreMIDI -o build/remove_device
xcrun clang -std=c11 -Wall -Wextra -Werror test_lifecycle.c \
    -framework CoreFoundation -framework CoreMIDI -o build/test_lifecycle
./build/test_lifecycle
