#include <CoreFoundation/CFPlugIn.h>
#include <CoreFoundation/CFPlugInCOM.h>
#include <CoreMIDI/MIDIDriver.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>

#define FACTORY_ID CFUUIDGetConstantUUIDWithBytes(NULL, \
    0x61,0x28,0x23,0xDD,0xEE,0xF0,0x4D,0x1A,0x96,0xC6,0x27,0x52,0x38,0x58,0x87,0x34)

int main(int argc, char **argv) {
    assert(argc == 2);
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL,
        (const UInt8 *)argv[1], (CFIndex)strlen(argv[1]), true);
    assert(url);
    CFPlugInRef plugin = CFPlugInCreate(NULL, url);
    assert(plugin);

    CFArrayRef factories = CFPlugInFindFactoriesForPlugInTypeInPlugIn(kMIDIDriverTypeID, plugin);
    assert(factories && CFArrayGetCount(factories) == 1);
    assert(CFEqual(CFArrayGetValueAtIndex(factories, 0), FACTORY_ID));

    MIDIDriverRef driver = CFPlugInInstanceCreate(NULL, FACTORY_ID, kMIDIDriverTypeID);
    assert(driver);
    void *queried = NULL;
    assert((*driver)->QueryInterface(driver, CFUUIDGetUUIDBytes(kMIDIDriverInterface2ID), &queried) == S_OK);
    assert(queried == driver);
    assert((*driver)->Release(queried) == 1);
    assert((*driver)->Release(driver) == 0);

    CFRelease(factories);
    CFRelease(plugin);
    CFRelease(url);
    puts("bundle registration/factory/ABI self-check passed (no MIDI setup changes)");
}
