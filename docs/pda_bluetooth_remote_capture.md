# PDA Bluetooth Remote Capture Setup

This guide explains how to use a Bluetooth camera remote to trigger capture from
the live-camera web page in Chrome on an Android PDA.

## How It Works

The Bluetooth remote sends a `Volume Up` key event. Chrome does not normally
interpret that event as a web-camera command, so Key Mapper converts it into a
tap on the capture button:

```text
Bluetooth remote press
    -> PDA receives Volume Up
    -> Key Mapper taps the web capture button
    -> The web page captures and uploads the image
```

This setup only triggers the existing capture action. It does not change image
processing, grid generation, or counting behavior.

## Requirements

- Android 10 PDA
- Google Chrome
- Bluetooth camera remote that sends `Volume Up`
- Key Mapper
- A working live-camera page

## Install Key Mapper

Download a stable APK from the official Key Mapper GitHub releases:

<https://github.com/keymapperorg/KeyMapper/releases>

Do not download the source-code archives or APKs from untrusted mirrors.

If the PDA cannot download the APK directly:

1. Download the APK on a computer.
2. Connect the PDA over USB in file-transfer mode.
3. Copy the APK into the PDA's `Download` folder.
4. Open it with the PDA's Files app and complete the installation.

Android may ask for permission to install unknown apps. Enable that permission
temporarily for the Files app, then disable it after installation.

## Create the Key Map

1. Pair the Bluetooth remote with the PDA.
2. Open Key Mapper.
3. Enable the Key Mapper accessibility service when prompted.
4. Create a new key map.
5. Record a trigger and press the Bluetooth remote once.
6. Confirm that the trigger is shown as `Volume Up`.
7. Select the short-press trigger type.
8. Add the `Tap screen` action.

If Key Mapper can distinguish the input device, restrict the trigger to the
Bluetooth remote. Otherwise, the PDA's physical volume-up button may also
trigger capture.

## Configure the Tap Position

1. Open the live-camera page in Chrome.
2. Set the screen orientation, browser zoom, and scroll position that will be
   used during operation.
3. Make sure the capture button is fully visible.
4. Take a screenshot with `Power + Volume Down`.
5. Return to the `Tap screen` action in Key Mapper.
6. Choose the screenshot.
7. Tap the center of the capture button in the screenshot.
8. Confirm that the `X` and `Y` coordinates are populated.
9. Save and enable the key map.

## Restrict the Map to Chrome

Add a condition equivalent to:

```text
Foreground application = Chrome
```

This reduces accidental taps when another application is open.

## Operation

1. Secure the PDA on the stand.
2. Connect the Bluetooth remote.
3. Open the live-camera page in Chrome.
4. Keep the same orientation, zoom, and scroll position used during setup.
5. Confirm that the Key Mapper mapping is enabled.
6. Press the remote once and confirm that exactly one image is captured.
7. Continue with remote capture after the single-capture test succeeds.

## Troubleshooting

### The remote only changes the volume

- Confirm that the key map is enabled.
- Confirm that the Key Mapper accessibility service is still enabled.
- Confirm that the remote is connected.
- Temporarily remove the Chrome foreground condition to test the mapping.

### The remote taps the wrong location

- Restore the original screen orientation.
- Restore the original Chrome zoom and scroll position.
- Take a new screenshot and configure the coordinates again.
- Keep the capture button in a fixed position during operation.

### Action entries do not open in Key Mapper

- Force stop and reopen Key Mapper.
- Clear the Key Mapper cache.
- Restart the PDA.
- Check the Key Mapper accessibility permission.

### The PDA volume button also triggers capture

If the trigger is configured for any device, the PDA's physical volume-up
button may trigger the same action. Restrict the trigger to the Bluetooth remote
when device selection is available.

## Configuration Storage

The mapping is stored locally by Key Mapper and is not part of this Git
repository:

- A replacement PDA must be configured again, unless the mapping is exported
  and restored with Key Mapper.
- Uninstalling Key Mapper or clearing its application data may remove the map.
- This document records the procedure, not the device-specific screen
  coordinates.
