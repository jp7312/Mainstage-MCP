#include <CoreMIDI/CoreMIDI.h>
#include <assert.h>
#include <stdio.h>
static MIDIEndpointRef received_source;
static const MIDIPacketList *received_packets;
static OSStatus receive_for_test(MIDIEndpointRef source, const MIDIPacketList *packets) {
    received_source = source;
    received_packets = packets;
    return 42;
}
#define MIDIReceived receive_for_test
#include "driver.c"

int main(void) {
    /* No CoreMIDI client, driver registration, or setup mutation in this test. */
    Driver driver = {.interface = &interface};
    atomic_init(&driver.references, 1);
    atomic_init(&driver.sources[0], 101);
    atomic_init(&driver.sources[1], 202);
    MIDIDriverRef ref = (MIDIDriverRef)&driver;
    MIDIPacketList packets = {0};
    void *queried = NULL;
    assert(query(&driver, CFUUIDGetUUIDBytes(kMIDIDriverInterface2ID), &queried) == S_OK);
    assert(queried == &driver && release(&driver) == 1);
    assert(query(&driver, CFUUIDGetUUIDBytes(kMIDIDriverInterface3ID), &queried) == E_NOINTERFACE);
    assert(queried == NULL);
    assert(send_packets(ref, &packets, (void *)1, NULL) == 42);
    assert(received_source == 101 && received_packets == &packets);
    assert(send_packets(ref, &packets, (void *)2, NULL) == 42);
    assert(received_source == 202);
    assert(send_packets(ref, &packets, (void *)3, NULL) == -50);
    assert(send_packets(ref, NULL, (void *)1, NULL) == -50);
    assert(stop(ref) == noErr);
    assert(send_packets(ref, &packets, (void *)1, NULL) == kMIDIObjectNotFound);
    assert(send_packets(ref, &packets, (void *)2, NULL) == kMIDIObjectNotFound);
    puts("driver ABI/routing self-check passed (no MIDI setup changes)");
}
